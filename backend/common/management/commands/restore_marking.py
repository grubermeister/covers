import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from tablib import Dataset

from common.marking_resources import (
    MARKING_BACKUP_SCHEMA,
    build_polymorphic_resolver,
    marking_dataset_specs,
)
from common.models import (
    Collection,
    Contribution,
    Cover,
    CoverRecycleBin,
    CoverVersion,
    Image,
    Marking,
    MarkingRecycleBin,
    MarkingVersion,
    SubmissionTransaction,
)


User = get_user_model()


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


def _replace_dataset_rows(dataset, rows):
    replacement = Dataset(headers=list(dataset.headers or []))
    for row in rows:
        replacement.append([row.get(header) for header in replacement.headers])
    return replacement


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


def _parse_dt(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


class Command(BaseCommand):
    help = (
        "Restore a marking-rooted JSON backup. The restore is idempotent on "
        "natural keys and can be validated with --dry-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            help="JSON input file to restore.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run import validation without committing changes.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        dry_run = bool(options["dry_run"])
        root_code, datasets = self._load_backup(path)

        self._validate_required_users(datasets)

        with transaction.atomic():
            self._import_datasets(datasets)
            self._restore_saved_timestamps(datasets)
            self._verify_restored_graph(root_code, datasets)

            if dry_run:
                transaction.set_rollback(True)

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS("Dry run completed; no data committed.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Marking restore completed successfully: {root_code}"
                )
            )
        self._warn_missing_media(datasets)

    def _load_backup(self, path):
        if not path.exists():
            raise CommandError(f"Input file does not exist: {path}")
        if path.is_dir():
            raise CommandError(f"Input path is a directory: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Could not parse JSON backup: {exc}") from exc

        if payload.get("schema") != MARKING_BACKUP_SCHEMA:
            raise CommandError(
                f"Unsupported marking backup schema: {payload.get('schema')!r}"
            )
        root_code = (payload.get("root_marking_code") or "").strip()
        if not root_code:
            raise CommandError("Marking backup is missing required root_marking_code.")
        raw_datasets = payload.get("datasets")
        if not isinstance(raw_datasets, dict):
            raise CommandError("Marking backup must include a datasets object.")

        datasets = {}
        for spec in marking_dataset_specs():
            raw_dataset = raw_datasets.get(spec.name)
            if raw_dataset is None:
                if spec.required:
                    raise CommandError(
                        f"Marking backup is missing required dataset: {spec.name}"
                    )
                continue
            datasets[spec.name] = _dataset_from_payload(spec.name, raw_dataset)
        return root_code, datasets

    def _validate_required_users(self, datasets):
        contribution_dataset = datasets.get("contributions")
        if contribution_dataset is None:
            return

        usernames = {
            (row.get("contributor") or "").strip()
            for row in contribution_dataset.dict
        }
        usernames.discard("")
        blank_count = sum(
            1
            for row in contribution_dataset.dict
            if not (row.get("contributor") or "").strip()
        )
        if blank_count:
            raise CommandError(
                "Contribution restore requires every row to include a contributor."
            )

        existing = set(
            User.objects.filter(username__in=usernames).values_list(
                "username",
                flat=True,
            )
        )
        missing = sorted(usernames - existing)
        if missing:
            raise CommandError(
                "Cannot restore marking backup because contributor user(s) are "
                "missing locally: "
                + ", ".join(missing)
            )

    def _import_datasets(self, datasets):
        for spec in marking_dataset_specs():
            dataset = datasets.get(spec.name)
            if dataset is None:
                self.stdout.write(f"{spec.label} import skipped.")
                continue

            if spec.name == "contributions":
                dataset = self._normalize_contribution_collections(dataset, datasets)
                datasets[spec.name] = dataset

            resource_kwargs = {}
            if spec.polymorphic:
                resource_kwargs["resolver"] = build_polymorphic_resolver()
            resource = spec.resource_class(**resource_kwargs)
            result = _import_or_raise(resource, dataset, spec.label)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported {spec.label} ({result.total_rows} rows)."
                )
            )

    def _normalize_contribution_collections(self, dataset, datasets):
        collection_dataset = datasets.get("collections")
        if collection_dataset is None:
            return dataset

        region_name_by_collection_name = {}
        for row in collection_dataset.dict:
            name = (row.get("name") or "").strip()
            region_name = (row.get("region") or "").strip()
            if name and region_name:
                region_name_by_collection_name[name] = region_name

        if not region_name_by_collection_name:
            return dataset

        rows = []
        changed = False
        for row in dataset.dict:
            next_row = dict(row)
            collection_name = (next_row.get("collection") or "").strip()
            region_name = region_name_by_collection_name.get(collection_name)
            if region_name:
                local_collection = (
                    Collection.objects.filter(region__name=region_name)
                    .order_by("pk")
                    .first()
                )
                if local_collection is not None:
                    local_name = local_collection.name
                    if local_name != collection_name:
                        next_row["collection"] = local_name
                        changed = True
            rows.append(next_row)

        if not changed:
            return dataset
        return _replace_dataset_rows(dataset, rows)

    def _restore_saved_timestamps(self, datasets):
        self._restore_contribution_timestamps(datasets.get("contributions"))
        self._restore_transaction_timestamps(datasets.get("submission_transactions"))
        self._restore_marking_version_timestamps(datasets.get("marking_versions"))
        self._restore_cover_version_timestamps(datasets.get("cover_versions"))
        self._restore_marking_recycle_timestamps(datasets.get("marking_recycle_bin"))
        self._restore_cover_recycle_timestamps(datasets.get("cover_recycle_bin"))

    def _restore_contribution_timestamps(self, dataset):
        if dataset is None:
            return
        for row in dataset.dict:
            code = (row.get("marking_code") or "").strip()
            if not code:
                continue
            updates = {}
            created = _parse_dt(row.get("created_date"))
            modified = _parse_dt(row.get("modified_date"))
            if created is not None:
                updates["created_date"] = created
            if modified is not None:
                updates["modified_date"] = modified
            if updates:
                Contribution.objects.filter(marking__code=code).update(**updates)

    def _restore_transaction_timestamps(self, dataset):
        if dataset is None:
            return
        for row in dataset.dict:
            created = _parse_dt(row.get("created_at"))
            transaction_uuid = row.get("transaction_uuid")
            if created is not None and transaction_uuid:
                SubmissionTransaction.objects.filter(
                    transaction_uuid=transaction_uuid,
                ).update(created_at=created)

    def _restore_marking_version_timestamps(self, dataset):
        if dataset is None:
            return
        for row in dataset.dict:
            created = _parse_dt(row.get("created_at"))
            code = (row.get("marking_code") or "").strip()
            version_no = row.get("version_no")
            if created is None or not code or version_no in (None, ""):
                continue
            marking = Marking.all_objects.filter(code=code).first()
            if marking is None:
                continue
            MarkingVersion.objects.filter(
                marking=marking,
                version_no=version_no,
            ).update(created_at=created)

    def _restore_cover_version_timestamps(self, dataset):
        if dataset is None:
            return
        for row in dataset.dict:
            created = _parse_dt(row.get("created_at"))
            code = (row.get("cover_code") or "").strip()
            version_no = row.get("version_no")
            if created is None or not code or version_no in (None, ""):
                continue
            cover = Cover.all_objects.filter(code=code).first()
            if cover is None:
                continue
            CoverVersion.objects.filter(
                cover=cover,
                version_no=version_no,
            ).update(created_at=created)

    def _restore_marking_recycle_timestamps(self, dataset):
        if dataset is None:
            return
        for row in dataset.dict:
            removed_at = _parse_dt(row.get("removed_at"))
            code = (row.get("marking_code") or "").strip()
            if removed_at is None or not code:
                continue
            marking = Marking.all_objects.filter(code=code).first()
            if marking is not None:
                MarkingRecycleBin.objects.filter(marking=marking).update(
                    removed_at=removed_at,
                )

    def _restore_cover_recycle_timestamps(self, dataset):
        if dataset is None:
            return
        for row in dataset.dict:
            removed_at = _parse_dt(row.get("removed_at"))
            code = (row.get("cover_code") or "").strip()
            if removed_at is None or not code:
                continue
            cover = Cover.all_objects.filter(code=code).first()
            if cover is not None:
                CoverRecycleBin.objects.filter(cover=cover).update(
                    removed_at=removed_at,
                )

    def _verify_restored_graph(self, root_code, datasets):
        marking = Marking.all_objects.filter(code=root_code).first()
        if marking is None:
            raise CommandError(
                f"Restore did not create or update root marking: {root_code}"
            )

        contribution_dataset = datasets.get("contributions")
        if contribution_dataset is None:
            return

        missing_links = []
        for row in contribution_dataset.dict:
            code = (row.get("marking_code") or "").strip()
            username = (row.get("contributor") or "").strip()
            if not code or not username:
                continue
            exists = Contribution.objects.filter(
                marking__code=code,
                contributor__username=username,
            ).exists()
            if not exists:
                missing_links.append(f"{code} -> {username}")

        if missing_links:
            raise CommandError(
                "Restore did not create expected contribution link(s): "
                + ", ".join(missing_links)
            )

    def _warn_missing_media(self, datasets):
        image_dataset = datasets.get("images")
        if image_dataset is None:
            return
        try:
            media_root = Path(settings.MEDIA_ROOT)
        except TypeError:
            self.stdout.write(
                self.style.WARNING(
                    f"Could not verify restored image files under MEDIA_ROOT: "
                    f"{settings.MEDIA_ROOT!r}"
                )
            )
            return
        missing = []
        for row in image_dataset.dict:
            storage = (row.get("storage_filename") or "").strip().lstrip("/")
            if not storage:
                continue
            try:
                image_path = media_root / storage
            except TypeError:
                self.stdout.write(
                    self.style.WARNING(
                        f"Could not verify restored image file path: {storage}"
                    )
                )
                continue
            if not image_path.is_file():
                missing.append(storage)
        if not missing:
            return
        for storage in sorted(set(missing)):
            self.stdout.write(
                self.style.WARNING(
                    f"Image file missing for restored metadata: {storage}"
                )
            )
