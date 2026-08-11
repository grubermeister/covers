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
    build_image_counts,
    normalize_listing,
    write_image_refs,
)
from v1_synthetic_listing import synthetic_listing
from v1_massachusetts import normalize_boston_listing_for_munger


CATALOG_PAGE_VALUE = "0"
IMAGE_COUNT_VALUE = "0"
ROW_TYPE_VALUE = "LISTING"
TOWN_COL = "txtTown"
TOWN_POSTMARK_COL = "txtTownPostmark"
V1_LISTING_VALUE_PATTERN = (
    r"(?:\d[\d,]*(?:\.\d+)?(?:[-/]\d[\d,]*(?:\.\d+)?)*|---?)"
)
V1_COMPLETE_VALUED_LISTING_RE = re.compile(
    r"\([^)]*\)\s*" + V1_LISTING_VALUE_PATTERN + r"\s*$"
)
V1_GLUED_SAME_LISTING_START_RE = re.compile(
    r"(?<![A-Za-z])(?:\(\d+\))?\s*\+?(?:The\s+)?Same\b",
    re.IGNORECASE,
)
V1_SAME_VALUED_LISTING_RE = re.compile(
    r"^(?:\(\d+\))?\s*\+?(?:The\s+)?Same\b"
    r"(?=[^\r\n)]*\))"
    r"(?:[^()]|\([^()]*\)){0,160}?"
    + V1_LISTING_VALUE_PATTERN
    + r"(?=\s|$)",
    re.IGNORECASE,
)
V1_COLOR_SPLIT_IGNORED_COLUMNS = frozenset({
    "nRawStateDataID",
    "nRawStateDataID_parent",
    "txtColors",
    "txtTownmarkColor",
    "txtDefaultImage",
    "txtPDFPage",
    "nImageCount",
    "dtEntered",
    "dtUpdated",
    "ynForReview",
    "txtReasonForReview",
    "txtMarkedBy",
    "dtMarkedForReview",
    "approve_status",
    "request_status",
    "txtUserEmail",
    "ynEmailCheck",
    "submitterId",
    "approverId",
})


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


def split_glued_same_listings(listing: str) -> list[str]:
    """Split v1 rows that accidentally joined adjacent Same listings.

    v1 raw text is normalized before the munger sees it, so a missing line
    split can become one string such as:
      Same VA.(1855-61;--;FREE;Black) 60 (1)Same(PAID,3;Green) 50

    The munger is row-oriented. Split only at an explicit Same-family listing
    with its own parenthetical value, and only when the preceding segment
    already ends like a complete valued catalog listing.
    """
    if not listing:
        return []
    starts = [0]
    for match in V1_GLUED_SAME_LISTING_START_RE.finditer(listing):
        start = match.start()
        if start == 0:
            continue
        if not V1_SAME_VALUED_LISTING_RE.match(listing[start:]):
            continue
        previous = listing[starts[-1]:start].strip()
        if V1_COMPLETE_VALUED_LISTING_RE.search(previous):
            starts.append(start)
    if len(starts) == 1:
        return [listing]

    parts = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(listing)
        part = listing[start:end].strip()
        if part:
            parts.append(part)
    return parts


def v1_color_split_key(row: dict[str, str], fields: list[str]) -> tuple[str, ...]:
    """Return the semantic key used to collapse manual v1 color splits.

    v1 sometimes has several rows for one catalog record because an editor
    manually split color variants across rows. The munger owns color fan-out,
    so the v1 adapter drops later rows only when all parser-relevant fields are
    identical after excluding color sources and administrative bookkeeping.

    Example duplicate shape:
      {"txtRawStateData": "Same(...;Black,Blue,Red) 20",
       "txtTownmarkColor": "Blue"}
      {"txtRawStateData": "Same(...;Black,Blue,Red) 20",
       "txtTownmarkColor": "Red"}
    """
    parts = []
    for field in fields:
        if field in V1_COLOR_SPLIT_IGNORED_COLUMNS:
            continue
        parts.append(normalize_listing(row.get(field)))
    return tuple(parts)


def dedupe_v1_color_split_rows(
    rows: list[dict[str, str]],
    fields: list[str],
    image_counts: dict[str, int] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Drop later rows that only differ by v1 color columns.

    Rows with image refs are always kept. If a duplicate image-bearing row were
    dropped here, write_image_refs() would have no included raw id to attach
    that image to during the v1 overlay step.
    """
    image_counts = image_counts or {}
    seen = set()
    kept = []
    dropped_raw_ids = []
    for row in rows:
        raw_id = (row.get(RAW_ID_COL) or "").strip()
        key = v1_color_split_key(row, fields)
        if int(image_counts.get(raw_id, 0)) > 0:
            seen.add(key)
            kept.append(row)
            continue
        if key in seen:
            dropped_raw_ids.append(raw_id)
            continue
        seen.add(key)
        kept.append(row)
    return kept, dropped_raw_ids


def write_v1_catalog_rows(
    slice_path: Path,
    out_path: Path,
    image_counts: dict[str, int] | None = None,
    state: str = "",
) -> tuple[int, list[str]]:
    """Write a v1 slice as munger-safe catalog rows.

    Returns (rows_written, included_raw_ids). included_raw_ids contains source
    rows whose txtRawStateData is non-empty or whose v1 split columns can be
    synthesized into munger-safe listing text.
    """
    catalog_rows = []
    included_raw_ids = []
    source_rows = []
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
        raw_rows = list(reader)
        source_rows, dropped_raw_ids = dedupe_v1_color_split_rows(
            raw_rows,
            fields,
            image_counts=image_counts,
        )
        if dropped_raw_ids:
            print(
                "dropped v1 color-split duplicate rows: {0} ({1})".format(
                    len(dropped_raw_ids),
                    ", ".join(dropped_raw_ids[:10]),
                )
            )
        else:
            print("dropped v1 color-split duplicate rows: 0")
        split_rows = 0
        for row in source_rows:
            listing = normalize_listing(row.get(RAW_TEXT_COL))
            listing = strip_glued_context_prefix(listing, row)
            if not listing:
                listing = synthetic_listing(row)
            if not listing:
                continue
            listing = normalize_boston_listing_for_munger(state, row, listing)
            raw_id = (row.get(RAW_ID_COL) or "").strip()
            listing_parts = split_glued_same_listings(listing)
            if len(listing_parts) > 1:
                split_rows += 1
            for listing_part in listing_parts:
                included_raw_ids.append(raw_id)
                catalog_rows.append(
                    {
                        "listing_text": listing_part,
                        "catalog_page": CATALOG_PAGE_VALUE,
                        "chunk_number": raw_id,
                        "image_count": IMAGE_COUNT_VALUE,
                        "row_type": ROW_TYPE_VALUE,
                        "is_manuscript": "",
                        "default_shape": "",
                        "institutional_owner": "",
                    }
                )
        print("split v1 glued Same listing rows: {0}".format(split_rows))
    write_catalog_rows(Path(out_path), catalog_rows)
    return len(catalog_rows), included_raw_ids


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
    image_counts = build_image_counts(images_path)
    rows_written, raw_ids = write_v1_catalog_rows(
        slice_out,
        catalog_rows_out,
        image_counts=image_counts,
        state=state,
    )
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
