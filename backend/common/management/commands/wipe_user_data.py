"""
Wipe user-generated submission data (contributions, drafts, version history,
and recycle bins), leaving the 14 ASCC catalog tables, all auth Users, and the
editor curation config (Collections + assignments) untouched.

Intended workflow -- run this FIRST, then refresh the catalog:

    python manage.py wipe_user_data
    python manage.py import_ascc_bundle tools/wip/out --truncate

After both steps the system holds only freshly imported catalog entries, your
existing user accounts, and the editor assignments that route future
contributions. The importer's own --truncate handles the 14 catalog tables;
this command only clears the non-catalog submission rows that would otherwise
dangle once the catalog is re-imported.

What is WIPED (6 tables, all rows):
    SubmissionTransactions   submission / moderation audit log
    MarkingVersions          marking snapshot history
    CoverVersions            cover snapshot history
    marking_recycle_bin      soft-deleted markings
    cover_recycle_bin        soft-deleted covers
    Contributions            pending / draft / approved submissions

What is PRESERVED:
    * the 14 ASCC catalog tables (handled by import_ascc_bundle --truncate)
    * auth Users / Groups (use backup_auth / restore_auth to manage those)
    * Collections + CollectionAssignments -- editor curation config tied to the
      preserved Users. Each Collection wraps one Region (OneToOne); since the
      importer re-inserts Regions under their stable CSV-driven ids, the
      Collections (and the assignments pointing at them) stay consistent across
      a --truncate re-import.
    * FAQEntries (admin-authored public site content)

Usage:
    python manage.py wipe_user_data            # prompts for confirmation
    python manage.py wipe_user_data --no-input # skip the prompt (scripts)
    python manage.py wipe_user_data --dry-run  # report counts, change nothing
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from common.models import (
    Contribution,
    CoverRecycleBin,
    CoverVersion,
    MarkingRecycleBin,
    MarkingVersion,
    SubmissionTransaction,
)


# Delete order: referencing rows before referenced rows so the wipe is valid
# even on a backend without FOREIGN_KEY_CHECKS toggling (e.g. SQLite). On MySQL
# we additionally disable FOREIGN_KEY_CHECKS (mirroring import_ascc_bundle
# --truncate): Django implements on_delete in Python, not as DB-level cascades,
# so the FKs reaching into these tables (e.g. MarkingVersion.transaction ->
# SubmissionTransaction) would otherwise block a raw DELETE.
WIPE_ORDER = (
    SubmissionTransaction,
    MarkingVersion,
    CoverVersion,
    MarkingRecycleBin,
    CoverRecycleBin,
    Contribution,
)


class Command(BaseCommand):
    help = (
        "Delete user-generated submission data (contributions, drafts, "
        "versions, recycle bins). Leaves the 14 catalog tables, auth Users, "
        "and editor Collection assignments untouched. Run "
        "before import_ascc_bundle --truncate for a fresh, catalog-only system."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Count what would be deleted from each table, then roll back. "
                "No rows are committed."
            ),
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
            self.stdout.write(self.style.WARNING(
                "This will permanently delete ALL contributions, drafts, version "
                "history, and recycle bins. Catalog tables, user accounts, and "
                "editor Collection assignments are NOT touched."
            ))
            answer = input("Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                raise CommandError("Aborted; nothing was deleted.")

        is_mysql = connection.vendor == "mysql"
        total = 0

        # Single outer transaction: any uncaught exception rolls back the whole
        # wipe, so the DB is never left half-cleared. --dry-run rolls back at
        # the end of a successful pass for the same effect.
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    if is_mysql:
                        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
                    try:
                        for model in WIPE_ORDER:
                            table = model._meta.db_table
                            # Raw DELETE (not TRUNCATE) so it stays inside the
                            # outer transaction; TRUNCATE auto-commits on MySQL
                            # and would defeat --dry-run rollback.
                            cursor.execute(f"DELETE FROM `{table}`")
                            deleted = cursor.rowcount
                            total += deleted
                            self.stdout.write(f"  {table:<24s} deleted={deleted:>6d}")
                    finally:
                        if is_mysql:
                            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

                if dry_run:
                    transaction.set_rollback(True)
        except Exception:
            self.stdout.write(self.style.ERROR(
                "Wipe aborted; all changes rolled back (no partial state left in DB)."
            ))
            raise

        self.stdout.write("")
        summary = f"Done. deleted {total} row(s) across {len(WIPE_ORDER)} tables."
        if dry_run:
            summary = "[DRY RUN] " + summary
            summary += " (rolled back)"
        self.stdout.write(self.style.SUCCESS(summary))
        if not dry_run:
            self.stdout.write(
                "Next: python manage.py import_ascc_bundle tools/wip/out --truncate"
            )
