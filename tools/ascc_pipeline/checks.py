"""Doctor check helpers for ASCC orchestration."""

from __future__ import annotations

import csv
from pathlib import Path


def check_item(name: str, ok: bool, detail: str, required: bool) -> dict[str, object]:
    return {"name": name, "ok": ok, "detail": detail, "required": required}


def check_path(name: str, path: Path, required: bool) -> dict[str, object]:
    return check_item(name, path.exists(), str(path), required)


def check_reference_work(input_dir: Path, code: str, required: bool = True) -> dict[str, object]:
    path = input_dir / "reference_works.csv"
    if not path.is_file():
        return check_item(f"reference work {code}", False, f"missing {path}", required)
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = [
                row for row in csv.DictReader(fh)
                if (row.get("code") or "").strip() == code
            ]
    except Exception as exc:
        return check_item(
            f"reference work {code}",
            False,
            f"{path} unreadable ({exc.__class__.__name__})",
            required,
        )
    if len(rows) == 1:
        return check_item(f"reference work {code}", True, str(path), required)
    return check_item(
        f"reference work {code}",
        False,
        f"{path} matched {len(rows)} rows",
        required,
    )

