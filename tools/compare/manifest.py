"""Manifest helpers for staged ASCC comparison runs."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return the sha256 hex digest for path."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def count_csv_rows(path: Path) -> int:
    """Return the number of data rows in a CSV file."""
    with path.open(newline="", encoding="utf-8") as fh:
        return max(sum(1 for _ in fh) - 1, 0)


def record_stage(
    manifest_path: Path,
    stage: str,
    inputs: list[Path],
    outputs: list[Path],
) -> None:
    """Append or replace one stage manifest entry.

    Manifest entry shape:
    {
      "stage": "stage0_slice",
      "ran_at": "2026-06-22T00:00:00Z",
      "inputs": [{"path": "tools/wip/in/a.csv", "sha256": "..."}],
      "outputs": [{"path": "tools/wip/out/b.csv", "rows": 2, "sha256": "..."}]
    }
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if manifest_path.exists():
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [e for e in entries if e.get("stage") != stage]
    entry = {
        "stage": stage,
        "ran_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "inputs": [_file_info(path, include_rows=False) for path in inputs],
        "outputs": [_file_info(path, include_rows=True) for path in outputs],
    }
    entries.append(entry)
    manifest_path.write_text(
        json.dumps(entries, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _file_info(path: Path, include_rows: bool) -> dict:
    info = {"path": str(path), "sha256": sha256_file(path)}
    if include_rows and path.suffix.lower() == ".csv":
        info["rows"] = count_csv_rows(path)
    return info


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """Write rows to path using the supplied stable field order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def read_csv(path: Path) -> list[dict]:
    """Read path into a list of dict rows."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

