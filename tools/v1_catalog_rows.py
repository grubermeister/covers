#!/usr/bin/env python3
"""Build munger catalog rows from v1 tblRawStateData.

Run from repo root:
    PYTHONPATH=tools uv run python tools/v1_catalog_rows.py VA \
        --raw tools/wip/in/tblRawStateData.csv \
        --states tools/wip/in/tblStates.csv \
        --images tools/wip/in/tblTownmarkImages.csv \
        --slice-out tools/wip/cache/v1/VA/slice.csv \
        --catalog-rows-out tools/wip/cache/v1/VA/catalog_rows.csv \
        --image-refs-out tools/wip/cache/v1/VA/image_refs.csv

Expected exit code: 0.

This is intentionally not an OCR adapter. It uses v1 raw catalog text as the
listing source, sets image_count to 0 so the existing munger does not look for
OCR-extracted image files, and keeps nRawStateDataID in chunk_number so the
post-munge reconciliation pass can map generated v2 markings back to v1 rows.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from catalog_rows import write_catalog_rows
from extract_state_cross_section import (
    RAW_TEXT_COL,
    STATUS_NOT_DELETED,
    resolve_state_id,
    write_slice,
)
from v1_to_v2_catalog_format import (
    RAW_ID_COL,
    normalize_listing,
    write_image_refs,
)


CATALOG_PAGE_VALUE = "0"
IMAGE_COUNT_VALUE = "0"
ROW_TYPE_VALUE = "LISTING"
TOWN_COL = "txtTown"
TOWN_POSTMARK_COL = "txtTownPostmark"


def candidate_town_tokens(row: dict[str, str]) -> list[str]:
    """Return v1 split-column town tokens, longest first.

    The munger still receives v1 catalog text, but some v1 rows glue a prose
    note to the listing, for example `...1775.Petersburg(...)`. The split
    columns identify the actual listing town, so those tokens are used only to
    find a safe parse start for the munger input.
    """
    tokens = []
    seen = set()
    for column in (TOWN_POSTMARK_COL, TOWN_COL):
        token = normalize_listing(row.get(column))
        key = token.upper()
        if token and key not in seen:
            tokens.append(token)
            seen.add(key)
    return sorted(tokens, key=len, reverse=True)


def strip_glued_context_prefix(listing: str, row: dict[str, str]) -> str:
    """Trim prose glued before a v1 listing town.

    This is intentionally narrow:
    - the split town token must be followed by listing syntax like `(`;
    - the prefix must end with a period, which matches the known v1 prose
      glue rows and avoids changing ordinary same-town listing text.
    """
    if not listing:
        return listing
    for token in candidate_town_tokens(row):
        pattern = re.compile(
            r"(?<![A-Za-z0-9])"
            + re.escape(token)
            + r"(?=\s*(?:[,/][A-Za-z. ]*)?\()",
            re.IGNORECASE,
        )
        for match in pattern.finditer(listing):
            if match.start() == 0:
                return listing
            prefix = listing[:match.start()].strip()
            if prefix.endswith("."):
                return listing[match.start():].strip()
    return listing


def write_v1_catalog_rows(slice_path: Path, out_path: Path) -> tuple[int, list[str]]:
    """Write a v1 slice as munger-safe catalog rows.

    Returns (rows_written, included_raw_ids). included_raw_ids contains only
    source rows whose normalized txtRawStateData is non-empty.
    """
    rows = []
    included_raw_ids = []
    with Path(slice_path).open(newline="", encoding="utf-8") as src:
        reader = csv.DictReader(src)
        fields = reader.fieldnames
        if fields is None:
            sys.exit("error: {0} is empty".format(slice_path))
        for required in (RAW_ID_COL, RAW_TEXT_COL):
            if required not in fields:
                sys.exit(
                    "error: {0} has no '{1}' column".format(slice_path, required)
                )
        for row in reader:
            listing = normalize_listing(row.get(RAW_TEXT_COL))
            listing = strip_glued_context_prefix(listing, row)
            if not listing:
                continue
            raw_id = (row.get(RAW_ID_COL) or "").strip()
            included_raw_ids.append(raw_id)
            rows.append(
                {
                    "listing_text": listing,
                    "catalog_page": CATALOG_PAGE_VALUE,
                    "chunk_number": raw_id,
                    "image_count": IMAGE_COUNT_VALUE,
                    "row_type": ROW_TYPE_VALUE,
                    "is_manuscript": "",
                    "default_shape": "",
                    "institutional_owner": "",
                }
            )
    write_catalog_rows(Path(out_path), rows)
    return len(rows), included_raw_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build v1-only ASCC catalog rows for the munger."
    )
    parser.add_argument("state", help="State abbreviation, for example VA")
    parser.add_argument(
        "--raw",
        default="tools/wip/in/tblRawStateData.csv",
        help="Path to v1 tblRawStateData.csv (default: %(default)s)",
    )
    parser.add_argument(
        "--states",
        default="tools/wip/in/tblStates.csv",
        help="Path to v1 tblStates.csv (default: %(default)s)",
    )
    parser.add_argument(
        "--images",
        default="tools/wip/in/tblTownmarkImages.csv",
        help="Path to v1 tblTownmarkImages.csv (default: %(default)s)",
    )
    parser.add_argument(
        "--slice-out",
        required=True,
        help="Output path for the filtered v1 state slice.",
    )
    parser.add_argument(
        "--catalog-rows-out",
        required=True,
        help="Output path for generated munger catalog rows.",
    )
    parser.add_argument(
        "--image-refs-out",
        required=True,
        help="Output path for normalized v1 image references.",
    )
    parser.add_argument(
        "--region-abbrev",
        default=None,
        help="Two-letter region abbreviation for image storage filenames.",
    )
    args = parser.parse_args(argv)

    state = args.state.strip().upper()
    raw_path = Path(args.raw)
    states_path = Path(args.states)
    images_path = Path(args.images)
    slice_out = Path(args.slice_out)
    catalog_rows_out = Path(args.catalog_rows_out)
    image_refs_out = Path(args.image_refs_out)

    for path in (raw_path, states_path, images_path):
        if not path.is_file():
            sys.exit("error: file not found: {0}".format(path))

    slice_out.parent.mkdir(parents=True, exist_ok=True)
    catalog_rows_out.parent.mkdir(parents=True, exist_ok=True)
    image_refs_out.parent.mkdir(parents=True, exist_ok=True)

    state_id = resolve_state_id(states_path, state)
    stats = write_slice(raw_path, slice_out, state_id, status=STATUS_NOT_DELETED)
    rows_written, raw_ids = write_v1_catalog_rows(slice_out, catalog_rows_out)
    region_abbrev = (
        args.region_abbrev.strip().upper()
        if args.region_abbrev
        else state
    )
    image_rows = write_image_refs(images_path, image_refs_out, raw_ids, region_abbrev)

    print("resolved {0} -> nStateID = {1}".format(state, state_id))
    print("wrote v1 slice rows: {0} -> {1}".format(stats.rows_written, slice_out))
    print("wrote catalog rows: {0} -> {1}".format(rows_written, catalog_rows_out))
    print("wrote image refs: {0} -> {1}".format(image_rows, image_refs_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
