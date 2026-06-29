"""
Delete superseded non-draft Contribution rows per contributor and target.

Usage from repo root, with mysql.cnf and backend/.env present:
    ./woco consolidate_superseded_contributions --dry-run
    ./woco consolidate_superseded_contributions --no-input

Expected exit code: 0.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.contribution_consolidation import (
    consolidate_superseded_contributions,
    contribution_target,
)
from common.models import Contribution, SubmissionTransaction


class Command(BaseCommand):
    help = (
        "Delete older non-draft Contribution rows for the same contributor and "
        "target Marking/Cover, preserving drafts and writing tombstone audit rows."
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
            help="Do not prompt for confirmation.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        no_input = bool(options["no_input"])

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN: no rows will be committed."))
        elif not no_input:
            self.stdout.write(
                self.style.WARNING(
                    "This will permanently delete older non-draft Contribution "
                    "rows that are superseded by a newer row for the same "
                    "contributor and target. Record history remains in "
                    "SubmissionTransactions."
                )
            )
            answer = input("Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                raise CommandError("Aborted; nothing was deleted.")

        groups = {}
        qs = (
            Contribution.objects.exclude(status=Contribution.STATUS_DRAFT)
            .select_related("contributor", "marking")
            .order_by("contributor_id", "modified_date", "pk")
        )
        for contribution in qs:
            target = contribution_target(contribution)
            if target is None:
                continue
            key = (contribution.contributor_id, target.kind, target.id)
            groups.setdefault(key, []).append(contribution)

        total = 0
        try:
            with transaction.atomic():
                for key, rows in sorted(groups.items()):
                    if len(rows) < 2:
                        continue
                    rows.sort(key=lambda row: (row.modified_date, row.pk))
                    keep = rows[-1]
                    target = contribution_target(keep)
                    deleted = consolidate_superseded_contributions(
                        current=keep,
                        target=target,
                        actor=None,
                        source=SubmissionTransaction.SOURCE_SYSTEM,
                    )
                    total += deleted
                    contributor_id, kind, target_id = key
                    self.stdout.write(
                        "  contributor={0} target={1}:{2} keep={3} deleted={4}".format(
                            contributor_id,
                            kind,
                            target_id,
                            keep.pk,
                            deleted,
                        )
                    )
                if dry_run:
                    transaction.set_rollback(True)
        except Exception:
            self.stdout.write(
                self.style.ERROR(
                    "Contribution consolidation aborted; all changes rolled back."
                )
            )
            raise

        summary = "Done. deleted {0} superseded Contribution row(s).".format(total)
        if dry_run:
            summary = "[DRY RUN] " + summary + " (rolled back)"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(summary))
