"""
Prune django-reversion rows without deleting unrelated object history.

This command deletes:
    * legacy Version rows for audit/snapshot tables that are no longer tracked
      by django-reversion
    * Version rows older than the configured retention window, except for the
      newest N Version rows for each object
    * Revision rows that become empty after Version rows are deleted

This command preserves:
    * Marking and Cover Version rows created by direct admin edits
    * the newest KEEP_PER_OBJECT Version rows for every object, even when those
      rows are older than RETENTION_DAYS
    * non-empty Revision rows that still contain at least one retained Version

The command deletes Version rows directly. Do not replace this with
django-reversion's deleterevisions command: deleterevisions deletes whole
Revision rows, and one Revision can contain multiple objects saved during the
same HTTP request.

Usage from repo root, with mysql.cnf and backend/.env present:
    ./woco prune_revisions --dry-run
    ./woco prune_revisions

Expected exit code: 0.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from reversion.models import Revision, Version

from common.models import (
    CoverRecycleBin,
    CoverVersion,
    MarkingRecycleBin,
    MarkingVersion,
    SubmissionTransaction,
)


EXCLUDED_MODELS = (
    SubmissionTransaction,
    MarkingVersion,
    CoverVersion,
    MarkingRecycleBin,
    CoverRecycleBin,
)


class Command(BaseCommand):
    help = (
        "Prune django-reversion Version rows according to retention policy, "
        "purge legacy versions for excluded audit tables, and delete only "
        "Revision rows that become empty."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "REVERSION_PRUNE_RETENTION_DAYS", 180),
            help="Delete version rows older than this many days.",
        )
        parser.add_argument(
            "--keep",
            type=int,
            default=getattr(settings, "REVERSION_PRUNE_KEEP_PER_OBJECT", 3),
            help="Always keep this many newest version rows per object.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted, then roll back.",
        )

    def handle(self, *args, **options):
        days = int(options["days"])
        keep = int(options["keep"])
        dry_run = bool(options["dry_run"])
        verbosity = int(options["verbosity"])

        if days < 0:
            raise CommandError("--days must be zero or greater.")
        if keep < 0:
            raise CommandError("--keep must be zero or greater.")

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN: no rows will be committed."))

        try:
            with transaction.atomic():
                legacy_deleted = self._purge_excluded_model_versions(verbosity)
                stale_deleted = self._prune_stale_versions(days, keep, verbosity)
                empty_revision_deleted = self._delete_empty_revisions()

                if dry_run:
                    transaction.set_rollback(True)
        except Exception:
            self.stdout.write(self.style.ERROR(
                "Revision prune aborted; all changes rolled back."
            ))
            raise

        self.stdout.write("")
        self.stdout.write(
            "legacy excluded-model versions deleted: "
            f"{legacy_deleted}"
        )
        self.stdout.write(f"stale retained-policy versions deleted: {stale_deleted}")
        self.stdout.write(f"empty revisions deleted: {empty_revision_deleted}")

        summary = (
            "Done. deleted "
            f"{legacy_deleted + stale_deleted} Version row(s) and "
            f"{empty_revision_deleted} empty Revision row(s)."
        )
        if dry_run:
            summary = "[DRY RUN] " + summary + " (rolled back)"
        self.stdout.write(self.style.SUCCESS(summary))

    def _purge_excluded_model_versions(self, verbosity):
        total = 0
        for model in EXCLUDED_MODELS:
            content_type = ContentType.objects.get_for_model(
                model,
                for_concrete_model=True,
            )
            deleted, _ = Version.objects.filter(content_type=content_type).delete()
            total += deleted
            if verbosity >= 1:
                self.stdout.write(
                    f"  legacy {model._meta.label:<32s} versions deleted={deleted:>6d}"
                )
        return total

    def _prune_stale_versions(self, days, keep, verbosity):
        cutoff = timezone.now() - timedelta(days=days)
        object_keys = list(
            Version.objects.order_by()
            .values_list("content_type_id", "db", "object_id")
            .distinct()
        )
        total = 0

        for content_type_id, db, object_id in object_keys:
            object_versions = Version.objects.filter(
                content_type_id=content_type_id,
                db=db,
                object_id=object_id,
            )
            keep_ids = list(
                object_versions.order_by("-revision__date_created", "-pk")
                .values_list("pk", flat=True)[:keep]
            )
            stale_versions = object_versions.filter(revision__date_created__lt=cutoff)
            if keep_ids:
                stale_versions = stale_versions.exclude(pk__in=keep_ids)
            deleted, _ = stale_versions.delete()
            total += deleted
            if verbosity >= 2 and deleted:
                self.stdout.write(
                    "  stale content_type_id="
                    f"{content_type_id} db={db} object_id={object_id} "
                    f"versions deleted={deleted}"
                )

        if verbosity >= 1:
            self.stdout.write(f"  stale versions deleted={total:>6d}")
        return total

    def _delete_empty_revisions(self):
        deleted, _ = Revision.objects.filter(version__isnull=True).delete()
        return deleted
