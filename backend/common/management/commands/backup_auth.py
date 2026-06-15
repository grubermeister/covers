###################################################################################################
## WoCo Commons - Auth backup export
## MPC: 2025/11/15
###################################################################################################
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from common.auth_resources import (
    AUTH_BACKUP_SCHEMA,
    auth_dataset_specs,
)


def _dataset_payload(dataset):
    return {
        "headers": list(dataset.headers or []),
        "rows": [dict(row) for row in dataset.dict],
    }


class Command(BaseCommand):
    help = (
        "Export auth/config data. By default, write one JSON backup file. "
        "With --emit-csv, write a directory containing fixed CSV files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            help=(
                "JSON output file, or output directory when --emit-csv is used."
            ),
        )
        parser.add_argument(
            "--emit-csv",
            action="store_true",
            help="Write a directory bundle of CSV files instead of one JSON file.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if options["emit_csv"]:
            self._write_csv_bundle(path)
        else:
            self._write_json_backup(path)

        self.stdout.write(
            self.style.WARNING(
                "Reminder: these files contain sensitive data "
                "(emails, password hashes) - encrypt them at rest."
            )
        )

    def _export_datasets(self):
        return {
            spec.name: spec.resource_class().export()
            for spec in auth_dataset_specs()
        }

    def _write_json_backup(self, path):
        if path.exists() and path.is_dir():
            raise CommandError(f"JSON output path is a directory: {path}")

        datasets = self._export_datasets()
        payload = {
            "schema": AUTH_BACKUP_SCHEMA,
            "generated_at": timezone.now().isoformat(),
            "datasets": {
                spec.name: _dataset_payload(datasets[spec.name])
                for spec in auth_dataset_specs()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                payload,
                cls=DjangoJSONEncoder,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(f"Exported auth backup to {path}"))

    def _write_csv_bundle(self, path):
        if path.exists() and not path.is_dir():
            raise CommandError(f"CSV output path exists and is not a directory: {path}")

        path.mkdir(parents=True, exist_ok=True)
        datasets = self._export_datasets()
        for spec in auth_dataset_specs():
            file_path = path / spec.filename
            file_path.write_text(
                datasets[spec.name].export("csv"),
                encoding="utf-8",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Exported {spec.label} to {file_path}"
                )
            )

###################################################################################################
