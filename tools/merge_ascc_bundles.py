"""Merge per-state ASCC import bundles into one importable bundle.

Run from repo root:

    uv run python tools/merge_ascc_bundles.py \
        --out tools/wip/cutover_bundle VA=/path/to/va_bundle NC=/path/to/nc_bundle

    uv run python tools/merge_ascc_bundles.py --check tools/wip/cutover_bundle

Expected exit code: 0 when the merged bundle has internally consistent ids and
foreign keys. The command writes only under --out and refuses a non-empty output
directory.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


AUDIT_TAIL = ["created_date", "modified_date", "created_by", "modified_by"]

LOAD_ORDER = [
    "colors",
    "letterings",
    "shapes",
    "regions",
    "reference_works",
    "post_offices",
    "post_office_regions",
    "markings",
    "covers",
    "cover_valuations",
    "dates_seen",
    "cover_markings",
    "citations",
    "images",
]

OPTIONAL_STEMS = {"covers", "cover_markings", "cover_valuations"}

DEDUPE_COMPARE_IGNORED_FIELDS = {
    "regions": {"parent_region"},
}

DEFAULT_HEADERS = {
    "colors": ["id", "name", "hex_val", "pantone_code"] + AUDIT_TAIL,
    "letterings": ["id", "name"] + AUDIT_TAIL,
    "shapes": ["id", "name", "code"] + AUDIT_TAIL,
    "regions": [
        "id",
        "created_date",
        "modified_date",
        "created_by",
        "modified_by",
        "name",
        "abbrev",
        "region_tier",
        "parent_region",
        "established_date",
        "defunct_date",
    ],
    "reference_works": [
        "id",
        "created_date",
        "modified_date",
        "created_by",
        "modified_by",
        "code",
        "title",
        "authorship",
        "publisher",
        "publication_year",
        "edition",
        "volume",
        "isbn",
        "url",
    ],
    "post_offices": ["id", "name"] + AUDIT_TAIL,
    "post_office_regions": ["id", "post_office", "region"] + AUDIT_TAIL,
    "markings": [
        "id",
        "code",
        "type",
        "catalog_txt",
        "inscription_txt",
        "desc",
        "is_manuscript",
        "shape",
        "lettering",
        "color",
        "is_irreg",
        "width",
        "height",
        "date_fmt",
        "impression",
        "rate_val",
        "post_office",
    ] + AUDIT_TAIL,
    "covers": [
        "id",
        "code",
        "color",
        "type",
        "has_adhesive",
        "height",
        "is_institutional",
        "width",
    ] + AUDIT_TAIL,
    "cover_valuations": ["id", "cover", "amt", "appraisal_date"] + AUDIT_TAIL,
    "dates_seen": ["id", "subject_type", "subject_id", "date", "granularity"] + AUDIT_TAIL,
    "cover_markings": [
        "id",
        "cover",
        "marking",
        "is_backstamp",
        "placement",
        "contributor_comment",
        "review_status",
        "reviewer",
        "review_notes",
        "reviewed_at",
    ] + AUDIT_TAIL,
    "citations": [
        "id",
        "reference_work",
        "subject_type",
        "subject_id",
        "citation_detail",
    ] + AUDIT_TAIL,
    "images": [
        "image_id",
        "subject_type",
        "subject_id",
        "original_filename",
        "storage_filename",
        "file_checksum",
        "mime_type",
        "image_width",
        "image_height",
        "file_size_bytes",
        "image_view",
        "image_description",
        "is_tracing",
        "display_order",
        "uploaded_by",
    ] + AUDIT_TAIL,
}

PRIMARY_KEYS = {
    "images": "image_id",
}


class MergeError(Exception):
    """Raised when input bundles cannot be merged safely."""


@dataclass(frozen=True)
class BundleSpec:
    label: str
    path: Path


@dataclass
class BundleData:
    spec: BundleSpec
    rows: dict[str, list[dict[str, str]]]
    headers: dict[str, list[str]]


def merge_bundles(bundle_specs: list[BundleSpec], out_dir: Path) -> dict[str, int]:
    """Merge bundle_specs into out_dir and return per-stem output row counts."""

    if out_dir.exists() and any(out_dir.iterdir()):
        raise MergeError(f"Output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    bundles = [read_bundle(spec) for spec in bundle_specs]
    merged_rows, headers = merge_bundle_data(bundles)
    write_bundle(out_dir, merged_rows, headers)

    errors = check_bundle(out_dir)
    if errors:
        shutil.rmtree(out_dir)
        raise MergeError("Merged bundle failed validation:\n" + "\n".join(errors))

    return {stem: len(rows) for stem, rows in merged_rows.items()}


def read_bundle(spec: BundleSpec) -> BundleData:
    """Read one input bundle directory."""

    if not spec.path.is_dir():
        raise MergeError(f"{spec.label}: not a directory: {spec.path}")

    rows: dict[str, list[dict[str, str]]] = {}
    headers: dict[str, list[str]] = {}
    for stem in LOAD_ORDER:
        path = spec.path / f"{stem}.csv"
        if not path.exists():
            if stem in OPTIONAL_STEMS:
                rows[stem] = []
                continue
            raise MergeError(f"{spec.label}: missing required CSV: {path}")
        stem_rows, stem_header = read_csv(path)
        rows[stem] = stem_rows
        headers[stem] = stem_header
    return BundleData(spec=spec, rows=rows, headers=headers)


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return [], []
        return [dict(row) for row in reader], list(reader.fieldnames)


def write_bundle(
    out_dir: Path,
    rows_by_stem: dict[str, list[dict[str, str]]],
    headers_by_stem: dict[str, list[str]],
) -> None:
    for stem in LOAD_ORDER:
        rows = rows_by_stem.get(stem, [])
        if stem in OPTIONAL_STEMS and not rows:
            continue
        header = headers_by_stem.get(stem) or DEFAULT_HEADERS[stem]
        path = out_dir / f"{stem}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def merge_bundle_data(
    bundles: list[BundleData],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[str]]]:
    rows: dict[str, list[dict[str, str]]] = {stem: [] for stem in LOAD_ORDER}
    headers = choose_headers(bundles)
    maps: dict[str, dict[tuple[str, str], str]] = {stem: {} for stem in LOAD_ORDER}

    merge_keyed_table(bundles, rows, maps, "colors", "name", "id")
    merge_keyed_table(bundles, rows, maps, "letterings", "name", "id")
    merge_keyed_table(bundles, rows, maps, "shapes", "name", "id")
    merge_keyed_table(bundles, rows, maps, "reference_works", "code", "id")
    merge_keyed_table(bundles, rows, maps, "regions", "name", "id")
    rewrite_region_parents(rows["regions"], bundles, maps)
    merge_post_offices_and_regions(bundles, rows, maps)
    merge_markings(bundles, rows, maps)
    merge_covers(bundles, rows, maps)
    merge_simple_fk_table(bundles, rows, maps, "cover_valuations", "id", {"cover": "covers"})
    merge_polymorphic_table(bundles, rows, maps, "dates_seen", "id")
    merge_simple_fk_table(
        bundles,
        rows,
        maps,
        "cover_markings",
        "id",
        {"cover": "covers", "marking": "markings"},
    )
    merge_polymorphic_table(
        bundles,
        rows,
        maps,
        "citations",
        "id",
        extra_fk_fields={"reference_work": "reference_works"},
    )
    merge_polymorphic_table(bundles, rows, maps, "images", "image_id")
    assert_unique_marking_codes(rows["markings"])
    assert_storage_filenames_do_not_cross_labels(bundles)
    return rows, headers


def choose_headers(bundles: list[BundleData]) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    for stem in LOAD_ORDER:
        header = next(
            (bundle.headers[stem] for bundle in bundles if stem in bundle.headers),
            DEFAULT_HEADERS[stem],
        )
        headers[stem] = extend_header(header, rows_for_stem(bundles, stem))
    return headers


def extend_header(header: list[str], rows: Iterable[dict[str, str]]) -> list[str]:
    out = list(header)
    seen = set(out)
    for row in rows:
        for key in row:
            if key not in seen:
                out.append(key)
                seen.add(key)
    return out


def rows_for_stem(bundles: list[BundleData], stem: str) -> Iterable[dict[str, str]]:
    for bundle in bundles:
        yield from bundle.rows.get(stem, [])


def merge_keyed_table(
    bundles: list[BundleData],
    rows: dict[str, list[dict[str, str]]],
    maps: dict[str, dict[tuple[str, str], str]],
    stem: str,
    key_field: str,
    id_field: str,
) -> None:
    seen: dict[str, str] = {}
    row_by_key: dict[str, dict[str, str]] = {}
    for bundle in bundles:
        for row in bundle.rows[stem]:
            old_id = require_id(row, id_field, stem, bundle.spec.label)
            key = clean_key(row.get(key_field, ""))
            if not key:
                raise MergeError(f"{bundle.spec.label}:{stem}: blank {key_field}")
            if key not in seen:
                new_id = str(len(rows[stem]) + 1)
                seen[key] = new_id
                out = dict(row)
                out[id_field] = new_id
                rows[stem].append(out)
                row_by_key[key] = out
            else:
                assert_dedupe_compatible(
                    stem,
                    id_field,
                    key_field,
                    row_by_key[key],
                    row,
                    bundle.spec.label,
                )
            maps[stem][(bundle.spec.label, old_id)] = seen[key]


def rewrite_region_parents(
    region_rows: list[dict[str, str]],
    bundles: list[BundleData],
    maps: dict[str, dict[tuple[str, str], str]],
) -> None:
    by_new_id = {row["id"]: row for row in region_rows}
    for bundle in bundles:
        for old_row in bundle.rows["regions"]:
            old_id = require_id(old_row, "id", "regions", bundle.spec.label)
            new_id = maps["regions"][(bundle.spec.label, old_id)]
            old_parent = blank_to_none(old_row.get("parent_region"))
            if old_parent is None:
                continue
            parent_id = maps["regions"].get((bundle.spec.label, old_parent))
            if parent_id is None:
                raise MergeError(
                    f"{bundle.spec.label}:regions {old_id} parent {old_parent} is missing"
                )
            by_new_id[new_id]["parent_region"] = parent_id


def merge_post_offices_and_regions(
    bundles: list[BundleData],
    rows: dict[str, list[dict[str, str]]],
    maps: dict[str, dict[tuple[str, str], str]],
) -> None:
    post_office_seen: dict[tuple[str, tuple[str, ...]], str] = {}
    link_seen: dict[tuple[str, str], str] = {}

    for bundle in bundles:
        old_po_regions = old_regions_by_post_office(bundle)
        for row in bundle.rows["post_offices"]:
            old_id = require_id(row, "id", "post_offices", bundle.spec.label)
            old_region_ids = old_po_regions.get(old_id, [])
            new_region_ids = tuple(
                sorted(maps["regions"][(bundle.spec.label, region_id)] for region_id in old_region_ids)
            )
            key = (clean_key(row.get("name", "")), new_region_ids)
            if not key[0]:
                raise MergeError(f"{bundle.spec.label}:post_offices: blank name")
            if key not in post_office_seen:
                new_id = str(len(rows["post_offices"]) + 1)
                post_office_seen[key] = new_id
                out = dict(row)
                out["id"] = new_id
                rows["post_offices"].append(out)
            maps["post_offices"][(bundle.spec.label, old_id)] = post_office_seen[key]

        for row in bundle.rows["post_office_regions"]:
            old_id = require_id(row, "id", "post_office_regions", bundle.spec.label)
            post_office = remap_required(
                maps,
                "post_offices",
                bundle.spec.label,
                row.get("post_office"),
                "post_office_regions.post_office",
            )
            region = remap_required(
                maps,
                "regions",
                bundle.spec.label,
                row.get("region"),
                "post_office_regions.region",
            )
            key = (post_office, region)
            if key not in link_seen:
                new_id = str(len(rows["post_office_regions"]) + 1)
                link_seen[key] = new_id
                out = dict(row)
                out["id"] = new_id
                out["post_office"] = post_office
                out["region"] = region
                rows["post_office_regions"].append(out)
            maps["post_office_regions"][(bundle.spec.label, old_id)] = link_seen[key]


def old_regions_by_post_office(bundle: BundleData) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in bundle.rows["post_office_regions"]:
        post_office = require_id(row, "post_office", "post_office_regions", bundle.spec.label)
        region = require_id(row, "region", "post_office_regions", bundle.spec.label)
        out.setdefault(post_office, []).append(region)
    return out


def merge_markings(
    bundles: list[BundleData],
    rows: dict[str, list[dict[str, str]]],
    maps: dict[str, dict[tuple[str, str], str]],
) -> None:
    for bundle in bundles:
        for row in bundle.rows["markings"]:
            old_id = require_id(row, "id", "markings", bundle.spec.label)
            out = dict(row)
            out["id"] = str(len(rows["markings"]) + 1)
            out["shape"] = remap_optional(maps, "shapes", bundle.spec.label, row.get("shape"))
            out["lettering"] = remap_optional(
                maps,
                "letterings",
                bundle.spec.label,
                row.get("lettering"),
            )
            out["color"] = remap_required(
                maps,
                "colors",
                bundle.spec.label,
                row.get("color"),
                "markings.color",
            )
            out["post_office"] = remap_required(
                maps,
                "post_offices",
                bundle.spec.label,
                row.get("post_office"),
                "markings.post_office",
            )
            maps["markings"][(bundle.spec.label, old_id)] = out["id"]
            rows["markings"].append(out)


def merge_covers(
    bundles: list[BundleData],
    rows: dict[str, list[dict[str, str]]],
    maps: dict[str, dict[tuple[str, str], str]],
) -> None:
    for bundle in bundles:
        for row in bundle.rows["covers"]:
            old_id = require_id(row, "id", "covers", bundle.spec.label)
            out = dict(row)
            out["id"] = str(len(rows["covers"]) + 1)
            out["color"] = remap_optional(maps, "colors", bundle.spec.label, row.get("color"))
            maps["covers"][(bundle.spec.label, old_id)] = out["id"]
            rows["covers"].append(out)


def merge_simple_fk_table(
    bundles: list[BundleData],
    rows: dict[str, list[dict[str, str]]],
    maps: dict[str, dict[tuple[str, str], str]],
    stem: str,
    id_field: str,
    fk_fields: dict[str, str],
) -> None:
    for bundle in bundles:
        for row in bundle.rows[stem]:
            old_id = require_id(row, id_field, stem, bundle.spec.label)
            out = dict(row)
            out[id_field] = str(len(rows[stem]) + 1)
            for field, target_stem in fk_fields.items():
                out[field] = remap_required(
                    maps,
                    target_stem,
                    bundle.spec.label,
                    row.get(field),
                    f"{stem}.{field}",
                )
            maps[stem][(bundle.spec.label, old_id)] = out[id_field]
            rows[stem].append(out)


def merge_polymorphic_table(
    bundles: list[BundleData],
    rows: dict[str, list[dict[str, str]]],
    maps: dict[str, dict[tuple[str, str], str]],
    stem: str,
    id_field: str,
    extra_fk_fields: dict[str, str] | None = None,
) -> None:
    extra_fk_fields = extra_fk_fields or {}
    for bundle in bundles:
        for row in bundle.rows[stem]:
            old_id = require_id(row, id_field, stem, bundle.spec.label)
            out = dict(row)
            out[id_field] = str(len(rows[stem]) + 1)
            out["subject_id"] = remap_subject_id(
                maps,
                bundle.spec.label,
                row.get("subject_type", ""),
                row.get("subject_id"),
                stem,
            )
            for field, target_stem in extra_fk_fields.items():
                out[field] = remap_required(
                    maps,
                    target_stem,
                    bundle.spec.label,
                    row.get(field),
                    f"{stem}.{field}",
                )
            maps[stem][(bundle.spec.label, old_id)] = out[id_field]
            rows[stem].append(out)


def remap_subject_id(
    maps: dict[str, dict[tuple[str, str], str]],
    label: str,
    subject_type: str,
    old_id: str | None,
    stem: str,
) -> str:
    if subject_type == "MARKING":
        return remap_required(maps, "markings", label, old_id, f"{stem}.subject_id")
    if subject_type == "COVER":
        return remap_required(maps, "covers", label, old_id, f"{stem}.subject_id")
    raise MergeError(f"{label}:{stem}: unknown subject_type {subject_type!r}")


def remap_required(
    maps: dict[str, dict[tuple[str, str], str]],
    target_stem: str,
    label: str,
    old_id: str | None,
    context: str,
) -> str:
    value = require_id_value(old_id, context, label)
    try:
        return maps[target_stem][(label, value)]
    except KeyError as exc:
        raise MergeError(f"{label}:{context}: id {value} has no target row") from exc


def remap_optional(
    maps: dict[str, dict[tuple[str, str], str]],
    target_stem: str,
    label: str,
    old_id: str | None,
) -> str:
    value = blank_to_none(old_id)
    if value is None:
        return ""
    try:
        return maps[target_stem][(label, value)]
    except KeyError as exc:
        raise MergeError(f"{label}:{target_stem}: optional id {value} has no target row") from exc


def assert_unique_marking_codes(marking_rows: list[dict[str, str]]) -> None:
    seen: dict[str, str] = {}
    for row in marking_rows:
        code = row.get("code", "").strip()
        if not code:
            continue
        if code in seen:
            raise MergeError(f"Duplicate Marking.code after merge: {code}")
        seen[code] = row["id"]


def assert_storage_filenames_do_not_cross_labels(bundles: list[BundleData]) -> None:
    labels_by_name: dict[str, set[str]] = {}
    for bundle in bundles:
        for row in bundle.rows["images"]:
            name = row.get("storage_filename", "").strip()
            if name:
                labels_by_name.setdefault(name, set()).add(bundle.spec.label)
    collisions = [
        name for name, labels in labels_by_name.items() if len(labels) > 1
    ]
    if collisions:
        head = ", ".join(sorted(collisions)[:5])
        raise MergeError(f"Image storage_filename crosses bundles: {head}")


def assert_dedupe_compatible(
    stem: str,
    id_field: str,
    key_field: str,
    existing: dict[str, str],
    candidate: dict[str, str],
    label: str,
) -> None:
    ignored = {id_field, key_field, *AUDIT_TAIL}
    ignored.update(DEDUPE_COMPARE_IGNORED_FIELDS.get(stem, set()))
    fields = sorted((set(existing) | set(candidate)) - ignored)
    conflicts = [
        field
        for field in fields
        if normalize_cell(existing.get(field)) != normalize_cell(candidate.get(field))
    ]
    if conflicts:
        joined = ", ".join(conflicts[:5])
        key_value = candidate.get(key_field, "")
        raise MergeError(
            f"{label}:{stem}: duplicate {key_field} {key_value!r} "
            f"conflicts on {joined}"
        )


def check_bundle(bundle_dir: Path) -> list[str]:
    """Validate id uniqueness and FK integrity for an existing bundle."""

    errors: list[str] = []
    rows: dict[str, list[dict[str, str]]] = {}
    for stem in LOAD_ORDER:
        path = bundle_dir / f"{stem}.csv"
        if not path.exists():
            if stem not in OPTIONAL_STEMS:
                errors.append(f"missing required CSV: {path}")
            rows[stem] = []
            continue
        stem_rows, _ = read_csv(path)
        rows[stem] = stem_rows

    id_sets = collect_id_sets(rows, errors)
    check_region_parents(rows, id_sets, errors)
    check_fk(rows, id_sets, errors, "post_office_regions", "post_office", "post_offices")
    check_fk(rows, id_sets, errors, "post_office_regions", "region", "regions")
    check_fk(rows, id_sets, errors, "markings", "shape", "shapes", optional=True)
    check_fk(rows, id_sets, errors, "markings", "lettering", "letterings", optional=True)
    check_fk(rows, id_sets, errors, "markings", "color", "colors")
    check_fk(rows, id_sets, errors, "markings", "post_office", "post_offices")
    check_fk(rows, id_sets, errors, "covers", "color", "colors", optional=True)
    check_fk(rows, id_sets, errors, "cover_valuations", "cover", "covers")
    check_fk(rows, id_sets, errors, "cover_markings", "cover", "covers")
    check_fk(rows, id_sets, errors, "cover_markings", "marking", "markings")
    check_fk(rows, id_sets, errors, "citations", "reference_work", "reference_works")
    check_polymorphic(rows, id_sets, errors, "dates_seen")
    check_polymorphic(rows, id_sets, errors, "citations")
    check_polymorphic(rows, id_sets, errors, "images")
    check_unique_values(rows["colors"], "colors", "name", errors)
    check_unique_values(rows["letterings"], "letterings", "name", errors)
    check_unique_values(rows["shapes"], "shapes", "name", errors)
    check_unique_values(rows["reference_works"], "reference_works", "code", errors)
    check_unique_values(rows["regions"], "regions", "name", errors)
    check_unique_tuple(
        rows["post_office_regions"],
        "post_office_regions",
        ("post_office", "region"),
        errors,
    )
    check_unique_tuple(
        rows["cover_markings"],
        "cover_markings",
        ("cover", "marking"),
        errors,
    )
    check_unique_values(rows["markings"], "markings", "code", errors, allow_blank=True)
    check_storage_filename_checksum_conflicts(rows["images"], errors)
    return errors


def collect_id_sets(
    rows: dict[str, list[dict[str, str]]],
    errors: list[str],
) -> dict[str, set[str]]:
    id_sets: dict[str, set[str]] = {}
    for stem, stem_rows in rows.items():
        id_field = id_field_for(stem)
        seen: set[str] = set()
        for row in stem_rows:
            value = row.get(id_field, "").strip()
            if not value:
                errors.append(f"{stem}: blank {id_field}")
                continue
            if value in seen:
                errors.append(f"{stem}: duplicate {id_field} {value}")
            seen.add(value)
        id_sets[stem] = seen
    return id_sets


def check_region_parents(
    rows: dict[str, list[dict[str, str]]],
    id_sets: dict[str, set[str]],
    errors: list[str],
) -> None:
    for row in rows["regions"]:
        parent = blank_to_none(row.get("parent_region"))
        if parent is not None and parent not in id_sets["regions"]:
            errors.append(f"regions {row.get('id')}: parent_region {parent} is missing")


def check_fk(
    rows: dict[str, list[dict[str, str]]],
    id_sets: dict[str, set[str]],
    errors: list[str],
    stem: str,
    field: str,
    target_stem: str,
    *,
    optional: bool = False,
) -> None:
    for row in rows[stem]:
        value = blank_to_none(row.get(field))
        if value is None:
            if not optional:
                errors.append(f"{stem} {row.get(id_field_for(stem))}: blank {field}")
            continue
        if value not in id_sets[target_stem]:
            errors.append(
                f"{stem} {row.get(id_field_for(stem))}: {field} {value} "
                f"is missing from {target_stem}"
            )


def check_polymorphic(
    rows: dict[str, list[dict[str, str]]],
    id_sets: dict[str, set[str]],
    errors: list[str],
    stem: str,
) -> None:
    for row in rows[stem]:
        row_id = row.get(id_field_for(stem))
        subject_type = row.get("subject_type")
        subject_id = blank_to_none(row.get("subject_id"))
        if subject_type == "MARKING":
            target_stem = "markings"
        elif subject_type == "COVER":
            target_stem = "covers"
        else:
            errors.append(f"{stem} {row_id}: unknown subject_type {subject_type!r}")
            continue
        if subject_id is None or subject_id not in id_sets[target_stem]:
            errors.append(
                f"{stem} {row_id}: subject_id {subject_id} is missing from {target_stem}"
            )


def check_unique_values(
    rows: list[dict[str, str]],
    stem: str,
    field: str,
    errors: list[str],
    *,
    allow_blank: bool = False,
) -> None:
    seen: set[str] = set()
    for row in rows:
        display_value = normalize_cell(row.get(field))
        value = clean_key(display_value)
        if not value and allow_blank:
            continue
        if value in seen:
            errors.append(f"{stem}: duplicate {field} {display_value}")
        seen.add(value)


def check_unique_tuple(
    rows: list[dict[str, str]],
    stem: str,
    fields: tuple[str, ...],
    errors: list[str],
) -> None:
    seen: set[tuple[str, ...]] = set()
    id_field = id_field_for(stem)
    for row in rows:
        value = tuple(clean_key(row.get(field)) for field in fields)
        if any(not part for part in value):
            continue
        if value in seen:
            label = ",".join(f"{field}={row.get(field)}" for field in fields)
            errors.append(f"{stem}: duplicate {label}")
        seen.add(value)


def check_storage_filename_checksum_conflicts(
    rows: list[dict[str, str]],
    errors: list[str],
) -> None:
    checksums_by_name: dict[str, set[str]] = {}
    for row in rows:
        storage_filename = row.get("storage_filename", "").strip()
        if not storage_filename:
            errors.append(f"images {row.get('image_id')}: blank storage_filename")
            continue
        checksum = row.get("file_checksum", "").strip()
        checksums_by_name.setdefault(storage_filename, set()).add(checksum)

    for storage_filename, checksums in sorted(checksums_by_name.items()):
        if len(checksums) > 1:
            errors.append(
                "images: storage_filename "
                f"{storage_filename} has conflicting file_checksum values"
            )


def parse_bundle_spec(raw: str) -> BundleSpec:
    if "=" not in raw:
        raise MergeError(f"Bundle spec must be LABEL=DIR: {raw}")
    label, path = raw.split("=", 1)
    label = label.strip().upper()
    if not label:
        raise MergeError(f"Bundle spec has blank label: {raw}")
    return BundleSpec(label=label, path=Path(path))


def require_id(row: dict[str, str], field: str, stem: str, label: str) -> str:
    return require_id_value(row.get(field), f"{stem}.{field}", label)


def require_id_value(value: str | None, context: str, label: str) -> str:
    cleaned = blank_to_none(value)
    if cleaned is None:
        raise MergeError(f"{label}:{context}: blank id")
    return cleaned


def blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def clean_key(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def normalize_cell(value: str | None) -> str:
    return str(value or "").strip()


def id_field_for(stem: str) -> str:
    return PRIMARY_KEYS.get(stem, "id")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge or validate ASCC import bundles."
    )
    parser.add_argument(
        "bundles",
        nargs="*",
        help="Input bundle specs as LABEL=DIR, required with --out.",
    )
    parser.add_argument("--out", help="Output bundle directory.")
    parser.add_argument("--check", help="Validate an existing bundle directory.")
    args = parser.parse_args(argv)
    if bool(args.out) == bool(args.check):
        parser.error("Specify exactly one of --out or --check.")
    if args.out and not args.bundles:
        parser.error("--out requires at least one LABEL=DIR bundle spec.")
    if args.check and args.bundles:
        parser.error("--check does not accept LABEL=DIR bundle specs.")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.check:
            errors = check_bundle(Path(args.check))
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            print(f"OK: {args.check}")
            return 0

        specs = [parse_bundle_spec(raw) for raw in args.bundles]
        counts = merge_bundles(specs, Path(args.out))
        for stem in LOAD_ORDER:
            if stem in counts:
                print(f"{stem}.csv: {counts[stem]} rows")
        return 0
    except MergeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
