"""Shared ASCC catalog-row CSV schema helpers.

The public ASCC pipeline boundary uses snake_case column names so a state run
can be explained without translating historic script vocabulary:

listing_text,catalog_page,chunk_number,image_count,row_type,is_manuscript,default_shape,institutional_owner

Older parser internals still use the original title-case names. Keep that
translation in one small module so future callers do not guess at the mapping.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping


CANONICAL_COLUMNS = [
    "listing_text",
    "catalog_page",
    "chunk_number",
    "image_count",
    "row_type",
    "is_manuscript",
    "default_shape",
    "institutional_owner",
]

REQUIRED_CANONICAL_COLUMNS = CANONICAL_COLUMNS[:5]
OPTIONAL_CANONICAL_COLUMNS = CANONICAL_COLUMNS[5:]

LEGACY_COLUMNS = [
    "Listing",
    "Page",
    "Chunk",
    "Images Above",
    "Type",
    "Manuscript",
    "Default Shape",
    "Institutional Ownership",
]

REQUIRED_LEGACY_COLUMNS = LEGACY_COLUMNS[:5]
OPTIONAL_LEGACY_COLUMNS = LEGACY_COLUMNS[5:]

CANONICAL_TO_LEGACY = dict(zip(CANONICAL_COLUMNS, LEGACY_COLUMNS))
LEGACY_TO_CANONICAL = dict(zip(LEGACY_COLUMNS, CANONICAL_COLUMNS))


def canonicalize_row(row: Mapping[str, object]) -> dict[str, object]:
    """Return one row in the public snake_case catalog-row shape."""
    out = {}
    for canonical, legacy in CANONICAL_TO_LEGACY.items():
        if canonical in row:
            out[canonical] = row.get(canonical, "")
        else:
            out[canonical] = row.get(legacy, "")
    return out


def legacy_row(row: Mapping[str, object]) -> dict[str, object]:
    """Return one row in the title-case shape used by old parser internals."""
    out = {}
    for canonical, legacy in CANONICAL_TO_LEGACY.items():
        if legacy in row:
            out[legacy] = row.get(legacy, "")
        else:
            out[legacy] = row.get(canonical, "")
    return out


def write_catalog_rows(path: Path, rows: list[Mapping[str, object]]) -> None:
    """Write rows using the canonical public catalog-row column order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=CANONICAL_COLUMNS,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(canonicalize_row(row))


def read_catalog_rows(path: Path) -> list[dict[str, str]]:
    """Read a catalog-row CSV and return canonical row dictionaries."""
    with Path(path).open(newline="") as fh:
        reader = csv.DictReader(fh)
        _validate_headers(path, reader.fieldnames)
        return [canonicalize_row(row) for row in reader]


def read_legacy_dataframe(path: Path):
    """Read a catalog-row CSV and return a DataFrame in legacy column shape.

    The munger currently has a large internal surface keyed on title-case
    names. This function is the only supported bridge from the public
    snake_case CSV to that older internal shape.
    """
    import pandas as pd

    path = Path(path)
    df = pd.read_csv(path)
    _validate_headers(path, list(df.columns))
    rename_map = {
        canonical: legacy
        for canonical, legacy in CANONICAL_TO_LEGACY.items()
        if canonical in df.columns
    }
    if rename_map:
        df = df.rename(columns=rename_map)
    for col in OPTIONAL_LEGACY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df


def _validate_headers(path: Path, fieldnames: list[str] | None) -> None:
    if fieldnames is None:
        raise ValueError(f"{path} is empty")
    fields = set(fieldnames)
    missing_canonical = [
        name for name in REQUIRED_CANONICAL_COLUMNS if name not in fields
    ]
    missing_legacy = [name for name in REQUIRED_LEGACY_COLUMNS if name not in fields]
    if missing_canonical and missing_legacy:
        raise ValueError(
            "{0} must use catalog-row columns {1}; missing {2}".format(
                path,
                ",".join(REQUIRED_CANONICAL_COLUMNS),
                missing_canonical,
            )
        )
