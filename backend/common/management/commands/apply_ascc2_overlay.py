"""
Apply a Sixth Edition ASCC overlay onto an ASCC1 baseline import.

This command expects:
  * an ASCC1 bundle directory with marking_lineage.csv
  * an ASCC2 overlay bundle directory produced by remunging the overlay CSV
  * an overlay map CSV from tools/build_ascc2_overlay.py
  * a v1 image-ref sidecar from tools/v1_to_v2_catalog_format.py

Behavior:
  * backfill visible ASCC1 record-create history when requested
  * update matched markings in place, preserving existing ASCC1/v2 images
  * add brand-new ASCC2 markings
  * soft-remove obsolete ASCC1 markings
  * replace direct MARKING citations and dates_seen from the ASCC2 bundle
  * append v1 image refs to the target townmark rows without deleting current
    images or duplicating storage_filename rows already on the target

Run from repo root with `woco`:

    woco apply_ascc2_overlay \
      --base-dir tools/wip/cache/ascc1 \
      --overlay-dir tools/wip/cache/ascc2_overlay_bundle \
      --overlay-map tools/wip/out/VA_ASCC2_overlay_map.csv \
      --v1-image-refs tools/wip/in/v1_VA_image_refs.csv \
      --region-abbrev VA \
      --ascc1-code ASCC1 \
      --ascc2-code ASCC2 \
      --audit-user-id 1

Use `--dry-run`; expected exit code 0.
"""
from __future__ import annotations

import csv
import hashlib
import mimetypes
from collections import defaultdict
from pathlib import Path

from PIL import Image as PILImage

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.audit import (
    build_marking_snapshot,
    create_marking_version,
    log_marking_removed,
    log_submission_transaction,
)
from common.models import (
    Citation,
    Color,
    DateSeen,
    Image,
    Lettering,
    Marking,
    MarkingRecycleBin,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
    Shape,
    SubmissionTransaction,
)


User = get_user_model()

MARKING_COLUMNS = [
    "id", "code", "type", "catalog_txt", "inscription_txt", "desc",
    "is_manuscript", "shape", "lettering", "color", "is_irreg", "width",
    "height", "date_fmt", "impression", "rate_val", "post_office",
]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def required_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise CommandError(f"Missing CSV: {path}")
    return read_csv_rows(path)


def maybe_int(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def maybe_decimal_text(value):
    raw = str(value or "").strip()
    return raw or None


def maybe_text(value):
    raw = str(value or "").strip()
    return raw or None


def parse_bool_or_none(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"true", "1", "yes"}:
        return True
    if raw in {"false", "0", "no"}:
        return False
    raise CommandError(f"Invalid boolean value: {value!r}")


def group_rows(rows, *keys):
    out = defaultdict(list)
    for row in rows:
        out[tuple(str(row.get(k, "") or "").strip() for k in keys)].append(row)
    return out


def first_row_by(rows, key):
    return {str(row.get(key, "") or "").strip(): row for row in rows}


def family_key(page, chunk):
    return (str(page or "").strip(), str(chunk or "").strip())


def lineage_primary_key(row):
    return (
        str(row.get("mark_kind", "") or "").strip(),
        str(row.get("parent_mark_type", "") or "").strip(),
        str(row.get("parent_local_index", "") or "").strip(),
        str(row.get("color_name", "") or "").strip(),
        str(row.get("inscription_txt", "") or "").strip(),
        str(row.get("rate_raw", "") or "").strip(),
        str(row.get("local_index", "") or "").strip(),
    )


def lineage_structural_key(row):
    return (
        str(row.get("mark_kind", "") or "").strip(),
        str(row.get("parent_mark_type", "") or "").strip(),
        str(row.get("parent_local_index", "") or "").strip(),
        str(row.get("color_name", "") or "").strip(),
        str(row.get("local_index", "") or "").strip(),
    )


def load_bundle_context(bundle_dir: Path):
    colors = required_csv(bundle_dir / "colors.csv")
    shapes = required_csv(bundle_dir / "shapes.csv")
    letterings = required_csv(bundle_dir / "letterings.csv")
    post_offices = required_csv(bundle_dir / "post_offices.csv")
    markings = required_csv(bundle_dir / "markings.csv")
    dates_seen = required_csv(bundle_dir / "dates_seen.csv")
    citations = required_csv(bundle_dir / "citations.csv")
    lineage = required_csv(bundle_dir / "marking_lineage.csv")
    return {
        "colors_by_id": first_row_by(colors, "id"),
        "shapes_by_id": first_row_by(shapes, "id"),
        "letterings_by_id": first_row_by(letterings, "id"),
        "post_offices_by_id": first_row_by(post_offices, "id"),
        "markings_by_id": first_row_by(markings, "id"),
        "dates_by_marking_id": group_rows(dates_seen, "subject_id"),
        "citations_by_marking_id": group_rows(citations, "subject_id"),
        "lineage_rows": lineage,
        "lineage_by_code": first_row_by(lineage, "marking_code"),
        "lineage_by_family": group_rows(lineage, "family_root_page", "family_root_chunk"),
    }


def natural_id_maps(region: Region):
    color_ids = {row.name: row.id for row in Color.objects.all()}
    shape_ids = {row.name: row.id for row in Shape.objects.all()}
    lettering_ids = {row.name: row.id for row in Lettering.objects.all()}
    post_offices = (
        PostOffice.objects.filter(post_office_regions__region=region)
        .distinct()
        .order_by("id")
    )
    post_office_ids = {}
    for row in post_offices:
        if row.name in post_office_ids:
            raise CommandError(
                f"Region {region.abbrev} has multiple post offices named {row.name!r}; "
                "natural-key resolution would be ambiguous."
            )
        post_office_ids[row.name] = row.id
    return {
        "color_ids": color_ids,
        "shape_ids": shape_ids,
        "lettering_ids": lettering_ids,
        "post_office_ids": post_office_ids,
    }


def ensure_overlay_post_offices(region: Region, overlay_ctx, actor):
    """Create PostOffice rows present in the overlay bundle but missing in DB."""
    existing = {
        row.name: row.id
        for row in PostOffice.objects.filter(post_office_regions__region=region).distinct()
    }
    created = 0
    for row in overlay_ctx["post_offices_by_id"].values():
        name = str(row.get("name", "") or "").strip()
        if not name or name in existing:
            continue
        post_office = PostOffice.objects.create(
            name=name,
            created_by=actor,
            modified_by=actor,
        )
        PostOfficeRegion.objects.create(
            post_office=post_office,
            region=region,
            created_by=actor,
            modified_by=actor,
        )
        existing[name] = post_office.id
        created += 1
    return created


def resolve_bundle_fk(bundle_ctx, db_maps, stem, raw_id):
    key = str(raw_id or "").strip()
    if not key:
        return None
    if stem == "color":
        row = bundle_ctx["colors_by_id"].get(key)
        name = str((row or {}).get("name", "") or "").strip()
        return db_maps["color_ids"].get(name)
    if stem == "shape":
        row = bundle_ctx["shapes_by_id"].get(key)
        name = str((row or {}).get("name", "") or "").strip()
        return db_maps["shape_ids"].get(name)
    if stem == "lettering":
        row = bundle_ctx["letterings_by_id"].get(key)
        name = str((row or {}).get("name", "") or "").strip()
        return db_maps["lettering_ids"].get(name)
    if stem == "post_office":
        row = bundle_ctx["post_offices_by_id"].get(key)
        name = str((row or {}).get("name", "") or "").strip()
        return db_maps["post_office_ids"].get(name)
    raise CommandError(f"Unknown lookup stem: {stem}")


def bundle_marking_values(bundle_ctx, db_maps, marking_row):
    is_manuscript = bool(parse_bool_or_none(marking_row.get("is_manuscript")))
    color_id = resolve_bundle_fk(bundle_ctx, db_maps, "color", marking_row.get("color"))
    if color_id is None:
        color_id = db_maps["color_ids"].get("BLACK")
    return {
        "code": maybe_text(marking_row.get("code")),
        "type": str(marking_row.get("type", "") or "").strip(),
        "catalog_txt": maybe_text(marking_row.get("catalog_txt")),
        "inscription_txt": str(marking_row.get("inscription_txt", "") or "").strip(),
        "desc": maybe_text(marking_row.get("desc")),
        "is_manuscript": is_manuscript,
        "shape_id": resolve_bundle_fk(bundle_ctx, db_maps, "shape", marking_row.get("shape")),
        "lettering_id": resolve_bundle_fk(bundle_ctx, db_maps, "lettering", marking_row.get("lettering")),
        "color_id": color_id,
        "is_irreg": parse_bool_or_none(marking_row.get("is_irreg")),
        "width": maybe_decimal_text(marking_row.get("width")),
        "height": maybe_decimal_text(marking_row.get("height")),
        "date_fmt": maybe_text(marking_row.get("date_fmt")),
        "impression": maybe_text(marking_row.get("impression")),
        "rate_val": maybe_decimal_text(marking_row.get("rate_val")),
        "post_office_id": resolve_bundle_fk(bundle_ctx, db_maps, "post_office", marking_row.get("post_office")),
    }


def apply_marking_values(marking: Marking, values: dict, actor):
    marking.code = values["code"]
    marking.type = values["type"]
    marking.catalog_txt = values["catalog_txt"]
    marking.inscription_txt = values["inscription_txt"]
    marking.desc = values["desc"]
    marking.is_manuscript = values["is_manuscript"]
    marking.shape_id = values["shape_id"]
    marking.lettering_id = values["lettering_id"]
    marking.color_id = values["color_id"]
    marking.is_irreg = values["is_irreg"]
    marking.width = values["width"]
    marking.height = values["height"]
    marking.date_fmt = values["date_fmt"]
    marking.impression = values["impression"]
    marking.rate_val = values["rate_val"]
    marking.post_office_id = values["post_office_id"]
    marking.modified_by = actor


def replace_marking_dates(marking: Marking, date_rows: list[dict[str, str]], actor):
    DateSeen.objects.filter(subject_type="MARKING", subject_id=marking.pk).delete()
    new_rows = []
    for row in date_rows:
        date_value = maybe_text(row.get("date"))
        granularity = maybe_text(row.get("granularity"))
        if not date_value or not granularity:
            continue
        new_rows.append(
            DateSeen(
                subject_type="MARKING",
                subject_id=marking.pk,
                date=date_value,
                granularity=granularity,
                created_by=actor,
                modified_by=actor,
            )
        )
    if new_rows:
        DateSeen.objects.bulk_create(new_rows)


def replace_marking_citations(marking: Marking, citation_rows: list[dict[str, str]], actor, ascc2_ref: ReferenceWork):
    Citation.objects.filter(subject_type="MARKING", subject_id=marking.pk).delete()
    for row in citation_rows:
        Citation.objects.create(
            reference_work=ascc2_ref,
            subject_type="MARKING",
            subject_id=marking.pk,
            citation_detail=str(row.get("citation_detail", "") or "").strip(),
            created_by=actor,
            modified_by=actor,
        )


def storage_meta(storage_root: Path, storage_filename: str, cache: dict[str, dict]):
    key = str(storage_filename or "").strip().lstrip("/")
    if not key:
        raise CommandError("Image ref row is missing storage_filename.")
    if key in cache:
        return cache[key]
    disk_path = storage_root / key
    if not disk_path.is_file():
        raise CommandError(f"Missing image file: {disk_path}")
    data = disk_path.read_bytes()
    with PILImage.open(disk_path) as image:
        width, height = image.size
    meta = {
        "file_checksum": hashlib.sha256(data).hexdigest(),
        "mime_type": mimetypes.guess_type(disk_path.name)[0] or "image/png",
        "image_width": width,
        "image_height": height,
        "file_size_bytes": len(data),
    }
    cache[key] = meta
    return meta


def add_v1_images(
    marking: Marking,
    image_rows: list[dict[str, str]],
    actor,
    storage_root: Path,
    cache: dict[str, dict],
    *,
    skip_missing_images: bool,
    missing_images: list[str],
):
    existing = list(
        Image.objects.filter(subject_type="MARKING", subject_id=marking.pk).order_by("display_order", "image_id")
    )
    by_storage = {row.storage_filename.lstrip("/"): row for row in existing}
    next_order = (existing[-1].display_order + 1) if existing else 1
    added = 0
    for row in image_rows:
        storage_filename = str(row.get("storage_filename", "") or "").strip().lstrip("/")
        if not storage_filename or storage_filename in by_storage:
            continue
        try:
            meta = storage_meta(storage_root, storage_filename, cache)
        except CommandError:
            if not skip_missing_images:
                raise
            missing_images.append(storage_filename)
            continue
        Image.objects.create(
            subject_type="MARKING",
            subject_id=marking.pk,
            original_filename=str(row.get("source_filename", "") or "").strip() or Path(storage_filename).name,
            storage_filename=storage_filename,
            file_checksum=meta["file_checksum"],
            mime_type=meta["mime_type"],
            image_width=meta["image_width"],
            image_height=meta["image_height"],
            file_size_bytes=meta["file_size_bytes"],
            image_view=maybe_text(row.get("image_view")) or "FULL",
            image_description=str(row.get("image_description", "") or "").strip(),
            is_tracing=bool(parse_bool_or_none(row.get("is_tracing"))),
            display_order=next_order,
            uploaded_by=actor,
            created_by=actor,
            modified_by=actor,
        )
        next_order += 1
        added += 1
    return added


def match_family(current_items, target_items):
    """Return (pairs, current_only, target_only) using exact then structural keys."""
    pairs = []
    unmatched_current = list(current_items)
    unmatched_target = list(target_items)

    def consume_by(key_fn):
        nonlocal unmatched_current, unmatched_target
        current_by_key = defaultdict(list)
        target_by_key = defaultdict(list)
        for item in unmatched_current:
            current_by_key[key_fn(item["lineage"])].append(item)
        for item in unmatched_target:
            target_by_key[key_fn(item["lineage"])].append(item)
        next_current = []
        next_target = []
        all_keys = set(current_by_key) | set(target_by_key)
        for key in all_keys:
            current_bucket = current_by_key.get(key, [])
            target_bucket = target_by_key.get(key, [])
            pair_count = min(len(current_bucket), len(target_bucket))
            for index in range(pair_count):
                pairs.append((current_bucket[index], target_bucket[index]))
            next_current.extend(current_bucket[pair_count:])
            next_target.extend(target_bucket[pair_count:])
        unmatched_current = next_current
        unmatched_target = next_target

    consume_by(lineage_primary_key)
    consume_by(lineage_structural_key)
    return pairs, unmatched_current, unmatched_target


def family_codes(rows):
    return {
        str(row.get("marking_code", "") or "").strip()
        for row in rows
        if str(row.get("marking_code", "") or "").strip()
    }


def backfill_ascc1_history(actor, ascc1_ref):
    cited_marking_ids = Citation.objects.filter(
        reference_work=ascc1_ref,
        subject_type="MARKING",
    ).values_list("subject_id", flat=True)
    targets = (
        Marking.all_objects.filter(id__in=cited_marking_ids)
        .exclude(versions__isnull=False)
        .distinct()
        .select_related("post_office")
    )
    count = 0
    for marking in targets:
        after_snapshot = build_marking_snapshot(marking)
        txn = log_submission_transaction(
            action=SubmissionTransaction.ACTION_RECORD_CREATE,
            actor=actor,
            marking=marking,
            source=SubmissionTransaction.SOURCE_SYSTEM,
            before_payload={},
            after_payload=after_snapshot,
            extra_payload={"workflow": "ascc1_history_backfill"},
        )
        create_marking_version(marking, txn, actor)
        count += 1
    return count


class Command(BaseCommand):
    help = "Apply a remunged ASCC2 overlay onto an imported ASCC1 baseline."

    def add_arguments(self, parser):
        parser.add_argument("--base-dir", required=True, help="ASCC1 bundle directory")
        parser.add_argument("--overlay-dir", required=True, help="ASCC2 overlay bundle directory")
        parser.add_argument("--overlay-map", required=True, help="Overlay map CSV path")
        parser.add_argument("--v1-image-refs", required=True, help="v1 image-ref CSV path")
        parser.add_argument("--region-abbrev", required=True, help="Target region abbrev (e.g. VA)")
        parser.add_argument("--ascc1-code", required=True, help="ReferenceWork.code for ASCC1")
        parser.add_argument("--ascc2-code", required=True, help="ReferenceWork.code for ASCC2")
        parser.add_argument("--audit-user-id", type=int, default=1, help="Actor user id for system history")
        parser.add_argument("--skip-backfill-history", action="store_true", help="Skip ASCC1 record-create history backfill")
        parser.add_argument("--skip-missing-images", action="store_true", help="Skip v1 image refs whose files are not present on disk")
        parser.add_argument("--dry-run", action="store_true", help="Validate and simulate only; roll back transaction")

    def handle(self, *args, **options):
        base_dir = Path(options["base_dir"])
        overlay_dir = Path(options["overlay_dir"])
        overlay_map_path = Path(options["overlay_map"])
        v1_image_refs_path = Path(options["v1_image_refs"])
        region_abbrev = str(options["region_abbrev"]).strip().upper()
        dry_run = bool(options["dry_run"])

        if not base_dir.is_dir():
            raise CommandError(f"Not a directory: {base_dir}")
        if not overlay_dir.is_dir():
            raise CommandError(f"Not a directory: {overlay_dir}")
        overlay_map_rows = required_csv(overlay_map_path)
        v1_image_rows = required_csv(v1_image_refs_path)

        try:
            actor = User.objects.get(pk=options["audit_user_id"])
        except User.DoesNotExist as exc:
            raise CommandError(f"Audit user not found: id={options['audit_user_id']}") from exc

        try:
            region = Region.objects.get(abbrev__iexact=region_abbrev)
        except Region.DoesNotExist as exc:
            raise CommandError(f"Region not found: {region_abbrev}") from exc

        try:
            ascc1_ref = ReferenceWork.objects.get(code=options["ascc1_code"])
        except ReferenceWork.DoesNotExist as exc:
            raise CommandError(f"ReferenceWork not found: code={options['ascc1_code']}") from exc
        try:
            ascc2_ref = ReferenceWork.objects.get(code=options["ascc2_code"])
        except ReferenceWork.DoesNotExist as exc:
            raise CommandError(f"ReferenceWork not found: code={options['ascc2_code']}") from exc

        base_ctx = load_bundle_context(base_dir)
        overlay_ctx = load_bundle_context(overlay_dir)
        storage_root = overlay_dir.parents[2] / "backend" / "media" if len(overlay_dir.parents) >= 3 else Path("backend/media")
        if not storage_root.is_dir():
            storage_root = Path(__file__).resolve().parents[4] / "backend" / "media"

        v1_images_by_source = group_rows(v1_image_rows, "source_row_id")
        overlay_map_by_compare_family = group_rows(overlay_map_rows, "compare_family_page", "compare_family_chunk")
        removed_family_rows = [row for row in overlay_map_rows if str(row.get("family_action", "")).strip() == "removed"]
        rep_chunk_by_source_id = {
            str(row.get("compare_source_id", "") or "").strip(): str(row.get("compare_chunk", "") or "").strip()
            for row in overlay_map_rows
            if str(row.get("compare_source_id", "") or "").strip()
        }
        image_meta_cache = {}
        summary = {
            "history_backfilled": 0,
            "updated": 0,
            "created": 0,
            "removed": 0,
            "images_added": 0,
            "images_missing": 0,
            "post_offices_created": 0,
        }
        missing_images = []

        with transaction.atomic():
            summary["post_offices_created"] = ensure_overlay_post_offices(region, overlay_ctx, actor)
            db_maps = natural_id_maps(region)

            if not options["skip_backfill_history"]:
                summary["history_backfilled"] = backfill_ascc1_history(actor, ascc1_ref)

            for compare_family_key, lineage_rows in overlay_ctx["lineage_by_family"].items():
                overlay_map_family = overlay_map_by_compare_family.get(compare_family_key, [])
                include_rows = [row for row in overlay_map_family if str(row.get("include_in_overlay", "")).strip().lower() == "true"]
                if not include_rows:
                    continue
                family_action = str(include_rows[0].get("family_action", "") or "").strip()
                base_family_keys = {
                    family_key(row.get("base_page"), row.get("base_chunk"))
                    for row in include_rows
                    if str(row.get("base_chunk", "") or "").strip() or str(row.get("base_page", "") or "").strip()
                }
                if family_action == "material" and len(base_family_keys) != 1:
                    raise CommandError(
                        f"Overlay family {compare_family_key} expected exactly 1 base family; got {sorted(base_family_keys)}"
                    )
                base_family_rows = []
                for key in base_family_keys:
                    base_family_rows.extend(base_ctx["lineage_by_family"].get(key, []))

                current_codes = family_codes(base_family_rows) | family_codes(lineage_rows)
                current_markings = list(Marking.all_objects.filter(code__in=current_codes).select_related("post_office"))
                current_items = []
                for marking in current_markings:
                    lineage = overlay_ctx["lineage_by_code"].get(marking.code) or base_ctx["lineage_by_code"].get(marking.code)
                    if lineage is None:
                        continue
                    current_items.append({"marking": marking, "lineage": lineage})

                target_items = []
                finalize_rows = []
                for lineage in lineage_rows:
                    mark_id = str(lineage.get("marking_id", "") or "").strip()
                    marking_row = overlay_ctx["markings_by_id"].get(mark_id)
                    if marking_row is None:
                        raise CommandError(f"Overlay lineage references unknown marking id: {mark_id}")
                    target_items.append(
                        {
                            "lineage": lineage,
                            "marking_row": marking_row,
                            "date_rows": overlay_ctx["dates_by_marking_id"].get((mark_id,), []),
                            "citation_rows": overlay_ctx["citations_by_marking_id"].get((mark_id,), []),
                        }
                    )

                pairs, current_only, target_only = match_family(current_items, target_items)
                source_chunk_to_marks = defaultdict(list)

                for current_item, target_item in pairs:
                    marking = current_item["marking"]
                    target_lineage = target_item["lineage"]
                    before_snapshot = build_marking_snapshot(marking)
                    values = bundle_marking_values(overlay_ctx, db_maps, target_item["marking_row"])
                    if Marking.all_objects.filter(code=values["code"]).exclude(pk=marking.pk).exists():
                        raise CommandError(f"Cannot update marking {marking.pk}; code collision on {values['code']}")
                    recycle_entry = MarkingRecycleBin.objects.filter(marking=marking).first()
                    if recycle_entry:
                        recycle_entry.delete()
                    apply_marking_values(marking, values, actor)
                    marking.full_clean()
                    marking.save()
                    replace_marking_dates(marking, target_item["date_rows"], actor)
                    replace_marking_citations(marking, target_item["citation_rows"], actor, ascc2_ref)
                    if str(target_lineage.get("mark_kind", "") or "").strip() == "TM":
                        source_chunk_to_marks[str(target_lineage.get("source_chunk", "") or "").strip()].append(marking)
                    finalize_rows.append(
                        {
                            "marking": marking,
                            "before_snapshot": before_snapshot,
                            "action": SubmissionTransaction.ACTION_CATALOG_DIRECT_EDIT,
                        }
                    )

                for target_item in target_only:
                    values = bundle_marking_values(overlay_ctx, db_maps, target_item["marking_row"])
                    existing = None
                    if values["code"]:
                        existing = Marking.all_objects.filter(code=values["code"]).first()
                    created = existing is None
                    marking = existing or Marking(created_by=actor, modified_by=actor)
                    before_snapshot = build_marking_snapshot(marking if existing else None)
                    recycle_entry = MarkingRecycleBin.objects.filter(marking=marking).first() if existing else None
                    if recycle_entry:
                        recycle_entry.delete()
                    apply_marking_values(marking, values, actor)
                    if created:
                        marking.created_by = actor
                    marking.full_clean()
                    marking.save()
                    replace_marking_dates(marking, target_item["date_rows"], actor)
                    replace_marking_citations(marking, target_item["citation_rows"], actor, ascc2_ref)
                    if str(target_item["lineage"].get("mark_kind", "") or "").strip() == "TM":
                        source_chunk_to_marks[str(target_item["lineage"].get("source_chunk", "") or "").strip()].append(marking)
                    finalize_rows.append(
                        {
                            "marking": marking,
                            "before_snapshot": before_snapshot,
                            "action": SubmissionTransaction.ACTION_RECORD_CREATE if created else SubmissionTransaction.ACTION_CATALOG_DIRECT_EDIT,
                            "created": created,
                        }
                    )

                for map_row in include_rows:
                    raw_source_id = str(map_row.get("compare_chunk", "") or "").strip()
                    if not raw_source_id:
                        continue
                    image_rows = v1_images_by_source.get((raw_source_id,), [])
                    if not image_rows:
                        continue
                    target_chunk = raw_source_id
                    if not source_chunk_to_marks.get(target_chunk):
                        target_chunk = rep_chunk_by_source_id.get(
                            str(map_row.get("compare_representative_id", "") or "").strip(),
                            target_chunk,
                        )
                    for marking in source_chunk_to_marks.get(target_chunk, []):
                        summary["images_added"] += add_v1_images(
                            marking,
                            image_rows,
                            actor,
                            storage_root,
                            image_meta_cache,
                            skip_missing_images=bool(options["skip_missing_images"]),
                            missing_images=missing_images,
                        )

                for row in finalize_rows:
                    marking = row["marking"]
                    before_snapshot = row["before_snapshot"]
                    after_snapshot = build_marking_snapshot(marking)
                    if before_snapshot == after_snapshot:
                        continue
                    txn = log_submission_transaction(
                        action=row["action"],
                        actor=actor,
                        marking=marking,
                        source=SubmissionTransaction.SOURCE_SYSTEM,
                        before_payload=before_snapshot,
                        after_payload=after_snapshot,
                        extra_payload={
                            "workflow": "ascc2_overlay",
                            "compare_family_page": compare_family_key[0],
                            "compare_family_chunk": compare_family_key[1],
                        },
                    )
                    create_marking_version(marking, txn, actor)
                    summary["created" if row.get("created") else "updated"] += 1

                for current_item in current_only:
                    marking = current_item["marking"]
                    if MarkingRecycleBin.objects.filter(marking=marking).exists():
                        continue
                    log_marking_removed(marking, actor, "ASCC2 overlay removed obsolete ASCC1-derived marking")
                    MarkingRecycleBin.objects.create(
                        marking=marking,
                        removed_by=actor,
                        reason="ASCC2 overlay removed obsolete ASCC1-derived marking",
                    )
                    summary["removed"] += 1

            removed_base_keys = {
                family_key(row.get("base_page"), row.get("base_chunk"))
                for row in removed_family_rows
            }
            for base_family_key in sorted(removed_base_keys):
                base_rows = base_ctx["lineage_by_family"].get(base_family_key, [])
                current_codes = family_codes(base_rows)
                for marking in Marking.all_objects.filter(code__in=current_codes):
                    if MarkingRecycleBin.objects.filter(marking=marking).exists():
                        continue
                    log_marking_removed(marking, actor, "ASCC2 overlay removed obsolete ASCC1-derived marking")
                    MarkingRecycleBin.objects.create(
                        marking=marking,
                        removed_by=actor,
                        reason="ASCC2 overlay removed obsolete ASCC1-derived marking",
                    )
                    summary["removed"] += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(f"history_backfilled={summary['history_backfilled']}")
        self.stdout.write(f"updated={summary['updated']}")
        self.stdout.write(f"created={summary['created']}")
        self.stdout.write(f"removed={summary['removed']}")
        self.stdout.write(f"images_added={summary['images_added']}")
        summary["images_missing"] = len(set(missing_images))
        self.stdout.write(f"images_missing={summary['images_missing']}")
        self.stdout.write(f"post_offices_created={summary['post_offices_created']}")
        if dry_run:
            self.stdout.write("DRY RUN: transaction rolled back.")
