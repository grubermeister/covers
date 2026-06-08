#!/usr/bin/env python3
"""build_ascc2_overlay -- prepare a Sixth Edition remunge overlay.

Inputs are the ASCC1 v2 OCR CSV and the v1-derived v2-format CSV. The output
overlay CSV contains only compare-side families that are textually new or
materially changed. Rows are emitted in compare-edition order, with
"Images Above" zeroed so the remunge is text-only and image attachment can be
handled explicitly in a later apply step.

A companion map CSV records the family/source-row correspondence across base
and compare, including compare duplicates and base-only removals. The apply
step uses the map for soft removals and for attaching v1 image refs keyed by
the compare-side Chunk/raw-row id.

Usage (from repo root; exit code 0 on success):

    python3 tools/build_ascc2_overlay.py \
        --base tools/wip/in/VA_ASCC_CTLG.csv \
        --compare tools/wip/in/v1_VA_ocr.csv \
        --out tools/wip/out/VA_ASCC2_overlay.csv \
        --map-out tools/wip/out/VA_ASCC2_overlay_map.csv
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    from catalog_edition_diff import (
        V2_COLUMNS,
        _CONTINUATION_RE,
        classify,
        expand_duplicate_accountability,
        load_listings,
        representative_groups,
    )
except ModuleNotFoundError:
    from tools.catalog_edition_diff import (
        V2_COLUMNS,
        _CONTINUATION_RE,
        classify,
        expand_duplicate_accountability,
        load_listings,
        representative_groups,
    )


def build_families(entries):
    """Return ordered family records keyed by the root source id."""
    families = []
    family_by_id = {}
    current = None
    for entry in entries:
        if current is None or not _CONTINUATION_RE.match(entry.listing):
            current = {
                "family_id": entry.source_id,
                "root": entry,
                "entries": [],
            }
            families.append(current)
            family_by_id[current["family_id"]] = current
        current["entries"].append(entry)
    return families, family_by_id


def row_entry(result, side):
    if side == "base":
        return result.get("old")
    return result.get("new")


def row_repr_entry(result, side):
    key = "old_representative" if side == "base" else "new_representative"
    return result.get(key) or row_entry(result, side)


def family_id_for(entry, family_by_entry_id):
    if entry is None:
        return ""
    return family_by_entry_id.get(entry.source_id, "")


def family_chunk(family):
    if not family:
        return ""
    return str(family["root"].row.get("Chunk", "") or "").strip()


def family_page(family):
    if not family:
        return ""
    return str(family["root"].row.get("Page", "") or "").strip()


def overlay_row(entry):
    row = {col: entry.row.get(col, "") for col in V2_COLUMNS}
    row["Images Above"] = 0
    return row


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a text-only ASCC2 overlay CSV and family map."
    )
    parser.add_argument("--base", required=True, help="ASCC1 v2 OCR CSV")
    parser.add_argument("--compare", required=True, help="v1-derived v2-format CSV")
    parser.add_argument("--out", required=True, help="Overlay CSV path")
    parser.add_argument("--map-out", required=True, help="Overlay map CSV path")
    parser.add_argument(
        "--pair-threshold",
        type=float,
        default=0.55,
        help="Min canon similarity to pair entries in replace/move (default 0.55)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    base_path = Path(args.base)
    compare_path = Path(args.compare)
    out_path = Path(args.out)
    map_path = Path(args.map_out)

    for path in (base_path, compare_path):
        if not path.is_file():
            raise SystemExit(f"error: input not found: {path}")

    base_entries, _ = load_listings(base_path, "BASE")
    compare_entries, _ = load_listings(compare_path, "COMPARE")
    base_reps, base_groups = representative_groups(base_entries)
    compare_reps, compare_groups = representative_groups(compare_entries)
    rep_results, _, _ = classify(base_reps, compare_reps, args.pair_threshold)
    report_results = expand_duplicate_accountability(rep_results, base_groups, compare_groups)

    base_families, base_family_by_id = build_families(base_entries)
    compare_families, compare_family_by_id = build_families(compare_entries)
    base_family_by_entry_id = {
        entry.source_id: family["family_id"]
        for family in base_families
        for entry in family["entries"]
    }
    compare_family_by_entry_id = {
        entry.source_id: family["family_id"]
        for family in compare_families
        for entry in family["entries"]
    }

    compare_family_state = defaultdict(
        lambda: {
            "include_in_overlay": False,
            "family_action": "",
            "base_family_ids": set(),
        }
    )
    removed_base_families = set()

    for result in rep_results:
        base_entry = result.get("old")
        compare_entry = result.get("new")
        base_family_id = family_id_for(base_entry, base_family_by_entry_id)
        compare_family_id = family_id_for(compare_entry, compare_family_by_entry_id)
        row_disposition = result["row_disposition"]
        content_change = result.get("content_change", "")
        if compare_family_id:
            state = compare_family_state[compare_family_id]
            if base_family_id:
                state["base_family_ids"].add(base_family_id)
            if row_disposition == "added":
                state["include_in_overlay"] = True
                state["family_action"] = "added"
            elif content_change == "material":
                state["include_in_overlay"] = True
                if state["family_action"] != "added":
                    state["family_action"] = "material"
        if row_disposition == "removed" and base_family_id:
            removed_base_families.add(base_family_id)

    overlay_family_ids = [
        family["family_id"]
        for family in compare_families
        if compare_family_state[family["family_id"]]["include_in_overlay"]
    ]
    rep_ids = {entry.source_id for entry in compare_reps}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=V2_COLUMNS)
        writer.writeheader()
        for family_id in overlay_family_ids:
            family = compare_family_by_id[family_id]
            for entry in family["entries"]:
                if entry.source_id not in rep_ids:
                    continue
                writer.writerow(overlay_row(entry))

    map_columns = [
        "compare_source_id",
        "compare_representative_id",
        "compare_family_id",
        "compare_family_chunk",
        "compare_family_page",
        "compare_chunk",
        "compare_page",
        "base_source_id",
        "base_representative_id",
        "base_family_id",
        "base_chunk",
        "base_page",
        "row_disposition",
        "content_change",
        "representative_row_disposition",
        "representative_content_change",
        "is_compare_duplicate",
        "include_in_overlay",
        "family_action",
    ]
    map_path.parent.mkdir(parents=True, exist_ok=True)
    with map_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=map_columns)
        writer.writeheader()
        for result in report_results:
            compare_entry = row_entry(result, "compare")
            base_entry = row_entry(result, "base")
            compare_rep = row_repr_entry(result, "compare")
            base_rep = row_repr_entry(result, "base")
            compare_family_id = family_id_for(compare_rep, compare_family_by_entry_id)
            base_family_id = family_id_for(base_rep, base_family_by_entry_id)
            compare_family = compare_family_by_id.get(compare_family_id)
            base_family = base_family_by_id.get(base_family_id)
            family_state = compare_family_state.get(compare_family_id, {})
            writer.writerow(
                {
                    "compare_source_id": compare_entry.source_id if compare_entry else "",
                    "compare_representative_id": compare_rep.source_id if compare_rep else "",
                    "compare_family_id": compare_family_id,
                    "compare_family_chunk": family_chunk(compare_family),
                    "compare_family_page": family_page(compare_family),
                    "compare_chunk": str(compare_entry.row.get("Chunk", "") or "").strip() if compare_entry else "",
                    "compare_page": str(compare_entry.row.get("Page", "") or "").strip() if compare_entry else "",
                    "base_source_id": base_entry.source_id if base_entry else "",
                    "base_representative_id": base_rep.source_id if base_rep else "",
                    "base_family_id": base_family_id,
                    "base_chunk": family_chunk(base_family),
                    "base_page": family_page(base_family),
                    "row_disposition": result["row_disposition"],
                    "content_change": result.get("content_change", ""),
                    "representative_row_disposition": result.get(
                        "representative_row_disposition", result["row_disposition"]
                    ),
                    "representative_content_change": result.get(
                        "representative_content_change", result.get("content_change", "")
                    ),
                    "is_compare_duplicate": "true"
                    if compare_entry is not None and compare_rep is not None and compare_entry.source_id != compare_rep.source_id
                    else "false",
                    "include_in_overlay": "true" if family_state.get("include_in_overlay") else "false",
                    "family_action": family_state.get("family_action", ""),
                }
            )
        for family_id in sorted(removed_base_families):
            family = base_family_by_id[family_id]
            writer.writerow(
                {
                    "compare_source_id": "",
                    "compare_representative_id": "",
                    "compare_family_id": "",
                    "compare_family_chunk": "",
                    "compare_family_page": "",
                    "compare_chunk": "",
                    "compare_page": "",
                    "base_source_id": family["root"].source_id,
                    "base_representative_id": family["root"].source_id,
                    "base_family_id": family_id,
                    "base_chunk": family_chunk(family),
                    "base_page": family_page(family),
                    "row_disposition": "removed",
                    "content_change": "",
                    "representative_row_disposition": "removed",
                    "representative_content_change": "",
                    "is_compare_duplicate": "false",
                    "include_in_overlay": "false",
                    "family_action": "removed",
                }
            )

    print(f"overlay families: {len(overlay_family_ids)}")
    print(f"overlay rows: {sum(1 for _ in open(out_path, 'r', encoding='utf-8')) - 1}")
    print(f"removed base families: {len(removed_base_families)}")
    print(f"overlay: {out_path}")
    print(f"map: {map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
