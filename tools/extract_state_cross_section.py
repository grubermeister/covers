#!/usr/bin/env python3
"""extract_state_cross_section -- carve an active, single-state slice out of
the v1 tblRawStateData.csv export.

Given a state abbreviation (e.g. 'VA'), this looks up the matching nStateID in
wip/in/tblStates.csv (via the txtStateAbv column), then writes a new CSV
containing only coherent tblRawStateData families for that state.

Default mode keeps active families:

  * family parent nStateID == the resolved state's nStateID
  * family parent ynDeleted == 'FALSE'
  * family parent approve_status != 'Deleted'

Rows that look like continuations ("Same(...)", "(L)(...)", "(E)(...)",
marker continuations, and lowercase-leading fragments) belong to the nearest
prior fresh parent row in source order. If a parent row is deleted or otherwise
excluded, its continuation rows are dropped with it so downstream tools cannot
attach those children to the previous surviving parent.

The output preserves the full original column set and header order so the
slice stays a drop-in cross-section of the v1 source data. Text is normalized
conservatively: txtRawStateData whitespace runs collapse to one space, and
other fields are stripped at the edges.

Usage (run from the tools/ directory; exit code 0 on success):

    python3 extract_state_cross_section.py VA

Optional overrides:

    python3 extract_state_cross_section.py VA \
        --raw wip/in/tblRawStateData.csv \
        --states wip/in/tblStates.csv \
        --out wip/in/tblRawStateData_VA_active.csv

The old approved-only status policy remains available:

    python3 extract_state_cross_section.py VA --status approved

The v1-only fresh import path uses the not-deleted policy:

    python3 extract_state_cross_section.py VA --status not-deleted

Default output path, for abbreviation VA, is:

    wip/in/tblRawStateData_VA_active.csv
"""
import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# v1 cells (HTML blobs, manuscript memos) can exceed Python's default CSV field
# cap. Raise it well past anything in the export so DictReader never throws
# "field larger than field limit".
csv.field_size_limit(10 ** 9)

# Column name constants -- keep these aligned with the v1 export header.
ABV_COL = "txtStateAbv"
STATE_ID_COL = "nStateID"
APPROVE_COL = "approve_status"
DELETED_COL = "ynDeleted"
RAW_TEXT_COL = "txtRawStateData"
APPROVED_VALUE = "Approved"
DELETED_VALUE = "Deleted"
FALSE_VALUE = "FALSE"
PENDING_VALUE = "Pending"
STATUS_ACTIVE = "active"
STATUS_APPROVED = "approved"
STATUS_NOT_DELETED = "not-deleted"

# Continuation rules keep v1 family slices intact. A child row that begins with
# Same, (L), (E), a marker continuation, or a lowercase continuation inherits
# inclusion from its nearest parent row.
_REL_CONTINUATION_RE = re.compile(
    r"^\s*\+?(?:same\b|\*?[(\[{][le][)\]}]\*?)",
    re.IGNORECASE,
)
_MARKER_CONTINUATION_RE = re.compile(r"^\s*\(.\)\s*\(")
_LOWERCASE_CONTINUATION_RE = re.compile(r"^\s*[a-z]")
_LEADING_MARKERS_RE = re.compile(r"^\s*(?:\*+\s*)?(?:\(\s*1\s*\)\s*)?")
_WS_RUN_RE = re.compile(r"\s+")


@dataclass
class SliceStats:
    """Audit counters for one extraction run."""

    raw_rows_read: int = 0
    state_rows_read: int = 0
    families_seen: int = 0
    families_written: int = 0
    rows_written: int = 0
    families_dropped_inactive_parent: int = 0
    continuation_rows_dropped_inactive_parent: int = 0
    pending_rows_included: int = 0


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


def is_continuation_listing(text: str) -> bool:
    """Return True if text inherits catalog context from the prior family."""
    cleaned = _LEADING_MARKERS_RE.sub("", text or "")
    return bool(
        _REL_CONTINUATION_RE.match(cleaned)
        or _MARKER_CONTINUATION_RE.match(cleaned)
        or _LOWERCASE_CONTINUATION_RE.match(cleaned)
    )


def is_active_row(row: dict) -> bool:
    """Return True when the v1 row should be considered active catalog data."""
    return (
        (row.get(DELETED_COL) or "").strip().upper() == FALSE_VALUE
        and (row.get(APPROVE_COL) or "").strip() != DELETED_VALUE
    )


def row_matches_status(row: dict, status: str) -> bool:
    """Return True when row passes the selected extraction status policy."""
    if status == STATUS_NOT_DELETED:
        return (row.get(DELETED_COL) or "").strip().upper() == FALSE_VALUE
    if not is_active_row(row):
        return False
    if status == STATUS_APPROVED:
        return (row.get(APPROVE_COL) or "").strip() == APPROVED_VALUE
    return True


def normalize_listing_text(text: str) -> str:
    """Collapse listing whitespace runs and strip edges."""
    if not text:
        return ""
    return _WS_RUN_RE.sub(" ", text).strip()


def normalize_output_row(row: dict, fields: list[str]) -> dict:
    """Return row normalized for output while preserving the source schema."""
    normalized = {}
    for field in fields:
        value = row.get(field)
        if value is None:
            normalized[field] = ""
        elif field == RAW_TEXT_COL:
            normalized[field] = normalize_listing_text(value)
        else:
            normalized[field] = value.strip()
    return normalized


def flush_family(family: list[dict], writer, fields: list[str],
                 status: str, stats: SliceStats) -> None:
    """Write or drop one family according to its parent row."""
    if not family:
        return
    stats.families_seen += 1
    parent = family[0]
    if not row_matches_status(parent, status):
        stats.families_dropped_inactive_parent += 1
        stats.continuation_rows_dropped_inactive_parent += max(
            len(family) - 1, 0
        )
        return

    wrote_family = False
    for row in family:
        if not row_matches_status(row, status):
            continue
        writer.writerow(normalize_output_row(row, fields))
        stats.rows_written += 1
        if (row.get(APPROVE_COL) or "").strip() == PENDING_VALUE:
            stats.pending_rows_included += 1
        wrote_family = True
    if wrote_family:
        stats.families_written += 1


def write_slice(raw_path: Path, out_path: Path, state_id: str,
                status: str = STATUS_ACTIVE) -> SliceStats:
    """Stream raw_path -> out_path keeping coherent families for state_id.

    Returns audit counters. Exits(2) if the raw export is missing the columns
    this filter depends on.
    """
    with raw_path.open(newline="", encoding="utf-8") as src:
        reader = csv.DictReader(src)
        fields = reader.fieldnames
        if fields is None:
            sys.exit("error: {0} is empty".format(raw_path))
        for required in (STATE_ID_COL, APPROVE_COL, DELETED_COL, RAW_TEXT_COL):
            if required not in fields:
                sys.exit(
                    "error: {0} has no '{1}' column".format(raw_path, required)
                )

        stats = SliceStats()
        current_family = []
        with out_path.open("w", newline="", encoding="utf-8") as dst:
            writer = csv.DictWriter(dst, fieldnames=fields)
            writer.writeheader()
            for row in reader:
                stats.raw_rows_read += 1
                same_state = (row.get(STATE_ID_COL) or "").strip() == state_id
                if not same_state:
                    continue
                stats.state_rows_read += 1
                text = row.get(RAW_TEXT_COL) or ""
                if not current_family or not is_continuation_listing(text):
                    flush_family(current_family, writer, fields, status, stats)
                    current_family = [row]
                else:
                    current_family.append(row)
            flush_family(current_family, writer, fields, status, stats)
    return stats


def print_stats(stats: SliceStats) -> None:
    """Print an ASCII audit summary for this extraction run."""
    print("raw rows read: {0}".format(stats.raw_rows_read))
    print("state rows read: {0}".format(stats.state_rows_read))
    print("families seen: {0}".format(stats.families_seen))
    print("families written: {0}".format(stats.families_written))
    print("rows written: {0}".format(stats.rows_written))
    print(
        "families dropped because parent was inactive: {0}".format(
            stats.families_dropped_inactive_parent
        )
    )
    print(
        "continuation rows dropped with inactive parents: {0}".format(
            stats.continuation_rows_dropped_inactive_parent
        )
    )
    print("pending rows included: {0}".format(stats.pending_rows_included))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract a family-aware, single-state slice of v1 "
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
        help="Output CSV path (default: wip/in/tblRawStateData_<ABV>_active.csv)",
    )
    parser.add_argument(
        "--status",
        choices=(STATUS_ACTIVE, STATUS_APPROVED, STATUS_NOT_DELETED),
        default=STATUS_ACTIVE,
        help="Row status policy inside valid families (default: %(default)s)",
    )
    args = parser.parse_args()

    abbrev = args.abbrev.strip().upper()
    raw_path = Path(args.raw)
    states_path = Path(args.states)
    out_path = (
        Path(args.out)
        if args.out
        else Path("wip/in/tblRawStateData_{0}_{1}.csv".format(abbrev, args.status))
    )

    for path in (raw_path, states_path):
        if not path.is_file():
            sys.exit("error: file not found: {0}".format(path))

    state_id = resolve_state_id(states_path, abbrev)
    print("resolved {0} -> {1} = {2}".format(abbrev, STATE_ID_COL, state_id))

    stats = write_slice(raw_path, out_path, state_id, args.status)
    print(
        "wrote {0} {1} row(s) for {2} to {3}".format(
            stats.rows_written, args.status, abbrev, out_path
        )
    )
    print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
