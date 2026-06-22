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
    l1 = paths["compare_dir"] / f"v1_{state}_L1_parsed.csv"
    l2 = paths["compare_dir"] / f"v1_{state}_L2_classified.csv"
    fam = paths["compare_dir"] / f"v1_{state}_family_claimed.csv"
    imgs = paths["compare_dir"] / f"v1_{state}_images.csv"
    write_csv(l0, l0_rows, V2_COLUMNS)
    write_csv(l1, l1_rows, [RAW_ID, "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText"])
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
            "L2 population:",
            *_population_lines(l2_rows),
            "sample:",
            *_sample_lines(l0_rows),
        ],
    )
    outs = [l0, l1, l2, fam, imgs]
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
    """Align v1 L0 edition rows against the v2 baseline cache."""
    state = paths["state"]
    v1_path = paths["compare_dir"] / f"v1_{state}_L0_edition.csv"
    v1 = _load_side(v1_path, "chunk_number", is_v2=False)
    v2 = _load_side(paths["catalog_rows"], "", is_v2=True)
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
    """Compare matched v1 L1 fields against aggregated v2 munger output."""
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
        )
    ]
    for path in required:
        if not path.exists():
            raise SystemExit(f"error: Stage 4 requires {path}")
    l1 = {r[RAW_ID]: r for r in read_csv(paths["compare_dir"] / f"v1_{state}_L1_parsed.csv")}
    align = read_csv(paths["compare_dir"] / f"align_{state}.csv")
    v2 = _aggregate_v2_fields(out_dir)
    out_rows = []
    for pair in align:
        if pair["disposition"] not in ("matched", "moved", "duplicate_pair"):
            continue
        v1_key = pair["v1_key"]
        v2_key = pair["v2_key"]
        if not v1_key or not v2_key:
            continue
        v1_fields = _v1_field_values(l1.get(v1_key, {}))
        v2_fields = v2.get(v2_key, {})
        for field in ("post_office/town", "dates_seen", "colors", "width/height", "rate_val"):
            left = v1_fields.get(field, "")
            right = v2_fields.get(field, "")
            out_rows.append({
                "v1_key": v1_key,
                "v2_key": v2_key,
                "field": field,
                "v1_value": left,
                "v2_value": right,
                "verdict": _field_verdict(left, right),
            })
    out = paths["compare_dir"] / f"fields_{state}.csv"
    fields = ["v1_key", "v2_key", "field", "v1_value", "v2_value", "verdict"]
    write_csv(out, out_rows, fields)
    by_field = Counter((r["field"], r["verdict"]) for r in out_rows)
    _write_summary(
        paths["compare_dir"] / "stage4_fields_summary.txt",
        [
            f"rows: {len(out_rows)}",
            f"field verdicts: {_counter_text(by_field)}",
            "sample:",
            *_sample_lines(out_rows),
        ],
    )
    record_stage(paths["manifest"], "stage4_fields", [paths["compare_dir"] / f"align_{state}.csv", *required], [out])
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
        out_rows.append({
            "v1_key": v1_key,
            "v2_key": row["v2_key"],
            "family_id": fam.get("detected_family_id", ""),
            "group_order": fam.get("group_order", ""),
            "v1_listing": slice_rows.get(v1_key, {}).get(RAW_TEXT, ""),
            "v2_listing": v2_rows.get(row["v2_key"], {}).get("Listing", ""),
            "edition_disposition": row["disposition"],
            "field_summary": ";".join(field_reasons.get(v1_key, [])),
            "family_ok": pres.get("family_ok", ""),
            "image_status": pres.get("image_status", ""),
            "needs_review": "true" if reasons else "false",
            "primary_review_reason": reasons[0] if reasons else "",
            "review_reasons": ";".join(reasons),
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
        "edition_disposition",
        "field_summary",
        "family_ok",
        "image_status",
        "needs_review",
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
    df["s3_fragment"] = df["clean_text"].map(detect_fragment)
    df["s4_trailing_value"] = df["clean_text"].map(lambda t: bool(TRAILING_VALUE_PATTERN.search(t)))
    df["s5_anatomy"] = df["clean_text"].map(lambda t: detect_structural_anatomy(t)["any"])
    df["is_manuscript_section"] = df.apply(_is_manuscript_section_row, axis=1)
    df["entry_form"] = df.apply(classify_entry_form, axis=1)
    seg = df.apply(lambda r: parse_manuscript_row(r) if r["entry_form"] == "manuscript" else segment_entry(r), axis=1)
    df = pd.concat([df, seg], axis=1)
    df["paren_fields"] = df.apply(split_paren_fields, axis=1)
    df["paren_field_types"] = [[] for _ in range(len(df))]
    heads = df.apply(parse_head, axis=1)
    df = pd.concat([df, heads], axis=1)
    tails = df.apply(decompose_tail, axis=1)
    df = pd.concat([df, tails], axis=1)
    df = resolve_relationships(df)
    df = roll_up_catalog_text(df)
    return df


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


def canon(text: str) -> str:
    """Return lowercase alphanumeric-folded text for alignment."""
    return NON_ALNUM_RE.sub("", str(text).lower())


def akey(text: str) -> str:
    """Return standalone edition alignment key with normalized price tokens."""
    folded = canon(text)
    prices = "|".join(p.group(0).replace(",", "") for p in PRICE_RE.finditer(str(text)))
    return f"{folded}|{prices}"


def _align_rows(v1: list[dict], v2: list[dict]) -> list[dict]:
    v1_groups = _groups(v1)
    v2_groups = _groups(v2)
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
        if rk in emitted_v2 or rk in used_v2:
            continue
        for idx, right in enumerate(group, start=1):
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


def _v1_field_values(row: dict) -> dict:
    return {
        "post_office/town": _norm_set(row.get("txtTown", "")),
        "dates_seen": _norm_set(row.get("txtDatesSeen", "")),
        "colors": _norm_set(row.get("txtColors", "")),
        "width/height": _norm_set(" x ".join(x for x in (row.get("width", ""), row.get("height", "")) if x)),
        "rate_val": _norm_set(row.get("txtRatesText", "")),
    }


def _aggregate_v2_fields(out_dir: Path) -> dict:
    markings = read_csv(out_dir / "markings.csv")
    dates = read_csv(out_dir / "dates_seen.csv")
    offices = {r["id"]: r.get("name", "") for r in read_csv(out_dir / "post_offices.csv")}
    colors_by_id = {r["id"]: r.get("name", "") for r in read_csv(out_dir / "colors.csv")}
    lineage = read_csv(out_dir / "marking_lineage.csv")
    by_marking = {r["id"]: r for r in markings}
    keys_by_marking = {r["marking_id"]: r["v2_key"] for r in lineage}
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
        date_vals = []
        for mid in mids:
            m = by_marking.get(mid, {})
            if m.get("type") == "TOWNMARK":
                town.append(offices.get(m.get("post_office", ""), ""))
                colors.append(colors_by_id.get(m.get("color", ""), m.get("color", "")))
                if m.get("width") or m.get("height"):
                    sizes.append(f"{m.get('width', '')} x {m.get('height', '')}")
            if m.get("type") == "RATEMARK":
                rates.append(m.get("rate_val", ""))
            date_vals.extend(dates_by_mid.get(mid, []))
        out[key] = {
            "post_office/town": _join_set(town),
            "dates_seen": _join_set(date_vals),
            "colors": _join_set(colors),
            "width/height": _join_set(sizes),
            "rate_val": _join_set(rates),
        }
    return out


def _field_verdict(left: str, right: str) -> str:
    if left and right and left == right:
        return "agree"
    if left and right:
        return "differ"
    if left:
        return "v1_only"
    if right:
        return "v2_only"
    return "agree"


def _norm_set(text: str) -> str:
    parts = re.split(r"[|;,]+", str(text or ""))
    return _join_set(parts)


def _join_set(values) -> str:
    cleaned = sorted({str(v).strip().upper() for v in values if str(v or "").strip()})
    return "|".join(cleaned)


def _v2_family_map(paths: dict) -> dict:
    rows = read_csv(paths["catalog_rows"])
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
    key_by_mid = {r["marking_id"]: r["v2_key"] for r in lineage}
    return {key_by_mid.get(r.get("subject_id", ""), "") for r in read_csv(images_path)} - {""}


def _field_reasons(path: Path) -> dict:
    reasons = defaultdict(list)
    for row in read_csv(path):
        if row["verdict"] == "agree":
            continue
        code_field = row["field"].replace("/", "_").replace(" ", "_")
        reasons[row["v1_key"]].append(f"S4:{code_field}_{row['verdict']}")
    return {k: sorted(set(v)) for k, v in reasons.items()}


def _reason_sort_key(reason: str) -> tuple:
    order = {"S3": 0, "S5": 1, "S4": 2, "S2": 3}
    return (order.get(reason.split(":", 1)[0], 9), reason)


def _int_key(value) -> int:
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0
