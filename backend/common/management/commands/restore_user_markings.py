"""
Batch-restore the per-marking JSON backups written by backup_user_markings.

Every backup file is attempted; a failure does not stop the batch (each
restore_marking call manages its own transaction, so a failed file rolls
back only itself). Results are written to restore_report.json next to the
backups: restored codes plus failures with their error messages -- the
failure list is the editor review queue, mirroring the project's
report-don't-guess rule for unresolvable records. The command exits with an
error when anything failed so operators notice, but by then everything
restorable has been restored.

Usage:
    python backend/manage.py restore_user_markings ./tools/wip/user-backups --dry-run
    python backend/manage.py restore_user_markings ./tools/wip/user-backups
"""
import json
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from common.management.commands.backup_user_markings import MANIFEST_FILENAME

REPORT_FILENAME = "restore_report.json"
REPORT_SCHEMA = "worldcovers.user_markings_restore_report.v1"


class Command(BaseCommand):
    help = (
        "Batch-restore per-marking JSON backups (from backup_user_markings), "
        "continuing past failures and writing a restore_report.json review list."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "in_dir",
            help="Directory containing per-marking JSON backups.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate every file (each restore rolled back); commit nothing.",
        )

    def handle(self, *args, **options):
        in_dir = Path(options["in_dir"])
        if not in_dir.is_dir():
            raise CommandError(f"Input path is not a directory: {in_dir}")
        dry_run = bool(options["dry_run"])

        skip = {MANIFEST_FILENAME, REPORT_FILENAME}
        files = sorted(
            path for path in in_dir.glob("*.json") if path.name not in skip
        )
        if not files:
            raise CommandError(f"No backup files found in {in_dir}.")

        restored, failures = [], []
        for path in files:
            try:
                call_command(
                    "restore_marking", str(path), dry_run=dry_run, verbosity=0
                )
            except Exception as exc:
                failures.append({"file": path.name, "error": str(exc)})
                self.stdout.write(self.style.ERROR(f"FAILED  {path.name}: {exc}"))
            else:
                restored.append(path.name)
                self.stdout.write(f"restored {path.name}")

        report = {
            "schema": REPORT_SCHEMA,
            "generated_at": timezone.now().isoformat(),
            "dry_run": dry_run,
            "restored": restored,
            "failures": failures,
        }
        report_path = in_dir / REPORT_FILENAME
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        summary = (
            f"{len(restored)}/{len(files)} backups "
            + ("validated" if dry_run else "restored")
        )
        if failures:
            raise CommandError(
                f"{summary}; {len(failures)} FAILED -- review {report_path}."
            )
        self.stdout.write(self.style.SUCCESS(f"{summary}; report: {report_path}"))
