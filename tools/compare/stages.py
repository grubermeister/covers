"""Staged ASCC v1/v2 comparison implementation.

Each stage is intentionally small and writes a CSV plus a text summary. The
entrypoint is tools/ascc_compare.py, run from repo root; expected exit code 0.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from extract_state_cross_section import STATUS_ACTIVE, resolve_state_id, write_slice
from munger.classify import (
    RELATIONSHIP_PATTERN,
    TRAILING_VALUE_PATTERN,
    _csv_manuscript_truthy,
    detect_cross_reference,
    detect_fragment,
    detect_structural_anatomy,
)
from munger.assembly import LETTERING_SEEDS, resolve_effective_shape
from munger.fields import _split_ms_date_token, classify_all_fields, subparse_fields
from munger.fields.dates import parse_date_field
from munger.head import parse_head, parse_manuscript_row
from munger.relationships import resolve_relationships, roll_up_catalog_text
from munger.segment import (
    classify_entry_form,
    decompose_tail,
    segment_entry,
    split_paren_fields,
)
from munger.text_utils import strip_dot_leaders
from catalog_rows import canonicalize_row
from v1_to_v2_catalog_format import (
    V2_COLUMNS,
    build_image_counts,
    normalize_listing,
    write_image_refs,
)

from .manifest import read_csv, record_stage, write_csv


TOOLS_DIR = Path(__file__).resolve().parents[1]
WIP_DIR = TOOLS_DIR / "wip"
COMPARE_ROOT = WIP_DIR / "cache" / "compare"
RAW_ID = "nRawStateDataID"
RAW_TEXT = "txtRawStateData"
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
PRICE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?|---?")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
DECADE_RE = re.compile(r"^(\d{3})[-0S]S?'?$", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"^(\d{4})-\d{2}-\d{2}$")
COMPARE_FIELDS = (
    "post_office/town",
    "dates_seen",
    "colors",
    "width/height",
    "rate_val",
    "shape",
    "lettering",
    "date_fmt",
    "is_manuscript",
    "description",
)
TEXT_FIELD_COLUMNS = [RAW_ID, *COMPARE_FIELDS, "shape_source"]
DESC_PAREN_ANNOTATIONS = [
    (re.compile(r"\(backstamp\)", re.IGNORECASE), "Backstamp"),
    (re.compile(r"\(no town cds\)", re.IGNORECASE), "No town cds"),
]
SEE_PAREN_RE = re.compile(r"\(\s*(See\b[^)]*)\)", re.IGNORECASE)
SEE_BARE_RE = re.compile(r"\b(See\b[^;)]*?)(?=\s*(?:--|\.{2,}|;|$))", re.IGNORECASE)
MULTI_WS_RE = re.compile(r"\s{2,}")
BARE_REL_RE = re.compile(r"^\s*[(\[{][LE][)\]}]\s*--\s*$", re.IGNORECASE)
LETTERING_NAMES = {name.lower(): name.upper() for name in LETTERING_SEEDS}
LETTERING_ALIASES = {
    "italics": "ITALIC",
    "sans serif": "SANS-SERIF",
    "sans serifs": "SANS-SERIF",
}
DATE_FMT_CODES = {"MD", "MDD", "YD", "YMD", "YMDD"}
DATE_FMT_ALIASES = {
    "MONTHDAY": "MDD",
    "MONTHDAYBELOW": "MDD",
}


def default_paths(
    state: str,
    catalog_rows: Path | None,
    bundle_dir: Path | None,
) -> dict:
    """Return all default filesystem paths for one state run."""
    state = state.upper()
    compare_dir = COMPARE_ROOT / state
    if catalog_rows is None:
        catalog_rows = WIP_DIR / "cache" / f"{state}_catalog_rows.csv"
    return {
        "state": state,
        "compare_dir": compare_dir,
        "manifest": compare_dir / "manifest.json",
        "states": WIP_DIR / "in" / "tblStates.csv",
        "raw": WIP_DIR / "in" / "tblRawStateData.csv",
        "images": WIP_DIR / "in" / "tblTownmarkImages.csv",
        "catalog_rows": catalog_rows,
        "bundle_dir": bundle_dir or (WIP_DIR / "out" / state.lower()),
    }


def stage0_slice(paths: dict, status: str = STATUS_ACTIVE) -> Path:
    """Extract the active v1 slice and add images_count."""
    state = paths["state"]
    out = paths["compare_dir"] / f"v1_{state}_slice.csv"
    tmp = paths["compare_dir"] / f"v1_{state}_slice_raw.csv"
    paths["compare_dir"].mkdir(parents=True, exist_ok=True)
    state_id = resolve_state_id(paths["states"], state)
    stats = write_slice(paths["raw"], tmp, state_id, status=status)
    image_counts = build_image_counts(paths["images"])
    rows = read_csv(tmp)
    fields = list(rows[0].keys()) if rows else _csv_fields(tmp)
    if "images_count" not in fields:
        fields.append("images_count")
    parent_count = 0
    child_count = 0
    for row in rows:
        raw_id = (row.get(RAW_ID) or "").strip()
        row["images_count"] = str(image_counts.get(raw_id, 0))
        claimed_parent = (row.get("nRawStateDataID_parent") or "").strip()
        if claimed_parent and claimed_parent != raw_id:
            child_count += 1
        else:
            parent_count += 1
    write_csv(out, rows, fields)
    tmp.unlink(missing_ok=True)
    _write_summary(
        paths["compare_dir"] / "stage0_slice_summary.txt",
        [
            f"state: {state}",
            f"status: {status}",
            f"raw rows read: {stats.raw_rows_read}",
            f"state rows read: {stats.state_rows_read}",
            f"rows written: {len(rows)}",
            f"pending rows included: {stats.pending_rows_included}",
            f"parent rows: {parent_count}",
            f"child rows: {child_count}",
            "population:",
            *_population_lines(rows),
            "sample:",
            *_sample_lines(rows),
        ],
    )
    record_stage(paths["manifest"], "stage0_slice", [paths["states"], paths["raw"], paths["images"]], [out])
    return out


def stage1_project(paths: dict) -> list[Path]:
    """Project isolated v1 layers keyed by nRawStateDataID."""
    state = paths["state"]
    slice_path = paths["compare_dir"] / f"v1_{state}_slice.csv"
    rows = read_csv(slice_path)
    image_counts = build_image_counts(paths["images"])
    l0_rows = []
    l1_text_rows = _v1_text_field_rows(rows)
    l1_rows = []
    l2_rows = []
    family_rows = []
    source_ids = []
    for row in rows:
        raw_id = (row.get(RAW_ID) or "").strip()
        listing = normalize_listing(row.get(RAW_TEXT))
        if listing:
            source_ids.append(raw_id)
        l0_rows.append({
            "listing_text": listing,
            "catalog_page": "",
            "chunk_number": raw_id,
            "image_count": image_counts.get(raw_id, 0),
            "row_type": "LISTING",
            "is_manuscript": "",
            "default_shape": "",
        })
        l1_rows.append({
            RAW_ID: raw_id,
            "txtTown": row.get("txtTown", ""),
            "txtDatesSeen": row.get("txtDatesSeen", ""),
            "txtColors": row.get("txtColors", ""),
            "txtSizes": row.get("txtSizes", ""),
            "width": row.get("txtWidth", "") or row.get("nWidth", ""),
            "height": row.get("txtHeight", "") or row.get("nHeight", ""),
            "txtValue": row.get("txtValue", ""),
            "txtRatesText": row.get("txtRatesText", ""),
            "txtTownmarkShape": row.get("txtTownmarkShape", ""),
            "txtTownmarkLettering": row.get("txtTownmarkLettering", ""),
            "txtTownmarkDateFormat": row.get("txtTownmarkDateFormat", ""),
            "ynManuscript": row.get("ynManuscript", ""),
            "ynManuscriptTownmarks": row.get("ynManuscriptTownmarks", ""),
            "txtOther": row.get("txtOther", ""),
            "memNotes": row.get("memNotes", ""),
        })
        l2 = {RAW_ID: raw_id}
        for key, value in row.items():
            if key.startswith("txtTownmark"):
                l2[key] = value
        l2_rows.append(l2)
        family_rows.append({
            RAW_ID: raw_id,
            "nRawStateDataID_parent": row.get("nRawStateDataID_parent", ""),
            "nGroupOrder": row.get("nGroupOrder", ""),
        })
    l0 = paths["compare_dir"] / f"v1_{state}_L0_edition.csv"
    l1_text = paths["compare_dir"] / f"v1_{state}_L1_text_interpreted.csv"
    l1 = paths["compare_dir"] / f"v1_{state}_L1_parsed.csv"
    l2 = paths["compare_dir"] / f"v1_{state}_L2_classified.csv"
    fam = paths["compare_dir"] / f"v1_{state}_family_claimed.csv"
    imgs = paths["compare_dir"] / f"v1_{state}_images.csv"
    write_csv(l0, l0_rows, V2_COLUMNS)
    write_csv(l1_text, l1_text_rows, TEXT_FIELD_COLUMNS)
    write_csv(
        l1,
        l1_rows,
        [
            RAW_ID,
            "txtTown",
            "txtDatesSeen",
            "txtColors",
            "txtSizes",
            "width",
            "height",
            "txtValue",
            "txtRatesText",
            "txtTownmarkShape",
            "txtTownmarkLettering",
            "txtTownmarkDateFormat",
            "ynManuscript",
            "ynManuscriptTownmarks",
            "txtOther",
            "memNotes",
        ],
    )
    l2_fields = [RAW_ID] + sorted({k for r in l2_rows for k in r.keys() if k != RAW_ID})
    write_csv(l2, l2_rows, l2_fields)
    write_csv(fam, family_rows, [RAW_ID, "nRawStateDataID_parent", "nGroupOrder"])
    write_image_refs(paths["images"], imgs, source_ids, state)
    _write_summary(
        paths["compare_dir"] / "stage1_project_summary.txt",
        [
            f"rows: {len(rows)}",
            "L1 population:",
            *_population_lines(l1_rows),
            "L1 text-interpreted population:",
            *_population_lines(l1_text_rows),
            "L2 population:",
            *_population_lines(l2_rows),
            "sample:",
            *_sample_lines(l0_rows),
        ],
    )
    outs = [l0, l1_text, l1, l2, fam, imgs]
    record_stage(paths["manifest"], "stage1_project", [slice_path, paths["images"]], outs)
    return outs


def stage2_family(paths: dict) -> Path:
    """Detect v1 families from edition text using munger relationship logic."""
    state = paths["state"]
    slice_rows = read_csv(paths["compare_dir"] / f"v1_{state}_slice.csv")
    claimed = {r[RAW_ID]: r for r in read_csv(paths["compare_dir"] / f"v1_{state}_family_claimed.csv")}
    df = _relationship_frame(slice_rows, RAW_ID, RAW_TEXT, order_col="nOrder")
    out_rows = _family_rows(df, claimed)
    out = paths["compare_dir"] / f"family_{state}.csv"
    fields = [
        "key",
        "detected_family_id",
        "detected_parent_key",
        "group_order",
        "resolved_town",
        "rel_type",
        "claimed_family_matches_detected",
    ]
    write_csv(out, out_rows, fields)
    hist = Counter(Counter(r["detected_family_id"] for r in out_rows).values())
    mismatches = sum(1 for r in out_rows if r["claimed_family_matches_detected"] == "false")
    _write_summary(
        paths["compare_dir"] / "stage2_family_summary.txt",
        [
            f"rows: {len(out_rows)}",
            f"detected families: {len(set(r['detected_family_id'] for r in out_rows))}",
            f"claim disagreements: {mismatches}",
            f"family size histogram: {_counter_text(hist)}",
            "sample:",
            *_sample_lines(out_rows),
        ],
    )
    record_stage(paths["manifest"], "stage2_family", [paths["compare_dir"] / f"v1_{state}_slice.csv"], [out])
    return out


def stage3_align(paths: dict) -> Path:
    """Align v1 L0 edition rows against the v2 baseline cache.

    Uses family-aware two-pass alignment when family_{state}.csv is present
    (written by stage2_family). Falls back to flat text alignment otherwise.
    Family-aware alignment prevents Same/... child entries from matching
    across different parent-town families.
    """
    state = paths["state"]
    v1_path = paths["compare_dir"] / f"v1_{state}_L0_edition.csv"
    v1 = _load_side(v1_path, "chunk_number", is_v2=False)
    v2 = _load_side(paths["catalog_rows"], "", is_v2=True)
    family_path = paths["compare_dir"] / f"family_{state}.csv"
    if family_path.exists():
        v1_fam = {r["key"]: r for r in read_csv(family_path)}
        v2_fam = _v2_family_map(paths)
        out_rows = _align_rows_family_aware(v1, v2, v1_fam, v2_fam)
    else:
        out_rows = _align_rows(v1, v2)
    out = paths["compare_dir"] / f"align_{state}.csv"
    fields = [
        "v1_key",
        "v2_key",
        "disposition",
        "score",
        "match_reason",
        "representative_v1_key",
        "representative_v2_key",
        "v1_duplicate_index",
        "v2_duplicate_index",
    ]
    write_csv(out, out_rows, fields)
    counts = Counter(r["disposition"] for r in out_rows)
    _write_summary(
        paths["compare_dir"] / "stage3_align_summary.txt",
        [
            f"rows: {len(out_rows)}",
            f"dispositions: {_counter_text(counts)}",
            "lowest score matches:",
            *_sample_lines(sorted([r for r in out_rows if r["score"]], key=lambda r: float(r["score"]))[:5]),
        ],
    )
    record_stage(paths["manifest"], "stage3_align", [v1_path, paths["catalog_rows"]], [out])
    return out


def stage4_fields(paths: dict) -> Path:
    """Compare catalog-text-derived fields separately from user-entered v1 split fields."""
    state = paths["state"]
    out_dir = paths["bundle_dir"]
    required = [
        out_dir / name
        for name in (
            "markings.csv",
            "dates_seen.csv",
            "post_offices.csv",
            "colors.csv",
            "marking_lineage.csv",
            "shapes.csv",
            "letterings.csv",
        )
    ]
    for path in required:
        if not path.exists():
            raise SystemExit(f"error: Stage 4 requires {path}")
    v1_text = {r[RAW_ID]: r for r in read_csv(paths["compare_dir"] / f"v1_{state}_L1_text_interpreted.csv")}
    l1 = {r[RAW_ID]: r for r in read_csv(paths["compare_dir"] / f"v1_{state}_L1_parsed.csv")}
    slice_path = paths["compare_dir"] / f"v1_{state}_slice.csv"
    slice_rows = {r[RAW_ID]: r for r in read_csv(slice_path)} if slice_path.exists() else {}
    align = read_csv(paths["compare_dir"] / f"align_{state}.csv")
    v2_text = _catalog_text_fields_by_key(paths["catalog_rows"])
    v2 = _aggregate_v2_fields(out_dir)
    out_rows = []
    for pair in align:
        if pair["disposition"] not in ("matched", "moved", "duplicate_pair"):
            continue
        v1_key = pair["v1_key"]
        v2_key = pair["v2_key"]
        if not v1_key or not v2_key:
            continue
        catalog_fields = v1_text.get(v1_key, {})
        user_fields = _v1_field_values(l1.get(v1_key, {}), catalog_fields)
        v2_text_fields = v2_text.get(v2_key, {})
        v2_bundle_fields = v2.get(v2_key, {})
        is_relation = _is_relationship_listing(slice_rows.get(v1_key, {}).get(RAW_TEXT, ""))
        for field in COMPARE_FIELDS:
            catalog_value = catalog_fields.get(field, "")
            user_value = user_fields.get(field, "")
            v2_text_value = v2_text_fields.get(field, "")
            v2_bundle_value = v2_bundle_fields.get(field, "")
            catalog_vs_v2 = _layer_verdict(catalog_value, v2_text_value, "v1_catalog_only", "v2_catalog_only")
            user_vs_catalog = _layer_verdict(user_value, catalog_value, "user_only", "catalog_only")
            user_vs_v2 = _field_verdict(field, user_value, v2_bundle_value)
            if _is_inherited_v2_only(field, user_vs_v2, is_relation):
                user_vs_v2 = "agree"
            out_rows.append({
                "v1_key": v1_key,
                "v2_key": v2_key,
                "field": field,
                "v1_catalog_value": catalog_value,
                "v1_user_value": user_value,
                "v2_text_value": v2_text_value,
                "v2_value": v2_bundle_value,
                "v1_value": user_value,
                "catalog_vs_v2_verdict": catalog_vs_v2,
                "user_vs_catalog_verdict": user_vs_catalog,
                "user_vs_v2_verdict": user_vs_v2,
                "verdict": user_vs_v2,
            })
    out = paths["compare_dir"] / f"fields_{state}.csv"
    fields = [
        "v1_key",
        "v2_key",
        "field",
        "v1_catalog_value",
        "v1_user_value",
        "v2_text_value",
        "v2_value",
        "v1_value",
        "catalog_vs_v2_verdict",
        "user_vs_catalog_verdict",
        "user_vs_v2_verdict",
        "verdict",
    ]
    write_csv(out, out_rows, fields)
    user_catalog_counts = Counter((r["field"], r["user_vs_catalog_verdict"]) for r in out_rows)
    catalog_v2_counts = Counter((r["field"], r["catalog_vs_v2_verdict"]) for r in out_rows)
    user_v2_counts = Counter((r["field"], r["user_vs_v2_verdict"]) for r in out_rows)
    _write_summary(
        paths["compare_dir"] / "stage4_fields_summary.txt",
        [
            f"rows: {len(out_rows)}",
            f"user-vs-catalog verdicts: {_counter_text(user_catalog_counts)}",
            f"catalog-vs-v2 verdicts: {_counter_text(catalog_v2_counts)}",
            f"user-vs-v2 compatibility verdicts: {_counter_text(user_v2_counts)}",
            "sample:",
            *_sample_lines(out_rows),
        ],
    )
    record_stage(
        paths["manifest"],
        "stage4_fields",
        [
            paths["compare_dir"] / f"align_{state}.csv",
            paths["compare_dir"] / f"v1_{state}_L1_text_interpreted.csv",
            paths["compare_dir"] / f"v1_{state}_L1_parsed.csv",
            paths["catalog_rows"],
            *required,
        ],
        [out],
    )
    return out


def stage5_preservation(paths: dict) -> Path:
    """Check detected family preservation and image representation."""
    state = paths["state"]
    family = read_csv(paths["compare_dir"] / f"family_{state}.csv")
    align = read_csv(paths["compare_dir"] / f"align_{state}.csv")
    slice_rows = {r[RAW_ID]: r for r in read_csv(paths["compare_dir"] / f"v1_{state}_slice.csv")}
    v2_family_by_key = _v2_family_map(paths)
    aligned_v2 = {r["v1_key"]: r["v2_key"] for r in align if r.get("v1_key")}
    represented_v2_images = _represented_v2_image_keys(paths["bundle_dir"])
    out_rows = []
    for row in family:
        key = row["key"]
        fam_id = row["detected_family_id"]
        members = [r["key"] for r in family if r["detected_family_id"] == fam_id]
        v2_families = sorted({
            v2_family_by_key.get(aligned_v2.get(member, ""))
            for member in members
            if aligned_v2.get(member, "") and v2_family_by_key.get(aligned_v2.get(member, ""))
        })
        family_ok = len(v2_families) <= 1
        family_note = "" if family_ok else "S5:family_split"
        img_count = int((slice_rows.get(key, {}).get("images_count") or "0") or 0)
        v2_key = aligned_v2.get(key, "")
        if img_count <= 0:
            image_status = "no_v1_images"
        elif v2_key in represented_v2_images:
            image_status = "represented"
        else:
            image_status = "S5:orphaned_images"
        out_rows.append({
            "key": key,
            "family_ok": "true" if family_ok else "false",
            "family_note": family_note,
            "image_status": image_status,
        })
    out = paths["compare_dir"] / f"preservation_{state}.csv"
    write_csv(out, out_rows, ["key", "family_ok", "family_note", "image_status"])
    _write_summary(
        paths["compare_dir"] / "stage5_preservation_summary.txt",
        [
            f"rows: {len(out_rows)}",
            f"split families: {sum(1 for r in out_rows if r['family_note'])}",
            f"orphaned image rows: {sum(1 for r in out_rows if r['image_status'] == 'S5:orphaned_images')}",
            "sample:",
            *_sample_lines(out_rows),
        ],
    )
    record_stage(paths["manifest"], "stage5_preservation", [paths["compare_dir"] / f"family_{state}.csv", paths["compare_dir"] / f"align_{state}.csv"], [out])
    return out


def stage6_ledger(paths: dict) -> Path:
    """Join stages into the per-family review ledger."""
    state = paths["state"]
    slice_rows = {r[RAW_ID]: r for r in read_csv(paths["compare_dir"] / f"v1_{state}_slice.csv")}
    v2_rows = {r["key"]: r for r in _load_side(paths["catalog_rows"], "", is_v2=True)}
    family = {r["key"]: r for r in read_csv(paths["compare_dir"] / f"family_{state}.csv")}
    preservation = {r["key"]: r for r in read_csv(paths["compare_dir"] / f"preservation_{state}.csv")}
    field_reasons = _field_reasons(paths["compare_dir"] / f"fields_{state}.csv")
    out_rows = []
    for row in read_csv(paths["compare_dir"] / f"align_{state}.csv"):
        v1_key = row["v1_key"]
        if not v1_key:
            continue
        reasons = []
        if row["disposition"] in ("added", "removed", "v1_duplicate", "v2_duplicate"):
            reasons.append(f"S3:{row['disposition']}")
        reasons.extend(field_reasons.get(v1_key, []))
        fam = family.get(v1_key, {})
        if fam.get("claimed_family_matches_detected") == "false":
            reasons.append("S2:claim_disagrees")
        pres = preservation.get(v1_key, {})
        if pres.get("family_note"):
            reasons.append(pres["family_note"])
        if pres.get("image_status") == "S5:orphaned_images":
            reasons.append("S5:orphaned_images")
        reasons = sorted(set(reasons), key=_reason_sort_key)
        field_codes = field_reasons.get(v1_key, [])
        review_codes = ";".join(reasons)
        out_rows.append({
            "v1_key": v1_key,
            "v2_key": row["v2_key"],
            "family_id": fam.get("detected_family_id", ""),
            "group_order": fam.get("group_order", ""),
            "v1_listing": slice_rows.get(v1_key, {}).get(RAW_TEXT, ""),
            "v2_listing": v2_rows.get(row["v2_key"], {}).get("Listing", ""),
            "listing_check": _plain_listing_check(row["disposition"]),
            "field_issues": _plain_field_issues(field_codes),
            "edition_disposition": row["disposition"],
            "field_summary": ";".join(field_codes),
            "family_ok": pres.get("family_ok", ""),
            "image_status": pres.get("image_status", ""),
            "needs_review": "true" if reasons else "false",
            "main_review_issue": _plain_review_issue(reasons[0]) if reasons else "",
            "review_issues": _plain_review_issues(reasons),
            "primary_review_reason": reasons[0] if reasons else "",
            "review_reasons": review_codes,
        })
    out_rows.sort(key=lambda r: (r["family_id"], _int_key(r["group_order"]), _int_key(r["v1_key"])))
    out = paths["compare_dir"] / f"review_ledger_{state}.csv"
    fields = [
        "v1_key",
        "v2_key",
        "family_id",
        "group_order",
        "v1_listing",
        "v2_listing",
        "listing_check",
        "field_issues",
        "edition_disposition",
        "field_summary",
        "family_ok",
        "image_status",
        "needs_review",
        "main_review_issue",
        "review_issues",
        "primary_review_reason",
        "review_reasons",
    ]
    write_csv(out, out_rows, fields)
    reason_counts = Counter(reason for row in out_rows for reason in row["review_reasons"].split(";") if reason)
    _write_summary(
        paths["compare_dir"] / "stage6_ledger_summary.txt",
        [
            f"rows: {len(out_rows)}",
            f"needs review: {sum(1 for r in out_rows if r['needs_review'] == 'true')}",
            f"reason counts: {_counter_text(reason_counts)}",
            "sample:",
            *_sample_lines(out_rows),
        ],
    )
    record_stage(paths["manifest"], "stage6_ledger", [paths["compare_dir"] / f"align_{state}.csv"], [out])
    return out


def _csv_fields(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh).fieldnames or [])


def _write_summary(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _population_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return ["  rows: 0"]
    total = len(rows)
    lines = []
    for key in rows[0].keys():
        count = sum(1 for row in rows if str(row.get(key, "")).strip())
        pct = (count / total) * 100
        lines.append(f"  {key}: {count}/{total} ({pct:.1f}%)")
    return lines


def _sample_lines(rows: list[dict]) -> list[str]:
    return [f"  {row}" for row in rows[:5]]


def _counter_text(counter: Counter) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: str(kv[0])))


def _v1_text_field_rows(rows: list[dict]) -> list[dict]:
    interpreted = _text_fields_by_key(rows, RAW_ID, RAW_TEXT, order_col="nOrder")
    out = []
    for row in rows:
        raw_id = (row.get(RAW_ID) or "").strip()
        values = interpreted.get(raw_id, _blank_text_fields(raw_id))
        out.append(values)
    return out


def _catalog_text_fields_by_key(path: Path) -> dict:
    rows = _listing_catalog_rows(read_csv(path))
    side = _load_side(path, "", is_v2=True)
    keyed_rows = []
    for row, meta in zip(rows, side):
        work = dict(row)
        work["_compare_key"] = meta["key"]
        keyed_rows.append(work)
    return _text_fields_by_key(keyed_rows, "_compare_key", "listing_text")


def _text_fields_by_key(rows: list[dict], key_col: str, text_col: str, order_col: str | None = None) -> dict:
    df = _relationship_frame(rows, key_col, text_col, order_col=order_col)
    out = {}
    for _, row in df.iterrows():
        key = str(row.get("_key") or "")
        values = _parsed_text_field_values(row)
        values[RAW_ID] = key
        out[key] = values
    return out


def _blank_text_fields(key: str) -> dict:
    row = {RAW_ID: key}
    for field in COMPARE_FIELDS:
        row[field] = ""
    row["shape_source"] = ""
    return row


def _relationship_frame(rows: list[dict], key_col: str, text_col: str, order_col: str | None = None) -> pd.DataFrame:
    work = []
    for pos, row in enumerate(rows):
        catalog_row = canonicalize_row(row)
        text = normalize_listing(
            row.get(text_col)
            or catalog_row.get("listing_text")
            or row.get("Listing")
            or ""
        )
        if not text:
            continue
        work.append({
            "_key": row.get(key_col) or row.get("key") or "",
            "_order": _int_key(row.get(order_col or "")) if order_col else pos,
            "Listing": text,
            "Manuscript": catalog_row.get("is_manuscript", ""),
            "Default Shape": catalog_row.get("default_shape", ""),
        })
    df = pd.DataFrame(work).sort_values(["_order"]).reset_index(drop=True)
    if df.empty:
        return df
    df["clean_text"] = df["Listing"].map(strip_dot_leaders)
    df["s1_relationship"] = df["clean_text"].map(lambda t: bool(RELATIONSHIP_PATTERN.match(t)))
    df["s2_cross_ref"] = df["clean_text"].map(detect_cross_reference)
    _apply_text_description_columns(df)
    df["s3_fragment"] = df["clean_text"].map(detect_fragment)
    df["s4_trailing_value"] = df["clean_text"].map(lambda t: bool(TRAILING_VALUE_PATTERN.search(t)))
    df["s5_anatomy"] = df["clean_text"].map(lambda t: detect_structural_anatomy(t)["any"])
    df["is_manuscript_section"] = df.apply(_is_manuscript_section_row, axis=1)
    df["entry_form"] = df.apply(classify_entry_form, axis=1)
    seg = df.apply(lambda r: parse_manuscript_row(r) if r["entry_form"] == "manuscript" else segment_entry(r), axis=1)
    df = pd.concat([df, seg], axis=1)
    df["paren_fields"] = df.apply(split_paren_fields, axis=1)
    df["paren_field_types"] = df["paren_fields"].apply(classify_all_fields)
    heads = df.apply(parse_head, axis=1)
    df = pd.concat([df, heads], axis=1)
    tails = df.apply(decompose_tail, axis=1)
    df = pd.concat([df, tails], axis=1)
    parsed = df.apply(subparse_fields, axis=1)
    df = pd.concat([df, parsed], axis=1)
    _add_head_dates(df)
    df = resolve_relationships(df)
    df = roll_up_catalog_text(df)
    _inherit_relationship_attributes(df)
    _resolve_text_shapes(df)
    return df


def _apply_text_description_columns(df: pd.DataFrame) -> None:
    df["paren_annotations_desc"] = df["clean_text"].map(_paren_annotation_lines)
    see_clauses = []
    cleaned_texts = []
    for _, row in df.iterrows():
        if row["s2_cross_ref"]:
            clause, cleaned = _extract_and_strip_see(row["clean_text"])
            see_clauses.append(clause)
            if cleaned:
                if BARE_REL_RE.match(cleaned):
                    cleaned = cleaned.replace("--", "(cross-ref) --", 1)
                cleaned_texts.append(cleaned)
            else:
                cleaned_texts.append(row["clean_text"])
        else:
            see_clauses.append(None)
            cleaned_texts.append(row["clean_text"])
    df["see_clause"] = see_clauses
    df["clean_text"] = cleaned_texts
    df.loc[df["s2_cross_ref"] & df["see_clause"].notna(), "s2_cross_ref"] = False


def _paren_annotation_lines(text: str) -> list[str]:
    value = str(text or "")
    return [label for pattern, label in DESC_PAREN_ANNOTATIONS if pattern.search(value)]


def _extract_and_strip_see(text: str) -> tuple[str | None, str]:
    value = str(text or "")
    clause = None
    match = SEE_PAREN_RE.search(value)
    if match:
        clause = match.group(1).strip()
        value = SEE_PAREN_RE.sub("", value, count=1)
    else:
        match = SEE_BARE_RE.search(value)
        if match:
            clause = match.group(1).strip()
            value = SEE_BARE_RE.sub("", value, count=1)
    value = MULTI_WS_RE.sub(" ", value).strip()
    return clause, value


def _add_head_dates(df: pd.DataFrame) -> None:
    for idx in df.index:
        row = df.loc[idx]
        raw = row.get("ms_date_text") if row.get("is_manuscript_section") else row.get("head_date_text")
        tokens = _split_ms_date_token(raw)
        if not tokens:
            continue
        existing = row.get("parsed_dates")
        new_dates = list(existing) if isinstance(existing, list) else []
        for token in tokens:
            try:
                new_dates.append(parse_date_field(token))
            except Exception:
                continue
        df.at[idx, "parsed_dates"] = new_dates


def _inherit_relationship_attributes(df: pd.DataFrame) -> None:
    for pos in range(len(df)):
        row = df.iloc[pos]
        src_idx = row.get("prev_sibling_idx")
        if src_idx is None or (isinstance(src_idx, float) and pd.isna(src_idx)):
            continue
        src = df.loc[src_idx]
        child_types = set(row.get("paren_field_types") or [])
        if "ms" not in child_types and "size" not in child_types:
            if src.get("is_manuscript") != row.get("is_manuscript"):
                df.iat[pos, df.columns.get_loc("is_manuscript")] = src.get("is_manuscript")
        for column in ("parsed_colors", "parsed_sizes", "parsed_dates"):
            if row.get(column):
                continue
            source_value = src.get(column)
            if source_value:
                df.iat[pos, df.columns.get_loc(column)] = list(source_value)


def _resolve_text_shapes(df: pd.DataFrame) -> None:
    shape_resolution = df.apply(
        lambda row: pd.Series(resolve_effective_shape(row), index=["effective_shape_code", "shape_source"]),
        axis=1,
    )
    df["effective_shape_code"] = shape_resolution["effective_shape_code"]
    df["shape_source"] = shape_resolution["shape_source"]


def _is_manuscript_section_row(row) -> bool:
    return _csv_manuscript_truthy(row)


def _family_rows(df: pd.DataFrame, claimed: dict) -> list[dict]:
    rows = []
    family_seq = {}
    for pos, row in df.iterrows():
        key = str(row["_key"])
        parent_idx = row.get("parent_idx")
        parent_key = ""
        if parent_idx is None or (isinstance(parent_idx, float) and pd.isna(parent_idx)):
            root_key = key
        else:
            parent_key = str(df.loc[parent_idx, "_key"])
            root_idx = parent_idx
            while True:
                maybe = df.loc[root_idx, "parent_idx"]
                if maybe is None or (isinstance(maybe, float) and pd.isna(maybe)):
                    break
                root_idx = maybe
            root_key = str(df.loc[root_idx, "_key"])
        group_order = family_seq.get(root_key, 0) + 1
        family_seq[root_key] = group_order
        claim_parent = (claimed.get(key, {}).get("nRawStateDataID_parent") or "").strip()
        if claim_parent == key:
            claim_parent = ""
        claim_ok = (not claim_parent and not parent_key) or claim_parent == parent_key
        rows.append({
            "key": key,
            "detected_family_id": root_key,
            "detected_parent_key": parent_key,
            "group_order": group_order,
            "resolved_town": row.get("resolved_town", ""),
            "rel_type": row.get("head_rel_type", ""),
            "claimed_family_matches_detected": "true" if claim_ok else "false",
        })
    return rows


def _load_side(path: Path, key_col: str, is_v2: bool) -> list[dict]:
    rows = read_csv(path)
    if is_v2:
        rows = _listing_catalog_rows(rows)
    counts = Counter()
    out = []
    for idx, row in enumerate(rows):
        catalog_row = canonicalize_row(row)
        if is_v2:
            base = (
                f"{catalog_row.get('catalog_page', '')}:"
                f"{catalog_row.get('chunk_number', '')}"
            )
            counts[base] += 1
            key = base if counts[base] == 1 else f"{base}#{counts[base]}"
        else:
            key = row.get(key_col, "") or catalog_row.get("chunk_number", "")
        listing = catalog_row.get("listing_text", "")
        out.append({
            "key": key,
            "Listing": listing,
            "order": idx,
            "akey": akey(listing),
        })
    return out


def _listing_catalog_rows(rows: list[dict]) -> list[dict]:
    """Return catalog rows that are actual listings.

    ASCC extraction keeps META banners in catalog_rows.csv for munger context.
    The compare alignment is entry-level, so META rows must not produce added
    or removed dispositions.
    """
    return [row for row in rows if _is_listing_catalog_row(row)]


def _is_listing_catalog_row(row: dict) -> bool:
    row_type = str(canonicalize_row(row).get("row_type", "") or "").strip().upper()
    return row_type == "LISTING"


def canon(text: str) -> str:
    """Return lowercase alphanumeric-folded text for alignment."""
    return NON_ALNUM_RE.sub("", str(text).lower())


def akey(text: str) -> str:
    """Return standalone edition alignment key with normalized price tokens."""
    folded = canon(text)
    prices = "|".join(p.group(0).replace(",", "") for p in PRICE_RE.finditer(str(text)))
    return f"{folded}|{prices}"


def _match_entries(v1_subset, v2_subset):
    """Match a subset of v1 entries against v2 entries by listing text.

    Returns (matched, used_v2) where:
      matched  -- {v1_akey: (v1_rep, v2_rep, score, reason)}
      used_v2  -- set of v2 akeys that were matched
    """
    v1_groups = _groups(v1_subset)
    v2_groups = _groups(v2_subset)
    matched = {}
    used_v2 = set()
    for key, lefts in v1_groups.items():
        if key in v2_groups:
            matched[key] = (lefts[0], v2_groups[key][0], 1.0, "exact")
            used_v2.add(key)
    left_keys = [k for k in v1_groups if k not in matched]
    right_keys = [k for k in v2_groups if k not in used_v2]
    candidates = []
    for lk in left_keys:
        for rk in right_keys:
            score = SequenceMatcher(None, canon(v1_groups[lk][0]["Listing"]), canon(v2_groups[rk][0]["Listing"])).ratio()
            if score >= 0.55:
                candidates.append((score, v1_groups[lk][0]["order"], v2_groups[rk][0]["order"], lk, rk))
    for score, _, _, lk, rk in sorted(candidates, key=lambda x: (-x[0], x[1], x[2])):
        if lk in matched or rk in used_v2:
            continue
        matched[lk] = (v1_groups[lk][0], v2_groups[rk][0], score, "fuzzy")
        used_v2.add(rk)
    return matched, used_v2


def _emit_alignment_rows(v1_members, v2_members, matched):
    """Emit alignment rows for one matched group of v1 and v2 members.

    Returns (rows, emitted_v2) where emitted_v2 is the set of v2 akeys used.
    Unmatched v1 members become 'added'; unmatched v2 members become 'removed'.
    """
    v1_groups = _groups(v1_members)
    v2_groups = _groups(v2_members)
    rows = []
    emitted_v2 = set()
    for lk, group in v1_groups.items():
        if lk in matched:
            rep_v1, rep_v2, score, reason = matched[lk]
            v2_group = v2_groups[rep_v2["akey"]]
            max_len = max(len(group), len(v2_group))
            for idx in range(max_len):
                left = group[idx] if idx < len(group) else None
                right = v2_group[idx] if idx < len(v2_group) else None
                if left and right:
                    disp = "matched" if idx == 0 else "duplicate_pair"
                elif left:
                    disp = "v1_duplicate"
                else:
                    disp = "v2_duplicate"
                rows.append(_align_row(left, right, disp, score, reason, rep_v1["key"], rep_v2["key"], idx + 1, idx + 1))
            emitted_v2.add(rep_v2["akey"])
        else:
            for idx, left in enumerate(group, start=1):
                rows.append(_align_row(left, None, "added", "", "unmatched_v1", left["key"], "", idx, ""))
    for rk, group in v2_groups.items():
        if rk in emitted_v2:
            continue
        for idx, right in enumerate(group, start=1):
            rows.append(_align_row(None, right, "removed", "", "unmatched_v2", "", right["key"], "", idx))
    return rows, emitted_v2


def _align_rows(v1: list[dict], v2: list[dict]) -> list[dict]:
    """Flat text-based alignment with no family context."""
    matched, _ = _match_entries(v1, v2)
    rows, _ = _emit_alignment_rows(v1, v2, matched)
    return rows


def _align_rows_family_aware(v1, v2, v1_fam, v2_fam):
    """Two-pass family-aware alignment.

    Pass 1 matches root entries only (distinctive town names).
    Pass 2 matches children within each matched family pair.

    This prevents "Same/..." child entries -- whose listing text is nearly
    identical across families -- from matching against children of a different
    parent town.

    v1_fam: {v1_key -> row from family_{state}.csv}
              A v1 entry is a root when detected_parent_key is empty.
    v2_fam: {v2_key -> detected_family_id}  (from _v2_family_map)
              A v2 entry is a root when its key equals its detected_family_id.
    """
    # Group v1 entries by family, identify each family's root.
    v1_by_family = defaultdict(list)
    v1_root_by_family = {}
    for r in v1:
        fid = (v1_fam.get(r["key"]) or {}).get("detected_family_id") or r["key"]
        v1_by_family[fid].append(r)
        if not ((v1_fam.get(r["key"]) or {}).get("detected_parent_key") or ""):
            v1_root_by_family[fid] = r

    # Group v2 entries by family, identify each family's root.
    v2_by_family = defaultdict(list)
    v2_root_by_family = {}
    for r in v2:
        fid = v2_fam.get(r["key"]) or r["key"]
        v2_by_family[fid].append(r)
        if fid == r["key"]:
            v2_root_by_family[fid] = r

    # Pass 1: match v1 roots against v2 roots.
    root_matched, _ = _match_entries(
        list(v1_root_by_family.values()),
        list(v2_root_by_family.values()),
    )
    v1_fam_to_v2_fam = {}
    for _, (v1_rep, v2_rep, _, _) in root_matched.items():
        v1_fid = (v1_fam.get(v1_rep["key"]) or {}).get("detected_family_id") or v1_rep["key"]
        v2_fid = v2_fam.get(v2_rep["key"]) or v2_rep["key"]
        v1_fam_to_v2_fam[v1_fid] = v2_fid

    # Pass 2: align all members within each matched family pair.
    rows = []
    matched_v2_families = set()
    for v1_fid, v1_members in v1_by_family.items():
        v2_fid = v1_fam_to_v2_fam.get(v1_fid)
        v2_members = v2_by_family.get(v2_fid, []) if v2_fid else []
        fam_matched, _ = _match_entries(v1_members, v2_members)
        fam_rows, _ = _emit_alignment_rows(v1_members, v2_members, fam_matched)
        rows.extend(fam_rows)
        if v2_fid:
            matched_v2_families.add(v2_fid)

    # v2 families that no v1 family claimed are fully removed.
    for v2_fid, v2_members in v2_by_family.items():
        if v2_fid in matched_v2_families:
            continue
        for idx, right in enumerate(v2_members, start=1):
            rows.append(_align_row(None, right, "removed", "", "unmatched_v2", "", right["key"], "", idx))

    return rows


def _groups(rows: list[dict]) -> dict:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["akey"]].append(row)
    return dict(grouped)


def _align_row(left, right, disposition, score, reason, rep_v1, rep_v2, v1_dup, v2_dup) -> dict:
    return {
        "v1_key": left["key"] if left else "",
        "v2_key": right["key"] if right else "",
        "disposition": disposition,
        "score": f"{score:.3f}" if isinstance(score, float) else score,
        "match_reason": reason,
        "representative_v1_key": rep_v1,
        "representative_v2_key": rep_v2,
        "v1_duplicate_index": v1_dup,
        "v2_duplicate_index": v2_dup,
    }


def _parsed_text_field_values(row) -> dict:
    shape = row.get("effective_shape_code", "")
    shape_source = row.get("shape_source", "")
    if shape_source == "catalog_fallback":
        shape = ""
    return {
        "post_office/town": _norm_town(row.get("resolved_town", "")),
        "dates_seen": _norm_parsed_dates(row.get("parsed_dates") or []),
        "colors": _join_set(row.get("parsed_colors") or []),
        "width/height": _norm_parsed_sizes(row.get("parsed_sizes") or []),
        "rate_val": _norm_parsed_rates(row.get("parsed_rates") or []),
        "shape": _norm_shape(shape),
        "lettering": _norm_text_lettering(row),
        "date_fmt": _norm_parsed_date_fmt(row.get("parsed_sizes") or []),
        "is_manuscript": _norm_bool(row.get("is_manuscript")),
        "description": _norm_description(_text_description_lines(row)),
        "shape_source": shape_source,
    }


def _v1_field_values(row: dict, text_fields: dict | None = None) -> dict:
    size_text = " x ".join(x for x in (row.get("width", ""), row.get("height", "")) if x)
    if not size_text:
        size_text = row.get("txtSizes", "")
    description_parts = []
    if text_fields:
        description_parts.append(text_fields.get("description", ""))
    description_parts.extend([row.get("txtOther", ""), row.get("memNotes", "")])
    return {
        "post_office/town": _norm_town(row.get("txtTown", "")),
        "dates_seen": _norm_dates(row.get("txtDatesSeen", "")),
        "colors": _norm_set(row.get("txtColors", "")),
        "width/height": _norm_size(size_text),
        "rate_val": _norm_rate(row.get("txtRatesText", "")),
        "shape": _norm_shape(row.get("txtTownmarkShape", "")),
        "lettering": _norm_lettering(row.get("txtTownmarkLettering", "")),
        "date_fmt": _norm_date_fmt(row.get("txtTownmarkDateFormat", "")),
        "is_manuscript": _norm_bool(_stored_manuscript_value(row)),
        "description": _norm_description(description_parts),
    }


def _aggregate_v2_fields(out_dir: Path) -> dict:
    markings = read_csv(out_dir / "markings.csv")
    dates = read_csv(out_dir / "dates_seen.csv")
    offices = {
        r.get("code") or r.get("id", ""): r.get("name", "")
        for r in read_csv(out_dir / "post_offices.csv")
    }
    colors_by_name = {
        r.get("code") or r.get("id") or r.get("name", ""): r.get("name", "")
        for r in read_csv(out_dir / "colors.csv")
    }
    shapes_by_name = {
        r.get("id") or r.get("name", ""): r.get("name", "") or r.get("code", "")
        for r in read_csv(out_dir / "shapes.csv")
    }
    for row in read_csv(out_dir / "shapes.csv"):
        if row.get("name"):
            shapes_by_name[row["name"]] = row.get("name", "") or row.get("code", "")
    letterings_by_name = {
        r.get("id") or r.get("name", ""): r.get("name", "")
        for r in read_csv(out_dir / "letterings.csv")
    }
    for row in read_csv(out_dir / "letterings.csv"):
        if row.get("name"):
            letterings_by_name[row["name"]] = row.get("name", "")
    lineage = read_csv(out_dir / "marking_lineage.csv")
    by_marking = {r.get("code") or r.get("id", ""): r for r in markings}
    keys_by_marking = {
        r.get("marking_code") or r.get("marking_id", ""): r["v2_key"]
        for r in lineage
    }
    mids_by_key = defaultdict(list)
    for mid, key in keys_by_marking.items():
        mids_by_key[key].append(mid)
    dates_by_mid = defaultdict(list)
    for row in dates:
        dates_by_mid[row.get("subject_id", "")].append(row.get("date", ""))
    out = {}
    for key, mids in mids_by_key.items():
        town = []
        colors = []
        sizes = []
        rates = []
        shapes = []
        letterings = []
        date_fmts = []
        manuscript_values = []
        descriptions = []
        date_vals = []
        for mid in mids:
            m = by_marking.get(mid, {})
            letterings.append(letterings_by_name.get(m.get("lettering", ""), m.get("lettering", "")))
            manuscript_values.append(_norm_bool(m.get("is_manuscript", "")))
            descriptions.append(m.get("desc", ""))
            if m.get("type") == "TOWNMARK":
                town.append(offices.get(m.get("post_office", ""), ""))
                colors.append(colors_by_name.get(m.get("color", ""), m.get("color", "")))
                shapes.append(shapes_by_name.get(m.get("shape", ""), m.get("shape", "")))
                date_fmts.append(m.get("date_fmt", ""))
                if m.get("width") or m.get("height"):
                    sizes.append(f"{m.get('width', '')} x {m.get('height', '')}")
            if m.get("type") == "RATEMARK":
                rates.append(m.get("rate_val", ""))
            date_vals.extend(dates_by_mid.get(mid, []))
        out[key] = {
            "post_office/town": _norm_town(_join_set(town)),
            "dates_seen": _norm_dates(_join_set(date_vals)),
            "colors": _join_set(colors),
            "width/height": _norm_size(_join_set(sizes)),
            "rate_val": _norm_rate(_join_set(rates)),
            "shape": _join_set(_norm_shape(s) for s in shapes),
            "lettering": _join_set(_norm_lettering(s) for s in letterings),
            "date_fmt": _join_set(_norm_date_fmt(s) for s in date_fmts),
            "is_manuscript": _join_set(v for v in manuscript_values if v),
            "description": _norm_description(descriptions),
        }
    return out


def _field_verdict(field: str, left: str, right: str) -> str:
    return _layer_verdict(left, right, "v1_only", "v2_only")


def _layer_verdict(left: str, right: str, left_only: str, right_only: str) -> str:
    if left and right and left == right:
        return "agree"
    if left and right:
        return "differ"
    if left:
        return left_only
    if right:
        return right_only
    return "agree"


def _is_relationship_listing(text: str) -> bool:
    """Return true when a row inherits visible context from a prior listing."""
    clean = strip_dot_leaders(str(text or "")).strip()
    return bool(RELATIONSHIP_PATTERN.match(clean))


def _is_inherited_v2_only(field: str, verdict: str, is_relation: bool) -> bool:
    if not is_relation or verdict != "v2_only":
        return False
    return field in {"post_office/town", "dates_seen", "colors", "width/height"}


def _norm_town(text: str) -> str:
    value = _join_set([text])
    value = re.sub(r"\bC\s*\.?\s*H\b\.?", "COURT HOUSE", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def _norm_size(text: str) -> str:
    nums = [_norm_decimal(m.group(0)) for m in NUMBER_RE.finditer(str(text or ""))]
    if len(nums) == 2 and nums[0] == nums[1]:
        return nums[0]
    if nums:
        return " X ".join(nums)
    return ""


def _norm_rate(text: str) -> str:
    nums = [_norm_decimal(m.group(0)) for m in NUMBER_RE.finditer(str(text or ""))]
    if nums:
        return "|".join(sorted(set(nums), key=_rate_sort_key))
    return _norm_set(text)


def _norm_decimal(text: str) -> str:
    value = str(text or "").strip().replace(",", "")
    if "." not in value:
        return value
    value = value.rstrip("0").rstrip(".")
    return value or "0"


def _rate_sort_key(value: str) -> tuple[int, str]:
    try:
        return (int(value), value)
    except ValueError:
        return (0, value)


def _norm_parsed_dates(parsed_dates: list[dict]) -> str:
    years = []
    for item in parsed_dates:
        start = item.get("date_year_start")
        end = item.get("date_year_end")
        if start is None or (isinstance(start, float) and pd.isna(start)):
            continue
        years.append(str(int(start)))
        if end is not None and not (isinstance(end, float) and pd.isna(end)):
            end_value = str(int(end))
            if end_value != years[-1]:
                years.append(end_value)
    return _collapse_years(years)


def _norm_parsed_sizes(parsed_sizes: list[dict]) -> str:
    values = []
    for item in parsed_sizes:
        dim1 = item.get("size_dim1")
        dim2 = item.get("size_dim2")
        if dim1 is None or (isinstance(dim1, float) and pd.isna(dim1)):
            continue
        parts = [_norm_decimal(str(dim1))]
        if dim2 is not None and not (isinstance(dim2, float) and pd.isna(dim2)):
            parts.append(_norm_decimal(str(dim2)))
        values.append(" X ".join(parts))
    return _join_set(values)


def _norm_parsed_rates(parsed_rates: list) -> str:
    values = []
    for group in parsed_rates:
        tokens = group if isinstance(group, list) else [group]
        for token in tokens:
            amount = token.get("rate_amount_raw") if isinstance(token, dict) else ""
            if amount:
                values.append(amount)
    return _norm_rate("|".join(values))


def _norm_parsed_date_fmt(parsed_sizes: list[dict]) -> str:
    return _join_set(_norm_date_fmt(item.get("size_dateformat")) for item in parsed_sizes)


def _norm_text_lettering(row) -> str:
    values = []
    values.extend(_lettering_from_text(item) for item in row.get("head_annotations", []) or [])
    for group in row.get("parsed_rates", []) or []:
        tokens = group if isinstance(group, list) else [group]
        for token in tokens:
            if isinstance(token, dict):
                values.append(_lettering_from_text(token.get("rate_bracket")))
    return _join_set(_norm_lettering(value) for value in values)


def _lettering_from_text(text: str) -> str:
    value = str(text or "").strip().lower()
    if not value:
        return ""
    if value in LETTERING_ALIASES:
        return LETTERING_ALIASES[value]
    if value in LETTERING_NAMES:
        return LETTERING_NAMES[value]
    for token in re.split(r"[\s,]+", value):
        if token in LETTERING_ALIASES:
            return LETTERING_ALIASES[token]
        if token in LETTERING_NAMES:
            return LETTERING_NAMES[token]
    return ""


def _norm_lettering(text: str) -> str:
    value = str(text or "").strip().lower()
    if not value or value == "normal":
        return ""
    if value in LETTERING_ALIASES:
        return LETTERING_ALIASES[value]
    if value in LETTERING_NAMES:
        return LETTERING_NAMES[value]
    return re.sub(r"\s+", " ", value).upper()


def _norm_date_fmt(text: str) -> str:
    value = str(text or "").strip().upper()
    if not value:
        return ""
    compact = re.sub(r"[^A-Z0-9]+", "", value)
    if compact in DATE_FMT_ALIASES:
        return DATE_FMT_ALIASES[compact]
    if compact in DATE_FMT_CODES:
        return compact
    found = []
    for token in re.split(r"[^A-Z0-9]+", value):
        if token in DATE_FMT_CODES:
            found.append(token)
    return _join_set(found)


def _stored_manuscript_value(row: dict) -> str:
    if _truthy(row.get("ynManuscript", "")) or _truthy(row.get("ynManuscriptTownmarks", "")):
        return "TRUE"
    if _falsey(row.get("ynManuscript", "")) or _falsey(row.get("ynManuscriptTownmarks", "")):
        return "FALSE"
    return ""


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "t"}


def _falsey(value) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "n", "f"}


def _norm_bool(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if _truthy(value):
        return "TRUE"
    if _falsey(value):
        return "FALSE"
    return ""


def _text_description_lines(row) -> list[str]:
    lines = list(row.get("paren_annotations_desc") or [])
    see_clause = row.get("see_clause")
    if see_clause and isinstance(see_clause, str) and see_clause.strip():
        lines.append(see_clause.strip())
    raw_date = row.get("ms_date_text") if row.get("is_manuscript_section") else row.get("head_date_text")
    date_desc = _format_dates_seen_desc(raw_date)
    if date_desc:
        lines.append(date_desc)
    tail_note = row.get("tail_annotation")
    if tail_note and isinstance(tail_note, str) and tail_note.strip():
        lines.append(tail_note.strip())
    return lines


def _format_dates_seen_desc(raw_text) -> str:
    tokens = _split_ms_date_token(raw_text)
    if not tokens:
        return ""
    return "Dates Seen " + ", ".join(tokens)


def _norm_description(values) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        raw_values = values.splitlines()
    else:
        raw_values = []
        for value in values:
            raw_values.extend(str(value or "").splitlines())
    lines = []
    for value in raw_values:
        line = re.sub(r"\s+", " ", str(value or "").strip())
        if not line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _norm_shape(text: str) -> str:
    value = str(text or "").strip().upper()
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value)
    value = value.replace("SEMI CIRCLE", "SEMI-CIRCLE")
    aliases = {
        "ARC OR SEMI-CIRCLE": "ARC",
        "BOX": "BOX",
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
    if value in aliases:
        return aliases[value]
    if " - " in value:
        code = value.split(" - ", 1)[0].strip()
        if code:
            return code
    return aliases.get(value, value)


def _norm_set(text: str) -> str:
    parts = re.split(r"[|;,]+", str(text or ""))
    return _join_set(parts)


def _join_set(values) -> str:
    cleaned = sorted({str(v).strip().upper() for v in values if str(v or "").strip()})
    return "|".join(cleaned)


def _years_from_value(s: str) -> list[str]:
    value = s.strip()
    if not value:
        return []
    iso = ISO_DATE_RE.match(value)
    if iso:
        return [iso.group(1)]
    range_match = re.search(r"\b(\d{4})\s*-\s*(\d{2,4})\b", value)
    if range_match:
        start = range_match.group(1)
        end = range_match.group(2)
        if len(end) == 2:
            end = start[:2] + end
        return [start, end]
    year = re.search(r"\b(\d{4})\b", value)
    if year:
        return [year.group(1)]
    trailing_decade = re.match(r"^-(\d{3})$", value)
    if trailing_decade:
        return [f"{trailing_decade.group(1)}0s"]
    decade = DECADE_RE.match(value)
    if decade:
        return [f"{decade.group(1)}0s"]
    return []


def _norm_dates(text: str) -> str:
    """Normalize a dates_seen value to a sorted pipe-joined set of years.

    Both v1 (raw year strings like '1862') and v2 (ISO dates like '1862-01-01')
    are reduced to their 4-digit year so the comparison is format-independent.
    Tokens without a year are ignored because Stage 4 is a year-level audit.
    """
    parts = re.split(r"[|;,]+", str(text or ""))
    years = [y for p in parts if p.strip() for y in _years_from_value(p)]
    return _collapse_years(years)


def _collapse_years(years: list[str]) -> str:
    years = sorted(set(years))
    if len(years) == 2 and years[0].endswith("0") and years[1].endswith("9"):
        if years[0][:3] == years[1][:3]:
            return f"{years[0][:3]}0s"
    return "|".join(years)


def _v2_family_map(paths: dict) -> dict:
    rows = _listing_catalog_rows(read_csv(paths["catalog_rows"]))
    side = _load_side(paths["catalog_rows"], "", is_v2=True)
    for row, meta in zip(rows, side):
        row["_key"] = meta["key"]
    df = _relationship_frame(rows, "_key", "listing_text")
    return {r["key"]: r["detected_family_id"] for r in _family_rows(df, {})}


def _represented_v2_image_keys(out_dir: Path) -> set[str]:
    lineage_path = out_dir / "marking_lineage.csv"
    images_path = out_dir / "images.csv"
    if not lineage_path.exists() or not images_path.exists():
        return set()
    lineage = read_csv(lineage_path)
    key_by_mid = {
        r.get("marking_code") or r.get("marking_id", ""): r["v2_key"]
        for r in lineage
    }
    return {key_by_mid.get(r.get("subject_id", ""), "") for r in read_csv(images_path)} - {""}


def _field_reasons(path: Path) -> dict:
    reasons = defaultdict(list)
    for row in read_csv(path):
        if "user_vs_catalog_verdict" in row and "catalog_vs_v2_verdict" in row:
            _append_layer_reason(reasons, row, "user_catalog", row["user_vs_catalog_verdict"])
            _append_layer_reason(reasons, row, "catalog_v2", row["catalog_vs_v2_verdict"])
            continue
        if row["verdict"] == "agree":
            continue
        code_field = row["field"].replace("/", "_").replace(" ", "_")
        reasons[row["v1_key"]].append(f"S4:{code_field}_{row['verdict']}")
    return {k: sorted(set(v)) for k, v in reasons.items()}


def _append_layer_reason(reasons: dict, row: dict, layer: str, verdict: str) -> None:
    if verdict == "agree":
        return
    code_field = row["field"].replace("/", "_").replace(" ", "_")
    reasons[row["v1_key"]].append(f"S4:{layer}_{code_field}_{verdict}")


def _plain_listing_check(disposition: str) -> str:
    labels = {
        "matched": "same",
        "moved": "same",
        "duplicate_pair": "same duplicate",
        "added": "v1 only",
        "removed": "v2 only",
        "v1_duplicate": "extra v1 duplicate",
        "v2_duplicate": "extra v2 duplicate",
    }
    return labels.get(disposition, disposition.replace("_", " "))


def _plain_field_issues(reasons: list[str]) -> str:
    return "; ".join(_plain_review_issue(reason) for reason in reasons)


def _plain_review_issues(reasons: list[str]) -> str:
    return "; ".join(_plain_review_issue(reason) for reason in reasons)


def _plain_review_issue(reason: str) -> str:
    layered = _plain_layered_s4_issue(reason)
    if layered:
        return layered
    labels = {
        "S2:claim_disagrees": "family claim disagrees",
        "S3:added": "v1 only",
        "S3:removed": "v2 only",
        "S3:v1_duplicate": "extra v1 duplicate",
        "S3:v2_duplicate": "extra v2 duplicate",
        "S4:colors_differ": "color differs",
        "S4:colors_v1_only": "color missing from v2",
        "S4:colors_v2_only": "color missing from v1",
        "S4:dates_seen_differ": "date differs",
        "S4:dates_seen_v1_only": "date missing from v2",
        "S4:dates_seen_v2_only": "date missing from v1",
        "S4:post_office_town_differ": "town differs",
        "S4:post_office_town_v1_only": "town missing from v2",
        "S4:post_office_town_v2_only": "town missing from v1",
        "S4:rate_val_differ": "rate differs",
        "S4:rate_val_v1_only": "rate missing from v2",
        "S4:rate_val_v2_only": "rate missing from v1",
        "S4:width_height_differ": "size differs",
        "S4:width_height_v1_only": "size missing from v2",
        "S4:width_height_v2_only": "size missing from v1",
        "S5:family_split": "family split",
        "S5:orphaned_images": "images not linked",
    }
    return labels.get(reason, reason)


def _plain_layered_s4_issue(reason: str) -> str:
    prefix = "S4:"
    if not reason.startswith(prefix):
        return ""
    body = reason[len(prefix):]
    layer = ""
    for candidate in ("user_catalog", "catalog_v2"):
        layer_prefix = candidate + "_"
        if body.startswith(layer_prefix):
            layer = candidate
            body = body[len(layer_prefix):]
            break
    if not layer:
        return ""
    verdict = ""
    for candidate in ("v1_catalog_only", "v2_catalog_only", "user_only", "catalog_only", "differ"):
        suffix = "_" + candidate
        if body.endswith(suffix):
            verdict = candidate
            field_code = body[:-len(suffix)]
            break
    if not verdict:
        return ""
    field_label = {
        "colors": "color",
        "date_fmt": "date format",
        "dates_seen": "date",
        "description": "description",
        "is_manuscript": "manuscript flag",
        "lettering": "lettering",
        "post_office_town": "town",
        "rate_val": "rate",
        "shape": "shape",
        "width_height": "size",
    }.get(field_code, field_code.replace("_", " "))
    if layer == "user_catalog":
        suffix = {
            "differ": "user entry disagrees with catalog text",
            "user_only": "user-entered but not in catalog text",
            "catalog_only": "in catalog text but not user-entered",
        }.get(verdict, verdict)
        return f"{field_label} {suffix}"
    suffix = {
        "differ": "changed between old and new catalog",
        "v1_catalog_only": "in old catalog but not new",
        "v2_catalog_only": "in new catalog but not old",
    }.get(verdict, verdict)
    return f"{field_label} {suffix}"


def _reason_sort_key(reason: str) -> tuple:
    order = {"S3": 0, "S5": 1, "S4": 2, "S2": 3}
    return (order.get(reason.split(":", 1)[0], 9), reason)


def _int_key(value) -> int:
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0
