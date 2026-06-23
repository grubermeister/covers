#!/usr/bin/env python3
"""Thin CLI for the staged ASCC v1/v2 comparison harness.

Run from repo root:
    uv run python tools/ascc_compare.py VA --all --status active \
        --catalog-rows tools/wip/cache/VA_catalog_rows.csv \
        --bundle-dir tools/wip/out/va

Expected exit code: 0.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from compare.stages import (
    default_paths,
    stage0_slice,
    stage1_project,
    stage2_family,
    stage3_align,
    stage4_fields,
    stage5_preservation,
    stage6_ledger,
)
from extract_state_cross_section import STATUS_ACTIVE, STATUS_APPROVED


STAGES = [
    stage0_slice,
    stage1_project,
    stage2_family,
    stage3_align,
    stage4_fields,
    stage5_preservation,
    stage6_ledger,
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state", help="state abbreviation, for example VA")
    parser.add_argument("--stage", type=int, choices=range(0, 7), default=None)
    parser.add_argument("--all", action="store_true", help="run all stages")
    parser.add_argument("--status", choices=[STATUS_ACTIVE, STATUS_APPROVED], default=STATUS_ACTIVE)
    parser.add_argument("--catalog-rows", type=Path, default=None)
    parser.add_argument("--bundle-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.all and args.stage is None:
        parser.error("choose --all or --stage N")

    paths = default_paths(args.state, args.catalog_rows, args.bundle_dir)
    if args.all:
        selected = range(0, 7)
    else:
        selected = [args.stage]

    for number in selected:
        func = STAGES[number]
        if number == 0:
            out = func(paths, status=args.status)
        else:
            out = func(paths)
        if isinstance(out, list):
            shown = ", ".join(str(p) for p in out)
        else:
            shown = str(out)
        print(f"stage {number}: {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
