#!/usr/bin/env python3
"""Apply v1 tblRawStateData fields to a fresh munger bundle.

Run from repo root:
    PYTHONPATH=tools uv run python tools/v1_bundle_overlay.py \
        --state VA \
        --slice tools/wip/cache/v1/VA/slice.csv \
        --image-refs tools/wip/cache/v1/VA/image_refs.csv \
        --bundle-dir tools/wip/out/v1_va \
        --v1-image-root tools/wip/in/v1_images \
        --media-dir backend/media/va

Expected exit code: 0.

This script edits generated bundle CSVs before database import. It does not
read v2 OCR data and it does not update database rows directly. Pass
--preserve-images when v1_attach_images.py has already populated images.csv
and this stage should only reconcile non-image bundle tables.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import mimetypes
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image as PILImage

from munger.fields.dates import FULL_DATE_RE, parse_date_field
from munger.fields.rates import split_rate_tokens, parse_rate_token
from munger.fields.sizes import parse_size_field
from v1_to_v2_catalog_format import IMAGE_REF_COLUMNS, RAW_ID_COL
from v1_synthetic_listing import (
    color_tokens as v1_color_tokens,
    has_synthetic_listing_evidence,
    synthetic_desc_lines,
)


AUDIT_TAIL = ["created_date", "modified_date", "created_by", "modified_by"]
RAW_TEXT_COL = "txtRawStateData"
REPORT_COLUMNS = ["raw_id", "issue", "detail"]
UNSUPPORTED_COLUMNS = [
    "txtTownmarkFraming",
    "txtTownmarkRateLocation",
]
IMAGE_COLUMNS = [
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
    *AUDIT_TAIL,
]
DATE_COLUMNS = [
    "subject_type",
    "subject_id",
    "date",
    "granularity",
    *AUDIT_TAIL,
]
CITATION_COLUMNS = [
    "reference_work",
    "subject_type",
    "subject_id",
    "citation_detail",
    *AUDIT_TAIL,
]
POST_OFFICE_REGION_COLUMNS = [
    "post_office",
    "region",
    *AUDIT_TAIL,
]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return [], []
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def nonblank(value: object) -> bool:
    return str(value or "").strip() != ""


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


SAME_PREFIX_RE = re.compile(r"^\s*(?:The\s+)?Same\b", re.IGNORECASE)
LEADING_INSCRIPTION_MARKER_RE = re.compile(r"^\s*(?:\(\s*\d(?:\.\d)?\s*\))\s*")
TRAILING_INSCRIPTION_MARKER_RE = re.compile(r"\s*(?:\(\s*\d(?:\.\d)?\s*\))\s*$")
CATALOG_DATE_MARKER_RE = re.compile(r"\s*[(\[{]\s*[EL]\s*[)\]}]\s*", re.IGNORECASE)


def strip_unambiguous_star_marker(text: str) -> str:
    """Remove one boundary catalog star; preserve multi-star inscriptions."""
    if text.count("*") != 1:
        return text
    return re.sub(r"^\s*\*|\*\s*$", "", text).strip()


def strip_inscription_markers(inscription: object) -> str:
    """Remove catalog-only markers from inscription text."""
    value = CATALOG_DATE_MARKER_RE.sub(" ", clean(inscription)).strip()
    value = strip_unambiguous_star_marker(value)
    while value:
        stripped = LEADING_INSCRIPTION_MARKER_RE.sub("", value, count=1).strip()
        stripped = TRAILING_INSCRIPTION_MARKER_RE.sub("", stripped, count=1).strip()
        stripped = strip_unambiguous_star_marker(stripped)
        if stripped == value:
            return clean(stripped)
        value = stripped
    return ""


def townmark_text_stem(text: object) -> str:
    """Return the townmark text prefix before a state or device suffix."""
    value = strip_inscription_markers(text)
    if "/" in value:
        return value.split("/", 1)[0].strip()
    return re.sub(
        r"\s+[A-Za-z]{1,4}\.?$|[./]\s*[A-Za-z]{1,4}\.?$",
        "",
        value,
    ).strip() or value


def resolve_same_inscription(inscription: object, parent_text: object) -> str:
    """Replace a leading Same placeholder with parent townmark text.

    Example input shape:
    inscription="Same/Wis."
    parent_text="WATERTOWN/Wis."
    returned value="WATERTOWN/Wis."
    """
    value = strip_inscription_markers(inscription)
    if not value:
        return ""
    match = SAME_PREFIX_RE.match(value)
    if not match:
        return value
    parent = strip_inscription_markers(parent_text)
    if not parent:
        return value
    suffix = strip_inscription_markers(value[match.end():])
    if not suffix.strip():
        return parent
    sep = "" if suffix.startswith("/") else " "
    return strip_inscription_markers(townmark_text_stem(parent) + sep + suffix)


def row_town_key(raw_row: dict[str, str], townmark_text: str) -> str:
    """Return the town key used for immediate Same carry-forward."""
    town = clean(raw_row.get("txtTown")).upper()
    if town:
        return town
    return townmark_text_stem(townmark_text).upper()


def overlay_row_inscription(
    raw_row: dict[str, str],
    carry_state: dict[str, str] | None = None,
) -> str:
    """Resolve v1 row inscription using immediate previous-row carry-forward."""
    townmark_text = strip_inscription_markers(raw_row.get("txtTownPostmark"))
    postmark_text = strip_inscription_markers(raw_row.get("txtPostmark"))
    source_text = townmark_text
    town_key = row_town_key(raw_row, townmark_text)
    if SAME_PREFIX_RE.match(postmark_text):
        if (
            carry_state is not None
            and carry_state.get("town_key") == town_key
            and nonblank(carry_state.get("inscription"))
        ):
            source_text = clean(carry_state.get("inscription"))
    inscription = resolve_same_inscription(raw_row.get("txtPostmark"), source_text)
    if not inscription:
        inscription = townmark_text
    if carry_state is not None and inscription:
        carry_state["town_key"] = town_key
        carry_state["inscription"] = inscription
    return inscription


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "t"}


def falsey(value: object) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "n", "f"}


def bool_value(value: object) -> str | None:
    if truthy(value):
        return "True"
    if falsey(value):
        return "False"
    return None


def v1_manuscript_value(row: dict[str, str]) -> str | None:
    for column in ("ynManuscript", "ynManuscriptTownmarks"):
        value = bool_value(row.get(column))
        if value == "True":
            return "True"
    for column in ("ynManuscript", "ynManuscriptTownmarks"):
        value = bool_value(row.get(column))
        if value == "False":
            return "False"
    return None


def next_int(rows: list[dict[str, str]], column: str, default: int = 1) -> int:
    values = []
    for row in rows:
        try:
            values.append(int(str(row.get(column, "") or "0")))
        except ValueError:
            pass
    return (max(values) + 1) if values else default


def audit_from(rows: list[dict[str, str]]) -> dict[str, str]:
    for row in rows:
        if all(name in row for name in AUDIT_TAIL):
            return {name: row.get(name, "") for name in AUDIT_TAIL}
    now = os.environ.get("ASCC_AUDIT_TS")
    if not now:
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return {
        "created_date": now,
        "modified_date": now,
        "created_by": "1",
        "modified_by": "1",
    }


def add_report(report: list[dict[str, str]], raw_id: str, issue: str, detail: str) -> None:
    report.append({"raw_id": raw_id, "issue": issue, "detail": detail})


def read_existing_report(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    fields, rows = read_csv(path)
    if not fields:
        return []
    return [
        {
            "raw_id": row.get("raw_id", ""),
            "issue": row.get("issue", ""),
            "detail": row.get("detail", ""),
        }
        for row in rows
    ]


def load_raw_rows(slice_path: Path) -> dict[str, dict[str, str]]:
    fields, rows = read_csv(slice_path)
    if RAW_ID_COL not in fields:
        sys.exit("error: {0} has no '{1}' column".format(slice_path, RAW_ID_COL))
    return {
        clean(row.get(RAW_ID_COL)): row
        for row in rows
        if nonblank(row.get(RAW_ID_COL)) and has_synthetic_listing_evidence(row)
    }


def build_lineage_maps(lineage_rows: list[dict[str, str]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    by_raw = defaultdict(list)
    tm_by_raw = defaultdict(list)
    for row in lineage_rows:
        raw_id = clean(row.get("chunk"))
        marking_code = clean(row.get("marking_code") or row.get("marking_id"))
        if not raw_id or not marking_code:
            continue
        by_raw[raw_id].append(marking_code)
        if clean(row.get("marking_type")).upper() == "TOWNMARK":
            tm_by_raw[raw_id].append(marking_code)
    return dict(by_raw), dict(tm_by_raw)


def color_lookup(rows: list[dict[str, str]]) -> dict[str, str]:
    return {clean(row.get("name")).upper(): clean(row.get("name")).upper() for row in rows}


def ensure_color(
    name: str,
    colors: list[dict[str, str]],
    fields: list[str],
    audit: dict[str, str],
) -> str:
    lookup = color_lookup(colors)
    key = clean(name).upper()
    if key in lookup:
        return lookup[key]
    row = {name: "" for name in fields}
    row.update(audit)
    row.update({"name": key, "hex_val": "#FFFFFF", "pantone_code": ""})
    colors.append(row)
    return key


def normalized_shape_code(value: object) -> str:
    text = clean(value).upper()
    if not text:
        return ""
    text = text.replace("SEMI CIRCLE", "SEMI-CIRCLE")
    aliases = {
        "ARC OR SEMI-CIRCLE": "ARC",
        "BOX": "BOX",
        "CDS": "C",
        "CIRCLE": "C",
        "DOUBLE CIRCLE": "DC",
        "DOUBLE LINE CIRCLE": "DLC",
        "DOUBLE LINE DOUBLE CIRCLE": "DLDC",
        "DOUBLE LINE DOUBLE OVAL": "DLDO",
        "DOUBLE LINE OVAL": "DLO",
        "DOUBLE OVAL": "DO",
        "NO OUTER RIM": "NOR",
        "OCTAGON": "OCTAGON",
        "OVAL": "O",
        "SL - STRAIGHT LINE": "SL",
        "STRAIGHT LINE": "SL",
    }
    if text in aliases:
        return aliases[text]
    first = re.split(r"[^A-Z0-9]+", text)[0]
    return aliases.get(first, first)


def shape_lookup(rows: list[dict[str, str]]) -> dict[str, str]:
    out = {}
    for row in rows:
        row_name = clean(row.get("name"))
        code = normalized_shape_code(row.get("code"))
        if code:
            out[code] = row_name
        name = clean(row.get("name")).upper()
        prefix = normalized_shape_code(name.split(" - ", 1)[0])
        if prefix:
            out[prefix] = row_name
        out[normalized_shape_code(name)] = row_name
    return out


def lettering_lookup(rows: list[dict[str, str]]) -> dict[str, str]:
    aliases = {
        "ITALICS": "ITALIC",
        "SANS SERIF": "SANS-SERIF",
        "SANS SERIFS": "SANS-SERIF",
    }
    out = {}
    for row in rows:
        key = clean(row.get("name")).upper()
        out[key] = clean(row.get("name"))
    for alias, target in aliases.items():
        if target in out:
            out[alias] = out[target]
    return out


def normalized_date_fmt(value: object) -> str:
    compact = re.sub(r"[^A-Z0-9]+", "", clean(value).upper())
    aliases = {"MONTHDAY": "MDD", "MONTHDAYBELOW": "MDD", "MANUSCRIPT": ""}
    if compact in aliases:
        return aliases[compact]
    if compact in {"MD", "MDD", "YD", "YMD", "YMDD"}:
        return compact
    for token in re.split(r"[^A-Z0-9]+", clean(value).upper()):
        if token in {"MD", "MDD", "YD", "YMD", "YMDD"}:
            return token
    return ""


def decimal_text(value: object) -> str:
    text = clean(value).replace(",", "")
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return ""
    out = "{0:.2f}".format(number).rstrip("0").rstrip(".")
    return out or "0"


def parsed_dimensions(row: dict[str, str]) -> tuple[str, str]:
    width = decimal_text(row.get("nWidth") or row.get("txtWidth"))
    height = decimal_text(row.get("nHeight") or row.get("txtHeight"))
    if width or height:
        return width, height
    raw_sizes = clean(row.get("txtSizes"))
    if not raw_sizes:
        return "", ""
    for token in re.split(r"[;|]+", raw_sizes):
        token = token.strip()
        if not token:
            continue
        parsed = parse_size_field(token)
        if parsed.get("size_error"):
            continue
        dim1 = parsed.get("size_dim1")
        dim2 = parsed.get("size_dim2")
        if dim1 is None:
            continue
        width = decimal_text(dim1)
        height = decimal_text(dim2 if dim2 is not None else dim1)
        return width, height
    return "", ""


def split_date_tokens(value: object) -> list[str]:
    tokens = []
    for part in re.split(r"[;|]+", clean(value)):
        part = part.strip()
        if not part:
            continue
        if "," not in part or FULL_DATE_RE.search(part):
            tokens.append(part)
            continue
        tokens.extend(piece.strip() for piece in part.split(",") if piece.strip())
    return tokens


def parsed_date_rows(value: object, subject_ids: list[str], audit: dict[str, str]) -> list[dict[str, str]]:
    rows = []
    seen = set()
    for token in split_date_tokens(value):
        parsed = parse_date_field(token)
        if parsed.get("date_error") or parsed.get("date_granularity") == "UNKNOWN":
            continue
        observations = []
        gran = parsed.get("date_granularity")
        try:
            if gran == "DAY":
                observed = date(
                    int(parsed["date_year_start"]),
                    int(parsed["date_month"]),
                    int(parsed["date_day"]),
                )
                observations.append((str(observed), "DAY"))
            elif gran == "MONTH":
                observed = date(
                    int(parsed["date_year_start"]),
                    int(parsed["date_month"]),
                    1,
                )
                observations.append((str(observed), "MONTH"))
            elif gran == "YEAR":
                observed = date(int(parsed["date_year_start"]), 1, 1)
                observations.append((str(observed), "YEAR"))
            elif gran in {"RANGE", "DECADE"}:
                for year in (parsed.get("date_year_start"), parsed.get("date_year_end")):
                    observed = date(int(year), 1, 1)
                    observations.append((str(observed), "YEAR"))
        except (TypeError, ValueError):
            continue
        for subject_id in subject_ids:
            for date_text, granularity in observations:
                key = (subject_id, date_text, granularity)
                if key in seen:
                    continue
                seen.add(key)
                row = {
                    "subject_type": "MARKING",
                    "subject_id": subject_id,
                    "date": date_text,
                    "granularity": granularity,
                }
                row.update(audit)
                rows.append(row)
    return rows


def dedupe_date_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return date rows without duplicate subject/date/granularity tuples."""
    out = []
    seen = set()
    for row in rows:
        key = (
            clean(row.get("subject_type")),
            clean(row.get("subject_id")),
            clean(row.get("date")),
            clean(row.get("granularity")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def parsed_rate_values(value: object) -> list[str]:
    out = []
    for token in split_rate_tokens(clean(value)):
        parsed = parse_rate_token(token)
        amount = parsed.get("rate_amount_raw")
        if amount:
            norm = decimal_text(amount)
            if norm:
                out.append(norm)
    if out:
        return out
    return [decimal_text(m.group(0)) for m in re.finditer(r"\d+(?:\.\d+)?", clean(value)) if decimal_text(m.group(0))]


def append_desc(existing: object, extras: list[str]) -> str:
    lines = []
    for value in [existing, *extras]:
        for line in str(value or "").splitlines():
            line = clean(line)
            if line and line not in lines:
                lines.append(line)
    return "\n".join(lines)


def source_post_office_regions(
    post_office_id: str,
    post_office_regions: list[dict[str, str]],
    regions: list[dict[str, str]],
) -> list[str]:
    found = [
        clean(row.get("region"))
        for row in post_office_regions
        if clean(row.get("post_office")) == post_office_id and nonblank(row.get("region"))
    ]
    if found:
        return found
    for row in regions:
        if nonblank(row.get("code")):
            return [clean(row.get("code"))]
    return []


def next_post_office_code(old_code: str, post_offices: list[dict[str, str]], regions: list[dict[str, str]]) -> str:
    prefix = ""
    if "-" in old_code:
        prefix = old_code.rsplit("-", 1)[0] + "-"
    if not prefix:
        for region in regions:
            if nonblank(region.get("code")):
                prefix = clean(region.get("code")) + "-"
                break
    if not prefix:
        prefix = "V1-"
    max_serial = 0
    for row in post_offices:
        code = clean(row.get("code"))
        if not code.startswith(prefix):
            continue
        try:
            max_serial = max(max_serial, int(code[len(prefix):]))
        except ValueError:
            continue
    return "{0}{1}".format(prefix, max_serial + 1)


def ensure_post_office(
    town: str,
    old_post_office_id: str,
    post_offices: list[dict[str, str]],
    post_office_fields: list[str],
    post_office_regions: list[dict[str, str]],
    por_fields: list[str],
    regions: list[dict[str, str]],
    audit: dict[str, str],
) -> str:
    key = clean(town).upper()
    for row in post_offices:
        if clean(row.get("name")).upper() == key:
            return clean(row.get("code"))
    new_code = next_post_office_code(old_post_office_id, post_offices, regions)
    po = {name: "" for name in post_office_fields}
    po.update(audit)
    po.update({"name": key, "code": new_code})
    post_offices.append(po)
    for region_id in source_post_office_regions(old_post_office_id, post_office_regions, regions):
        por = {name: "" for name in por_fields}
        por.update(audit)
        por.update({"post_office": new_code, "region": region_id})
        post_office_regions.append(por)
    return new_code


def clone_code(base_code: str, existing_codes: set[str]) -> str:
    for serial in range(1, len(existing_codes) + 100):
        suffix = "-C{0}".format(serial)
        candidate = "{0}{1}".format(base_code[: max(1, 30 - len(suffix))], suffix)
        if candidate not in existing_codes:
            return candidate
    raise RuntimeError("could not allocate clone marking code")


def clone_townmark(
    template: dict[str, str],
    new_code: str,
    color_name: str,
    raw_id: str,
    markings_fields: list[str],
    lineage_rows: list[dict[str, str]],
) -> dict[str, str]:
    clone = {name: template.get(name, "") for name in markings_fields}
    clone["code"] = new_code
    clone["color"] = color_name
    lineage_template = None
    for row in lineage_rows:
        if clean(row.get("marking_code") or row.get("marking_id")) == clean(template.get("code")):
            lineage_template = row
            break
    if lineage_template is not None:
        lineage = dict(lineage_template)
        lineage.pop("marking_id", None)
        lineage["marking_code"] = new_code
        lineage["marking_type"] = "TOWNMARK"
        lineage["chunk"] = raw_id
        lineage_rows.append(lineage)
    return clone


def ensure_townmark_colors(
    raw_id: str,
    raw_row: dict[str, str],
    markings: list[dict[str, str]],
    markings_fields: list[str],
    lineage_rows: list[dict[str, str]],
    tm_by_raw: dict[str, list[str]],
    colors: list[dict[str, str]],
    color_fields: list[str],
    audit: dict[str, str],
    deleted_ids: set[str],
    clone_sources: dict[str, str],
    report: list[dict[str, str]],
) -> None:
    desired_names = v1_color_tokens(raw_row)
    if not desired_names:
        return
    by_code = {clean(row.get("code")): row for row in markings}
    tm_codes = [code for code in tm_by_raw.get(raw_id, []) if code in by_code]
    if not tm_codes:
        add_report(report, raw_id, "missing_townmark", "v1 colors could not be applied")
        return
    desired_color_names = [
        ensure_color(name, colors, color_fields, audit) for name in desired_names
    ]
    existing_codes = {clean(row.get("code")) for row in markings if nonblank(row.get("code"))}
    while len(tm_codes) < len(desired_color_names):
        template = by_code[tm_codes[-1]]
        new_code = clone_code(clean(template.get("code")) or "V1", existing_codes)
        existing_codes.add(new_code)
        clone = clone_townmark(
            template,
            new_code,
            desired_color_names[len(tm_codes)],
            raw_id,
            markings_fields,
            lineage_rows,
        )
        markings.append(clone)
        by_code[new_code] = clone
        tm_codes.append(new_code)
        tm_by_raw.setdefault(raw_id, []).append(new_code)
        clone_sources[new_code] = clean(template.get("code"))
    for tm_code, color_name in zip(tm_codes, desired_color_names):
        by_code[tm_code]["color"] = color_name
    for extra_code in tm_codes[len(desired_color_names):]:
        deleted_ids.add(extra_code)
        tm_by_raw[raw_id].remove(extra_code)


def apply_row_fields(
    raw_id: str,
    raw_row: dict[str, str],
    markings_by_id: dict[str, dict[str, str]],
    marking_ids: list[str],
    townmark_ids: list[str],
    ratemark_ids: list[str],
    lookups: dict[str, dict[str, str]],
    tables: dict[str, object],
    report: list[dict[str, str]],
    carry_state: dict[str, str] | None = None,
) -> None:
    townmark_rows = [markings_by_id[mid] for mid in townmark_ids if mid in markings_by_id]
    marking_rows = [markings_by_id[mid] for mid in marking_ids if mid in markings_by_id]
    if nonblank(raw_row.get("txtTown")) and marking_rows:
        first_po = clean(marking_rows[0].get("post_office"))
        new_po = ensure_post_office(
            raw_row.get("txtTown"),
            first_po,
            tables["post_offices"],
            tables["post_office_fields"],
            tables["post_office_regions"],
            tables["post_office_region_fields"],
            tables["regions"],
            tables["audit"],
        )
        for row in marking_rows:
            row["post_office"] = new_po
    inscription = overlay_row_inscription(raw_row, carry_state)
    if inscription:
        for row in townmark_rows:
            row["inscription_txt"] = inscription
    width, height = parsed_dimensions(raw_row)
    if width or height:
        for row in townmark_rows:
            if width:
                row["width"] = width
            if height:
                row["height"] = height
    shape_code = normalized_shape_code(raw_row.get("txtTownmarkShape"))
    if shape_code:
        shape_id = lookups["shapes"].get(shape_code)
        if shape_id:
            for row in townmark_rows:
                if not truthy(row.get("is_manuscript")):
                    row["shape"] = shape_id
        else:
            add_report(report, raw_id, "unknown_shape", raw_row.get("txtTownmarkShape", ""))
    lettering_key = clean(raw_row.get("txtTownmarkLettering")).upper()
    if lettering_key:
        lettering_id = lookups["letterings"].get(lettering_key)
        if lettering_id:
            for row in townmark_rows:
                if not truthy(row.get("is_manuscript")):
                    row["lettering"] = lettering_id
        else:
            add_report(report, raw_id, "unknown_lettering", raw_row.get("txtTownmarkLettering", ""))
    date_fmt = normalized_date_fmt(raw_row.get("txtTownmarkDateFormat"))
    if date_fmt:
        for row in townmark_rows:
            row["date_fmt"] = date_fmt
    manuscript = v1_manuscript_value(raw_row)
    if manuscript is not None:
        for row in townmark_rows:
            if manuscript == "True":
                row["is_manuscript"] = manuscript
                row["shape"] = ""
                row["lettering"] = ""
                row["is_irreg"] = ""
                continue
            if not clean(row.get("shape")):
                add_report(
                    report,
                    raw_id,
                    "manuscript_false_without_shape",
                    "ynManuscript is false but no handstamp shape is available",
                )
                if truthy(row.get("is_manuscript")) or ";MS" in clean(row.get("catalog_txt")).upper():
                    row["is_manuscript"] = "True"
                    row["shape"] = ""
                    row["lettering"] = ""
                    row["is_irreg"] = ""
                continue
            row["is_manuscript"] = manuscript
            if not row.get("is_irreg"):
                row["is_irreg"] = "False"
    desc_lines = [
        raw_row.get("txtOther", ""),
        raw_row.get("memNotes", ""),
        *synthetic_desc_lines(raw_row),
    ]
    if any(nonblank(value) for value in desc_lines):
        for row in townmark_rows:
            row["desc"] = append_desc(row.get("desc"), desc_lines)
    if nonblank(raw_row.get("txtRatesText")):
        rate_values = parsed_rate_values(raw_row.get("txtRatesText"))
        ratemark_rows = [markings_by_id[mid] for mid in ratemark_ids if mid in markings_by_id]
        if len(rate_values) == 1 and ratemark_rows:
            for row in ratemark_rows:
                row["rate_val"] = rate_values[0]
        elif len(rate_values) == len(ratemark_rows):
            for row, value in zip(ratemark_rows, rate_values):
                row["rate_val"] = value
        elif rate_values:
            add_report(
                report,
                raw_id,
                "rate_structure",
                "txtRatesText has {0} value(s), bundle has {1} ratemark(s)".format(
                    len(rate_values), len(ratemark_rows)
                ),
            )
    for column in UNSUPPORTED_COLUMNS:
        if nonblank(raw_row.get(column)):
            add_report(report, raw_id, "unsupported_column", column)


def rebuild_dates(
    raw_rows: dict[str, dict[str, str]],
    by_raw: dict[str, list[str]],
    dates: list[dict[str, str]],
    clone_sources: dict[str, str],
    audit: dict[str, str],
) -> list[dict[str, str]]:
    replaced_subjects = set()
    new_rows = []
    for raw_id, raw_row in raw_rows.items():
        if not nonblank(raw_row.get("txtDatesSeen")):
            continue
        subject_ids = by_raw.get(raw_id, [])
        replaced_subjects.update(subject_ids)
        new_rows.extend(parsed_date_rows(raw_row.get("txtDatesSeen"), subject_ids, audit))
    kept = [row for row in dates if clean(row.get("subject_id")) not in replaced_subjects]
    existing_by_subject = defaultdict(list)
    for row in kept:
        existing_by_subject[clean(row.get("subject_id"))].append(row)
    for new_id, source_id in clone_sources.items():
        if new_id in replaced_subjects:
            continue
        for row in existing_by_subject.get(source_id, []):
            clone = dict(row)
            clone["subject_id"] = new_id
            kept.append(clone)
    out = kept + new_rows
    for row in out:
        for key, value in audit.items():
            row.setdefault(key, value)
    return dedupe_date_rows(out)


def rebuild_citations(
    lineage_rows: list[dict[str, str]],
    reference_work: str,
    audit: dict[str, str],
) -> list[dict[str, str]]:
    rows = []
    seen = set()
    for lineage in lineage_rows:
        marking_code = clean(lineage.get("marking_code") or lineage.get("marking_id"))
        if not marking_code or marking_code in seen:
            continue
        seen.add(marking_code)
        row = {
            "reference_work": reference_work,
            "subject_type": "MARKING",
            "subject_id": marking_code,
            "citation_detail": "",
        }
        row.update(audit)
        rows.append(row)
    return rows


def resolve_image_source(root: Path, source_filename: str) -> Path | None:
    source = Path(source_filename)
    candidates = []
    if source.is_absolute():
        candidates.append(source)
    candidates.append(root / source_filename)
    candidates.append(root / source.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def build_images(
    state: str,
    image_refs: list[dict[str, str]],
    tm_by_raw: dict[str, list[str]],
    image_root: Path,
    media_dir: Path,
    allow_missing: bool,
    audit: dict[str, str],
    report: list[dict[str, str]],
) -> list[dict[str, object]]:
    media_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    display_order = defaultdict(int)
    for ref in image_refs:
        raw_id = clean(ref.get("source_row_id"))
        subject_ids = tm_by_raw.get(raw_id, [])
        if not subject_ids:
            add_report(report, raw_id, "image_without_townmark", ref.get("source_filename", ""))
            continue
        source_filename = clean(ref.get("source_filename"))
        source_path = resolve_image_source(image_root, source_filename)
        if source_path is None:
            add_report(report, raw_id, "missing_image_file", source_filename)
            if allow_missing:
                continue
            raise FileNotFoundError("missing v1 image file: {0}".format(source_filename))
        basename = source_path.name
        dest_path = media_dir / basename
        if source_path.resolve() != dest_path.resolve():
            shutil.copy2(source_path, dest_path)
        data = dest_path.read_bytes()
        with PILImage.open(dest_path) as image:
            image_width, image_height = image.size
        for subject_id in subject_ids:
            display_order[subject_id] += 1
            row = {
                "subject_type": "MARKING",
                "subject_id": subject_id,
                "original_filename": basename,
                "storage_filename": "{0}/{1}".format(state.lower(), basename),
                "file_checksum": hashlib.sha256(data).hexdigest(),
                "mime_type": mimetypes.guess_type(basename)[0] or "application/octet-stream",
                "image_width": str(image_width),
                "image_height": str(image_height),
                "file_size_bytes": str(len(data)),
                "image_view": clean(ref.get("image_view")) or "FULL",
                "image_description": clean(ref.get("image_description")),
                "is_tracing": clean(ref.get("is_tracing")) or "False",
                "display_order": str(display_order[subject_id]),
                "uploaded_by": "1",
            }
            row.update(audit)
            rows.append(row)
    return rows


def reconcile_preserved_images(
    images: list[dict[str, str]],
    deleted_ids: set[str],
    clone_sources: dict[str, str],
) -> list[dict[str, str]]:
    rows = [
        dict(row)
        for row in images
        if clean(row.get("subject_id")) not in deleted_ids
    ]
    by_subject = defaultdict(list)
    for row in rows:
        subject_id = clean(row.get("subject_id"))
        if subject_id:
            by_subject[subject_id].append(row)
    existing_subjects = set(by_subject)
    for new_id, source_id in clone_sources.items():
        if new_id in existing_subjects:
            continue
        for source_row in by_subject.get(source_id, []):
            clone = dict(source_row)
            clone["subject_id"] = new_id
            rows.append(clone)
    display_order = defaultdict(int)
    for row in rows:
        subject_id = clean(row.get("subject_id"))
        if subject_id and "display_order" in row:
            display_order[subject_id] += 1
            row["display_order"] = str(display_order[subject_id])
    return rows


def apply_overlay(args: argparse.Namespace) -> int:
    state = args.state.strip().upper()
    bundle_dir = Path(args.bundle_dir)
    raw_rows = load_raw_rows(Path(args.slice))
    image_ref_fields, image_refs = read_csv(Path(args.image_refs))
    missing_image_columns = [c for c in IMAGE_REF_COLUMNS if c not in image_ref_fields]
    if missing_image_columns:
        sys.exit("error: image refs missing columns: {0}".format(missing_image_columns))

    paths = {
        "markings": bundle_dir / "markings.csv",
        "lineage": bundle_dir / "marking_lineage.csv",
        "post_offices": bundle_dir / "post_offices.csv",
        "post_office_regions": bundle_dir / "post_office_regions.csv",
        "regions": bundle_dir / "regions.csv",
        "colors": bundle_dir / "colors.csv",
        "letterings": bundle_dir / "letterings.csv",
        "shapes": bundle_dir / "shapes.csv",
        "dates": bundle_dir / "dates_seen.csv",
        "citations": bundle_dir / "citations.csv",
        "images": bundle_dir / "images.csv",
        "reference_works": bundle_dir / "reference_works.csv",
    }
    for label, path in paths.items():
        if label == "images":
            continue
        if not path.is_file():
            sys.exit("error: missing bundle CSV: {0}".format(path))

    markings_fields, markings = read_csv(paths["markings"])
    lineage_fields, lineage_rows = read_csv(paths["lineage"])
    post_office_fields, post_offices = read_csv(paths["post_offices"])
    por_fields, post_office_regions = read_csv(paths["post_office_regions"])
    region_fields, regions = read_csv(paths["regions"])
    color_fields, colors = read_csv(paths["colors"])
    lettering_fields, letterings = read_csv(paths["letterings"])
    shape_fields, shapes = read_csv(paths["shapes"])
    date_fields, dates = read_csv(paths["dates"])
    citation_fields, citations = read_csv(paths["citations"])
    image_fields, existing_images = read_csv(paths["images"]) if paths["images"].is_file() else (IMAGE_COLUMNS, [])
    reference_work_fields, reference_works = read_csv(paths["reference_works"])
    audit = audit_from(markings or colors or post_offices)
    report = read_existing_report(Path(args.report)) if args.preserve_images else []

    by_raw, tm_by_raw = build_lineage_maps(lineage_rows)
    lookups = {
        "shapes": shape_lookup(shapes),
        "letterings": lettering_lookup(letterings),
    }
    deleted_ids = set()
    clone_sources = {}
    for raw_id, raw_row in raw_rows.items():
        if raw_id not in by_raw:
            add_report(report, raw_id, "missing_lineage", "no generated marking rows")
            continue
        ensure_townmark_colors(
            raw_id,
            raw_row,
            markings,
            markings_fields,
            lineage_rows,
            tm_by_raw,
            colors,
            color_fields,
            audit,
            deleted_ids,
            clone_sources,
            report,
        )
    if deleted_ids:
        markings = [row for row in markings if clean(row.get("code")) not in deleted_ids]
        lineage_rows = [
            row for row in lineage_rows
            if clean(row.get("marking_code") or row.get("marking_id")) not in deleted_ids
        ]
        dates = [row for row in dates if clean(row.get("subject_id")) not in deleted_ids]
        citations = [
            row for row in citations
            if clean(row.get("subject_id")) not in deleted_ids
        ]
    by_raw, tm_by_raw = build_lineage_maps(lineage_rows)

    markings_by_id = {clean(row.get("code")): row for row in markings}
    ratemark_by_raw = defaultdict(list)
    for row in lineage_rows:
        if clean(row.get("marking_type")).upper() == "RATEMARK":
            ratemark_by_raw[clean(row.get("chunk"))].append(clean(row.get("marking_code") or row.get("marking_id")))
    tables = {
        "post_offices": post_offices,
        "post_office_fields": post_office_fields,
        "post_office_regions": post_office_regions,
        "post_office_region_fields": por_fields or POST_OFFICE_REGION_COLUMNS,
        "regions": regions,
        "audit": audit,
    }
    carry_state: dict[str, str] = {}
    for raw_id, raw_row in raw_rows.items():
        apply_row_fields(
            raw_id,
            raw_row,
            markings_by_id,
            by_raw.get(raw_id, []),
            tm_by_raw.get(raw_id, []),
            ratemark_by_raw.get(raw_id, []),
            lookups,
            tables,
            report,
            carry_state,
        )

    by_raw, tm_by_raw = build_lineage_maps(lineage_rows)
    dates = rebuild_dates(raw_rows, by_raw, dates, clone_sources, audit)
    reference_work_id = ""
    if citations:
        reference_work_id = clean(citations[0].get("reference_work"))
    if not reference_work_id and reference_works:
        reference_work_id = clean(reference_works[0].get("code"))
    reference_work_id = reference_work_id or "ASCC"
    citations = rebuild_citations(lineage_rows, reference_work_id, audit)
    if args.preserve_images:
        images = reconcile_preserved_images(existing_images, deleted_ids, clone_sources)
    else:
        images = build_images(
            state,
            image_refs,
            tm_by_raw,
            Path(args.v1_image_root),
            Path(args.media_dir),
            bool(args.allow_missing_v1_images),
            audit_from(existing_images or markings),
            report,
        )

    write_csv(paths["markings"], markings_fields, markings)
    write_csv(paths["lineage"], lineage_fields, lineage_rows)
    write_csv(paths["post_offices"], post_office_fields, post_offices)
    write_csv(paths["post_office_regions"], por_fields or POST_OFFICE_REGION_COLUMNS, post_office_regions)
    write_csv(paths["colors"], color_fields, colors)
    write_csv(paths["dates"], date_fields or DATE_COLUMNS, dates)
    write_csv(paths["citations"], citation_fields or CITATION_COLUMNS, citations)
    write_csv(paths["images"], image_fields or IMAGE_COLUMNS, images)
    write_csv(Path(args.report), REPORT_COLUMNS, report)

    print("v1 overlay rows: {0}".format(len(raw_rows)))
    print("markings: {0}".format(len(markings)))
    print("dates_seen: {0}".format(len(dates)))
    print("citations: {0}".format(len(citations)))
    image_note = " (preserved)" if args.preserve_images else ""
    print("images: {0}{1}".format(len(images), image_note))
    print("report rows: {0} -> {1}".format(len(report), args.report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply v1 fields to a munger bundle.")
    parser.add_argument("--state", required=True)
    parser.add_argument("--slice", required=True)
    parser.add_argument("--image-refs", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--v1-image-root", required=True)
    parser.add_argument("--media-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--allow-missing-v1-images", action="store_true")
    parser.add_argument(
        "--preserve-images",
        action="store_true",
        help="leave existing images.csv rows untouched and reconcile fields only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    return apply_overlay(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
