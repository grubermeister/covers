###################################################################################################
## WoCo Commons - Restore auth objects from backup
## MPC: 2025/11/17
###################################################################################################
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from tablib import Dataset

from common.models import Collection, CollectionAssignment
from common.auth_resources import (
    AUTH_BACKUP_SCHEMA,
    auth_dataset_specs,
)


def _load_csv_dataset(path):
    with path.open("r", encoding="utf-8") as f:
        raw = f.read()
    return Dataset().load(raw, format="csv")


def _dataset_from_payload(name, payload):
    if not isinstance(payload, dict):
        raise CommandError(f"Dataset {name} must be an object.")

    headers = payload.get("headers")
    rows = payload.get("rows")
    if not isinstance(headers, list):
        raise CommandError(f"Dataset {name} must include a headers list.")
    if not isinstance(rows, list):
        raise CommandError(f"Dataset {name} must include a rows list.")

    dataset = Dataset(headers=headers)
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise CommandError(f"Dataset {name} row {index} must be an object.")
        dataset.append([row.get(header) for header in headers])
    return dataset


def _format_import_errors(result):
    parts = []
    if result.has_errors():
        row_errors = result.row_errors()
        parts.append(f"row errors: {row_errors[:20]}")
        if len(row_errors) > 20:
            parts.append(f"... {len(row_errors) - 20} additional row errors")
    if result.has_validation_errors():
        invalid_rows = result.invalid_rows
        parts.append(f"validation errors: {invalid_rows[:20]}")
        if len(invalid_rows) > 20:
            parts.append(f"... {len(invalid_rows) - 20} additional validation errors")
    return "\n".join(parts) or "unknown import error"


def _import_or_raise(resource, dataset, label):
    result = resource.import_data(dataset, dry_run=False)
    if result.has_errors() or result.has_validation_errors():
        raise CommandError(f"Errors importing {label}:\n{_format_import_errors(result)}")
    return result


def _assignment_keep_ids(dataset):
    keep_filter = Q()
    for row in dataset.dict:
        username = (row.get("user") or "").strip()
        collection_name = (row.get("collection") or "").strip()
        if not username or not collection_name:
            continue
        keep_filter |= Q(user__username=username, collection__name=collection_name)

    if not keep_filter:
        return []

    return list(
        CollectionAssignment.objects.filter(keep_filter).values_list("pk", flat=True)
    )


def _missing_collection_names(dataset):
    if dataset is None:
        return set()

    missing = set()
    for row in dataset.dict:
        name = (row.get("name") or "").strip()
        if name and not Collection.objects.filter(name=name).exists():
            missing.add(name)
    return missing


def _without_skipped_collection_assignments(dataset, skipped_collection_names):
    if not skipped_collection_names:
        return dataset, 0

    filtered = Dataset(headers=dataset.headers)
    skipped = 0
    for row in dataset.dict:
        if (row.get("collection") or "").strip() in skipped_collection_names:
            skipped += 1
            continue
        filtered.append([row.get(header) for header in dataset.headers])
    return filtered, skipped


class Command(BaseCommand):
    help = (
        "Import auth/config data from one JSON backup file. With --emit-csv, "
        "read a directory containing fixed CSV files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            help=(
                "JSON input file, or input directory when --emit-csv is used."
            ),
        )
        parser.add_argument(
            "--emit-csv",
            action="store_true",
            help="Read a directory bundle of CSV files instead of one JSON file.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run import validation without committing changes.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["path"])
        dry_run = options["dry_run"]

        if options["emit_csv"]:
            datasets = self._load_csv_bundle(path)
        else:
            datasets = self._load_json_backup(path)

        self._import_datasets(datasets)

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(
                self.style.SUCCESS("Dry run completed; no data committed.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Auth import completed successfully.")
            )

    def _load_json_backup(self, path):
        if not path.exists():
            raise CommandError(f"JSON input file does not exist: {path}")
        if path.is_dir():
            raise CommandError(f"JSON input path is a directory: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Could not parse JSON backup: {exc}") from exc

        if payload.get("schema") != AUTH_BACKUP_SCHEMA:
            raise CommandError(
                "Unsupported auth backup schema: "
                f"{payload.get('schema')!r}"
            )
        raw_datasets = payload.get("datasets")
        if not isinstance(raw_datasets, dict):
            raise CommandError("Auth backup must include a datasets object.")

        datasets = {}
        for spec in auth_dataset_specs():
            raw_dataset = raw_datasets.get(spec.name)
            if raw_dataset is None:
                if spec.required:
                    raise CommandError(
                        f"Auth backup is missing required dataset: {spec.name}"
                    )
                continue
            datasets[spec.name] = _dataset_from_payload(spec.name, raw_dataset)
        return datasets

    def _load_csv_bundle(self, path):
        if not path.exists():
            raise CommandError(f"CSV input directory does not exist: {path}")
        if not path.is_dir():
            raise CommandError(f"CSV input path is not a directory: {path}")

        datasets = {}
        for spec in auth_dataset_specs():
            file_path = path / spec.filename
            if not file_path.exists():
                if spec.required:
                    raise CommandError(
                        f"CSV bundle is missing required file: {file_path}"
                    )
                continue
            datasets[spec.name] = _load_csv_dataset(file_path)
        return datasets

    def _import_datasets(self, datasets):
        specs = {spec.name: spec for spec in auth_dataset_specs()}
        skipped_collection_names = set()

        group_dataset = datasets.get("groups")
        if group_dataset is not None:
            spec = specs["groups"]
            _import_or_raise(spec.resource_class(), group_dataset, spec.label)
            self.stdout.write(self.style.SUCCESS("Groups imported."))
        else:
            self.stdout.write("Groups import skipped.")

        user_dataset = datasets.get("users")
        if user_dataset is None:
            raise CommandError("Auth backup is missing required users dataset.")
        spec = specs["users"]
        _import_or_raise(spec.resource_class(), user_dataset, spec.label)
        self.stdout.write(self.style.SUCCESS("Users imported."))

        email_dataset = datasets.get("emails")
        if email_dataset is not None:
            spec = specs["emails"]
            _import_or_raise(spec.resource_class(), email_dataset, spec.label)
            self.stdout.write(self.style.SUCCESS("Email addresses imported."))
        else:
            self.stdout.write("Email address import skipped.")

        collection_dataset = datasets.get("collections")
        if collection_dataset is not None:
            spec = specs["collections"]
            _import_or_raise(spec.resource_class(), collection_dataset, spec.label)
            skipped_collection_names = _missing_collection_names(collection_dataset)
            self.stdout.write(self.style.SUCCESS("State collections imported."))
            if skipped_collection_names:
                self.stdout.write(
                    self.style.WARNING(
                        "Skipped collection rows that conflict with local "
                        "Region ownership: "
                        + ", ".join(sorted(skipped_collection_names))
                    )
                )
        else:
            self.stdout.write("State collections import skipped.")

        assignment_dataset = datasets.get("assignments")
        if assignment_dataset is not None:
            assignment_dataset, skipped_assignments = (
                _without_skipped_collection_assignments(
                    assignment_dataset,
                    skipped_collection_names,
                )
            )
            spec = specs["assignments"]
            _import_or_raise(spec.resource_class(), assignment_dataset, spec.label)
            keep_ids = _assignment_keep_ids(assignment_dataset)
            deleted, _ = CollectionAssignment.objects.exclude(pk__in=keep_ids).delete()
            self.stdout.write(
                self.style.SUCCESS("Collection assignments imported.")
            )
            if skipped_assignments:
                self.stdout.write(
                    self.style.WARNING(
                        "Skipped "
                        f"{skipped_assignments} assignment row(s) for skipped "
                        "collection rows."
                    )
                )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Collection assignments mirrored; removed {deleted} stale rows."
                )
            )
        else:
            self.stdout.write("Collection assignments import skipped.")

###################################################################################################
