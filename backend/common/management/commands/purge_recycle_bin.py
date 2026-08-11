"""
Permanently delete catalog records currently hidden by recycle-bin sidecars.

This command deletes the binned Marking and Cover rows themselves. Deleting
only MarkingRecycleBin or CoverRecycleBin rows would restore those records, so
the command starts from sidecar IDs and then hard-deletes the catalog rows.

Usage from repo root, with mysql.cnf and backend/.env present:
    ./woco purge_recycle_bin --dry-run
    ./woco purge_recycle_bin --no-input

Expected exit code: 0.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import (
    Citation,
    Cover,
    CoverRecycleBin,
    DateSeen,
    Image,
    Marking,
    MarkingRecycleBin,
)


class Command(BaseCommand):
    help = (
        "Permanently delete all Marking and Cover records currently in the "
        "recycle bin. Polymorphic image, date, and citation rows are also "
        "deleted for those subjects."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted, then roll back.",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            dest="no_input",
            help="Do not prompt for confirmation (for use in scripts).",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        no_input = bool(options["no_input"])

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN: no rows will be committed."))
        elif not no_input:
            self.stdout.write(
                self.style.WARNING(
                    "This will permanently delete ALL markings and covers "
                    "currently in the recycle bin. This cannot be restored by "
                    "deleting recycle-bin sidecar rows."
                )
            )
            answer = input("Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                raise CommandError("Aborted; nothing was deleted.")

        marking_ids = list(
            MarkingRecycleBin.objects.order_by()
            .values_list("marking_id", flat=True)
        )
        cover_ids = list(
            CoverRecycleBin.objects.order_by()
            .values_list("cover_id", flat=True)
        )

        try:
            with transaction.atomic():
                polymorphic_total = self._delete_polymorphic_rows(
                    "MARKING",
                    marking_ids,
                )
                polymorphic_total += self._delete_polymorphic_rows(
                    "COVER",
                    cover_ids,
                )
                cover_total = self._delete_catalog_rows(
                    Cover,
                    CoverRecycleBin,
                    cover_ids,
                )
                marking_total = self._delete_catalog_rows(
                    Marking,
                    MarkingRecycleBin,
                    marking_ids,
                )

                if dry_run:
                    transaction.set_rollback(True)
        except Exception:
            self.stdout.write(
                self.style.ERROR(
                    "Recycle-bin purge aborted; all changes rolled back."
                )
            )
            raise

        total = polymorphic_total + cover_total + marking_total
        summary = f"Done. deleted {total} row(s), including ORM cascades."
        if dry_run:
            summary = "[DRY RUN] " + summary + " (rolled back)"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(summary))

    def _delete_polymorphic_rows(self, subject_type, subject_ids):
        total = 0
        for model in (Image, DateSeen, Citation):
            deleted, _ = model.objects.filter(
                subject_type=subject_type,
                subject_id__in=subject_ids,
            ).delete()
            total += deleted
            self.stdout.write(
                f"  {model._meta.db_table:<24s} "
                f"{subject_type:<7s} deleted={deleted:>6d}"
            )
        return total

    def _delete_catalog_rows(self, model, sidecar_model, object_ids):
        deleted, details = model.all_objects.filter(pk__in=object_ids).delete()
        model_deleted = details.get(model._meta.label, 0)
        sidecar_deleted = details.get(sidecar_model._meta.label, 0)
        self.stdout.write(
            f"  {model._meta.db_table:<24s} deleted={model_deleted:>6d}"
        )
        self.stdout.write(
            f"  {sidecar_model._meta.db_table:<24s} deleted={sidecar_deleted:>6d}"
        )
        cascaded = deleted - model_deleted - sidecar_deleted
        if cascaded:
            self.stdout.write(
                f"  {'ORM cascades':<24s} deleted={cascaded:>6d}"
            )
        return deleted
