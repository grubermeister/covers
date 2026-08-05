"""
Direct loader for pre-#88 (id-based) ASCC bundle CSVs -- VOLUME TESTING ONLY.

The current import_ascc_bundle expects post-#88 bundles keyed by `code`
columns; the bundles available locally (tools/wip/cutover, tools/wip/bundles/*)
are the older id-referenced format it can no longer read. For the issue #59
volume tests only the resulting data shape matters, not the ingest path, so
this command inserts those CSVs directly, preserving their explicit ids.

    DB_NAME=worldcovers_voltest ./woco load_legacy_bundle tools/wip/cutover

Shares the seed_volume_test guard: refuses to touch non-voltest databases.
"""
import csv
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from common.models import (
    Citation, Color, DateSeen, Image, Lettering, Marking,
    PostOffice, PostOfficeRegion, ReferenceWork, Region, Shape,
)
from common.management.commands.seed_volume_test import PROTECTED_DB_HINT

# (csv stem, model, csv id column) in FK-dependency order.
LOAD_ORDER = [
    ("colors", Color, "id"),
    ("letterings", Lettering, "id"),
    ("shapes", Shape, "id"),
    ("reference_works", ReferenceWork, "id"),
    ("regions", Region, "id"),
    ("post_offices", PostOffice, "id"),
    ("post_office_regions", PostOfficeRegion, "id"),
    ("markings", Marking, "id"),
    ("dates_seen", DateSeen, "id"),
    ("citations", Citation, "id"),
    ("images", Image, "image_id"),
]


class Command(BaseCommand):
    help = "Load a legacy id-based bundle directly (volume-test DBs only)."

    def add_arguments(self, parser):
        parser.add_argument("bundle_dir")
        parser.add_argument("--batch-size", type=int, default=2000)
        parser.add_argument("--allow-db", default="")

    def handle(self, *args, **opts):
        db_name = connection.settings_dict["NAME"]
        if PROTECTED_DB_HINT not in db_name and opts["allow_db"] != db_name:
            raise CommandError(f"Refusing to run against '{db_name}'.")
        bundle = Path(opts["bundle_dir"])
        if not bundle.is_dir():
            raise CommandError(f"Not a directory: {bundle}")
        user = get_user_model().objects.order_by("id").first()
        if user is None:
            raise CommandError("Create a user first.")

        with transaction.atomic():
            for stem, model, id_col in LOAD_ORDER:
                path = bundle / f"{stem}.csv"
                if not path.exists():
                    self.stdout.write(f"{stem}: missing, skipped")
                    continue
                n = self._load_csv(path, model, id_col, user.id, opts["batch_size"])
                self.stdout.write(f"{stem}: {n} rows")
            # bulk_create fires no signals; refresh the date-range cache.
            call_command("recompute_marking_date_ranges", "--all")
        self.stdout.write(self.style.SUCCESS(f"Loaded {bundle} into {db_name}."))

    def _load_csv(self, path, model, id_col, user_id, batch_size):
        fields = {f.name: f for f in model._meta.concrete_fields}
        objs, count = [], 0
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                kwargs = {"pk": int(row[id_col]),
                          "created_by_id": user_id, "modified_by_id": user_id}
                for col, raw in row.items():
                    if col in (id_col, "created_date", "modified_date",
                               "created_by", "modified_by", "uploaded_by"):
                        continue
                    field = fields.get(col)
                    if field is None:
                        continue  # legacy column with no current model field
                    if field.is_relation:
                        kwargs[field.attname] = int(raw) if raw != "" else None
                    elif raw == "" and (field.null or field.has_default()):
                        pass  # let NULL/default stand
                    else:
                        kwargs[field.name] = field.to_python(raw)
                if "uploaded_by" in fields and kwargs.get("uploaded_by_id") is None:
                    kwargs["uploaded_by_id"] = user_id
                objs.append(model(**kwargs))
                if len(objs) >= batch_size:
                    count += len(model._default_manager.bulk_create(objs, batch_size=batch_size))
                    objs = []
        if objs:
            count += len(model._default_manager.bulk_create(objs, batch_size=batch_size))
        return count
