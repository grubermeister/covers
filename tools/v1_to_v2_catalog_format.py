#!/usr/bin/env python3
"""v1_to_v2_catalog_format -- best-effort convert a v1 tblRawStateData
cross-section into the v2 pipeline output shape (as in VA_ASCC_CTLG.csv).

The v2 catalog CSV has exactly these columns, in this order:

    Listing,Page,Chunk,Images Above,Type,Manuscript,Default Shape

Mapping applied per v1 row:

    Listing        <- txtRawStateData (raw listing text, with excess
                      whitespace collapsed: see normalize_listing)
    Page           <- "" (no v1 equivalent; left blank)
    Chunk          <- nRawStateDataID
    Images Above   <- count of tblTownmarkImages rows whose nRawStateDataID
                      matches this row AND whose ynDeleted == 'False'
    Type           <- "LISTING" (constant for every row)
    Manuscript     <- "" (no v1 equivalent; left blank)
    Default Shape  <- "" (no v1 equivalent; left blank)

This is best-effort: Page / Manuscript / Default Shape are intentionally
empty because the v1 export carries no clean source for them.

Usage (run from the tools/ directory; exit code 0 on success):

    python3 v1_to_v2_catalog_format.py wip/in/v1_VA_data.csv

Optional overrides:

    python3 v1_to_v2_catalog_format.py wip/in/v1_VA_data.csv \
        --images wip/in/tblTownmarkImages.csv \
        --out wip/out/v1_VA_data_v2format.csv

Default output path is the input path with a '_v2format.csv' suffix, e.g.
wip/in/v1_VA_data.csv -> wip/in/v1_VA_data_v2format.csv
"""
import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

# Matches any run of whitespace (spaces, tabs, newlines, carriage returns).
# Used to collapse excess whitespace in v1 listing text down to single spaces.
_WS_RUN = re.compile(r"\s+")

# v1 cells (HTML blobs, manuscript memos) can exceed Python's default CSV field
# cap. Raise it well past anything in the export so DictReader never throws.
csv.field_size_limit(10 ** 9)

# v2 output header, exact order -- do not reorder.
V2_COLUMNS = [
    "Listing",
    "Page",
    "Chunk",
    "Images Above",
    "Type",
    "Manuscript",
    "Default Shape",
]

# v1 source column names.
LISTING_COL = "txtRawStateData"
RAW_ID_COL = "nRawStateDataID"

# tblTownmarkImages column names.
IMG_RAW_ID_COL = "nRawStateDataID"
IMG_DELETED_COL = "ynDeleted"
# Image rows count toward "Images Above" only when ynDeleted is exactly this.
NOT_DELETED_VALUE = "False"

TYPE_VALUE = "LISTING"


def normalize_listing(text: str) -> str:
    """Collapse excess whitespace in a v1 listing string.

    Every run of whitespace -- including embedded newlines and tabs that the
    v1 export sometimes carries mid-listing -- becomes a single space, and
    leading/trailing whitespace is stripped. Returns "" for a None/empty input.

    Example: '\\nRICHMOND,(E)  6 PAID,\\nPAID 6;Black 400'
          -> 'RICHMOND,(E) 6 PAID, PAID 6;Black 400'
    """
    if not text:
        return ""
    return _WS_RUN.sub(" ", text).strip()


def build_image_counts(images_path: Path) -> Counter:
    """Return a Counter mapping nRawStateDataID (str) -> count of non-deleted
    image rows for that id.

    Only rows where ynDeleted == 'False' are counted. Exits(2) if the file is
    missing the columns this depends on.
    """
    counts = Counter()
    with images_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        if fields is None:
            sys.exit("error: {0} is empty".format(images_path))
        for required in (IMG_RAW_ID_COL, IMG_DELETED_COL):
            if required not in fields:
                sys.exit(
                    "error: {0} has no '{1}' column".format(
                        images_path, required
                    )
                )
        for row in reader:
            if (row.get(IMG_DELETED_COL) or "").strip() == NOT_DELETED_VALUE:
                raw_id = (row.get(IMG_RAW_ID_COL) or "").strip()
                if raw_id:
                    counts[raw_id] += 1
    return counts


def convert(src_path: Path, out_path: Path, image_counts: Counter) -> int:
    """Stream src_path -> out_path in v2 column shape. Returns rows written.

    Exits(2) if the source lacks the v1 columns this depends on.
    """
    with src_path.open(newline="", encoding="utf-8") as src:
        reader = csv.DictReader(src)
        fields = reader.fieldnames
        if fields is None:
            sys.exit("error: {0} is empty".format(src_path))
        for required in (LISTING_COL, RAW_ID_COL):
            if required not in fields:
                sys.exit(
                    "error: {0} has no '{1}' column".format(src_path, required)
                )

        written = 0
        skipped = 0
        with out_path.open("w", newline="", encoding="utf-8") as dst:
            writer = csv.DictWriter(dst, fieldnames=V2_COLUMNS)
            writer.writeheader()
            for row in reader:
                listing = normalize_listing(row.get(LISTING_COL))
                # Final pass: drop entries with no listing text left after
                # whitespace normalization -- they carry nothing to catalog.
                if not listing:
                    skipped += 1
                    continue
                raw_id = (row.get(RAW_ID_COL) or "").strip()
                writer.writerow(
                    {
                        "Listing": listing,
                        "Page": "",
                        "Chunk": raw_id,
                        "Images Above": image_counts.get(raw_id, 0),
                        "Type": TYPE_VALUE,
                        "Manuscript": "",
                        "Default Shape": "",
                    }
                )
                written += 1
    if skipped:
        print("skipped {0} row(s) with no listing text".format(skipped))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a v1 tblRawStateData cross-section to the v2 "
        "catalog CSV format."
    )
    parser.add_argument(
        "src", help="Path to the v1 cross-section CSV (e.g. wip/in/v1_VA_data.csv)"
    )
    parser.add_argument(
        "--images",
        default="wip/in/tblTownmarkImages.csv",
        help="Path to tblTownmarkImages.csv (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output CSV path (default: <src>_v2format.csv)",
    )
    args = parser.parse_args()

    src_path = Path(args.src)
    images_path = Path(args.images)
    out_path = (
        Path(args.out)
        if args.out
        else src_path.with_name(src_path.stem + "_v2format.csv")
    )

    for path in (src_path, images_path):
        if not path.is_file():
            sys.exit("error: file not found: {0}".format(path))

    image_counts = build_image_counts(images_path)
    print(
        "loaded image counts for {0} distinct nRawStateDataID(s)".format(
            len(image_counts)
        )
    )

    written = convert(src_path, out_path, image_counts)
    print("wrote {0} v2-format row(s) to {1}".format(written, out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
