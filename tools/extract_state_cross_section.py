#!/usr/bin/env python3
"""extract_state_cross_section -- carve an Approved, single-state slice out of
the v1 tblRawStateData.csv export.

Given a state abbreviation (e.g. 'VA'), this looks up the matching nStateID in
wip/in/tblStates.csv (via the txtStateAbv column), then writes a new CSV
containing only the tblRawStateData rows where:

  * approve_status == 'Approved'
  * nStateID       == the resolved state's nStateID

The output preserves the full original column set and header order so the
slice stays a drop-in cross-section of the v1 source data.

Usage (run from the tools/ directory; exit code 0 on success):

    python3 extract_state_cross_section.py VA

Optional overrides:

    python3 extract_state_cross_section.py VA \
        --raw wip/in/tblRawStateData.csv \
        --states wip/in/tblStates.csv \
        --out wip/in/tblRawStateData_VA_Approved.csv

Default output path, for abbreviation VA, is:

    wip/in/tblRawStateData_VA_Approved.csv
"""
import argparse
import csv
import sys
from pathlib import Path

# v1 cells (HTML blobs, manuscript memos) can exceed Python's default CSV field
# cap. Raise it well past anything in the export so DictReader never throws
# "field larger than field limit".
csv.field_size_limit(10 ** 9)

# Column name constants -- keep these aligned with the v1 export header.
ABV_COL = "txtStateAbv"
STATE_ID_COL = "nStateID"
APPROVE_COL = "approve_status"
APPROVED_VALUE = "Approved"


def resolve_state_id(states_path: Path, abbrev: str) -> str:
    """Return the nStateID (as a string) whose txtStateAbv matches abbrev.

    Matching is case-insensitive and trims surrounding whitespace. Raises
    SystemExit(2) with a readable message if the file lacks the expected
    columns or the abbreviation is not found.
    """
    target = abbrev.strip().upper()
    with states_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or ABV_COL not in reader.fieldnames:
            sys.exit(
                "error: {0} has no '{1}' column (found: {2})".format(
                    states_path, ABV_COL, reader.fieldnames
                )
            )
        for row in reader:
            if (row.get(ABV_COL) or "").strip().upper() == target:
                state_id = (row.get(STATE_ID_COL) or "").strip()
                if not state_id:
                    sys.exit(
                        "error: state '{0}' has an empty {1}".format(
                            target, STATE_ID_COL
                        )
                    )
                return state_id
    sys.exit("error: no state with {0} == '{1}'".format(ABV_COL, target))


def write_slice(raw_path: Path, out_path: Path, state_id: str) -> int:
    """Stream raw_path -> out_path keeping Approved rows for state_id.

    Returns the count of rows written. Exits(2) if the raw export is missing
    the columns this filter depends on.
    """
    with raw_path.open(newline="", encoding="utf-8") as src:
        reader = csv.DictReader(src)
        fields = reader.fieldnames
        if fields is None:
            sys.exit("error: {0} is empty".format(raw_path))
        for required in (STATE_ID_COL, APPROVE_COL):
            if required not in fields:
                sys.exit(
                    "error: {0} has no '{1}' column".format(raw_path, required)
                )

        written = 0
        with out_path.open("w", newline="", encoding="utf-8") as dst:
            writer = csv.DictWriter(dst, fieldnames=fields)
            writer.writeheader()
            for row in reader:
                same_state = (row.get(STATE_ID_COL) or "").strip() == state_id
                approved = (row.get(APPROVE_COL) or "").strip() == APPROVED_VALUE
                if same_state and approved:
                    writer.writerow(row)
                    written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract an Approved, single-state slice of v1 "
        "tblRawStateData.csv."
    )
    parser.add_argument(
        "abbrev", help="State abbreviation to slice on, e.g. VA"
    )
    parser.add_argument(
        "--raw",
        default="wip/in/tblRawStateData.csv",
        help="Path to v1 tblRawStateData.csv (default: %(default)s)",
    )
    parser.add_argument(
        "--states",
        default="wip/in/tblStates.csv",
        help="Path to v1 tblStates.csv (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output CSV path (default: wip/in/tblRawStateData_<ABV>_Approved.csv)",
    )
    args = parser.parse_args()

    abbrev = args.abbrev.strip().upper()
    raw_path = Path(args.raw)
    states_path = Path(args.states)
    out_path = (
        Path(args.out)
        if args.out
        else Path("wip/in/tblRawStateData_{0}_Approved.csv".format(abbrev))
    )

    for path in (raw_path, states_path):
        if not path.is_file():
            sys.exit("error: file not found: {0}".format(path))

    state_id = resolve_state_id(states_path, abbrev)
    print("resolved {0} -> {1} = {2}".format(abbrev, STATE_ID_COL, state_id))

    written = write_slice(raw_path, out_path, state_id)
    print(
        "wrote {0} Approved row(s) for {1} to {2}".format(
            written, abbrev, out_path
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
