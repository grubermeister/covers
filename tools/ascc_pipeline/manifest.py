"""Manifest helper functions for ASCC pipeline runs."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


def count_csv_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def image_status_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        return dict(Counter(row.get("Status", "") for row in reader))

