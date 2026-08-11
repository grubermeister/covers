#!/usr/bin/env python3
"""Attach v1 townmark images to a munger-built ASCC bundle.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python tools/v1_attach_images.py \
        --state VA \
        --image-refs tools/wip/cache/v1/VA/image_refs.csv \
        --bundle-dir tools/wip/out/v1_va \
        --v1-image-root backups/images/virginia \
        --media-dir backend/media/va

Expected exit code: 0.

This stage intentionally attaches only images. It does not apply v1
split-column warnings and it does not edit non-image bundle tables.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from v1_bundle_overlay import (
    COVER_COLUMNS,
    COVER_MARKING_COLUMNS,
    IMAGE_COLUMNS,
    IMAGE_REF_COLUMNS,
    audit_from,
    build_images,
    build_source_map_indexes,
    read_csv,
    write_csv,
)


WARNING_COLUMNS = ["raw_id", "issue", "detail"]


def attach_images(args: argparse.Namespace) -> int:
    state = str(args.state or "").strip().upper()
    bundle_dir = Path(args.bundle_dir)
    source_map_path = bundle_dir / "source_marking_map.csv"
    images_path = bundle_dir / "images.csv"
    markings_path = bundle_dir / "markings.csv"
    covers_path = bundle_dir / "covers.csv"
    cover_markings_path = bundle_dir / "cover_markings.csv"

    image_ref_fields, image_refs = read_csv(Path(args.image_refs))
    missing_image_columns = [c for c in IMAGE_REF_COLUMNS if c not in image_ref_fields]
    if missing_image_columns:
        sys.exit("error: image refs missing columns: {0}".format(missing_image_columns))
    if not source_map_path.is_file():
        sys.exit("error: missing bundle CSV: {0}".format(source_map_path))
    if not markings_path.is_file():
        sys.exit("error: missing bundle CSV: {0}".format(markings_path))

    source_map_fields, source_map_rows = read_csv(source_map_path)
    marking_fields, markings = read_csv(markings_path)
    image_fields, existing_images = (
        read_csv(images_path) if images_path.is_file() else (IMAGE_COLUMNS, [])
    )
    cover_fields, covers = (
        read_csv(covers_path) if covers_path.is_file() else (COVER_COLUMNS, [])
    )
    cover_marking_fields, cover_markings = (
        read_csv(cover_markings_path)
        if cover_markings_path.is_file()
        else (COVER_MARKING_COLUMNS, [])
    )
    warnings = []
    _, tm_by_raw = build_source_map_indexes(source_map_rows)
    images, new_covers, new_cover_markings = build_images(
        state,
        image_refs,
        tm_by_raw,
        Path(args.v1_image_root),
        Path(args.media_dir),
        bool(args.allow_missing_v1_images),
        audit_from(existing_images or markings),
        warnings,
        covers,
    )
    # Cover-view images (v1 txtView Front/Back) need a cover to hang off; the
    # codes are allocated above the highest serial already in covers.csv.
    covers = covers + new_covers
    cover_markings = cover_markings + new_cover_markings

    write_csv(images_path, image_fields or IMAGE_COLUMNS, images)
    if covers:
        write_csv(covers_path, cover_fields or COVER_COLUMNS, covers)
    if cover_markings:
        write_csv(
            cover_markings_path,
            cover_marking_fields or COVER_MARKING_COLUMNS,
            cover_markings,
        )
    if args.warnings:
        write_csv(Path(args.warnings), WARNING_COLUMNS, warnings)

    print("v1 image refs: {0}".format(len(image_refs)))
    print("v1 images:     {0} -> {1}".format(len(images), images_path))
    if new_covers:
        print(
            "v1 covers:     +{0} -> {1} (cover-view images)".format(
                len(new_covers), covers_path
            )
        )
        print(
            "v1 cover_markings: +{0} -> {1}".format(
                len(new_cover_markings), cover_markings_path
            )
        )
    if warnings:
        print("v1 image warnings: {0} -> {1}".format(len(warnings), args.warnings))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attach v1 image files to a munger-built ASCC bundle."
    )
    parser.add_argument("--state", required=True)
    parser.add_argument("--image-refs", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--v1-image-root", required=True)
    parser.add_argument("--media-dir", required=True)
    parser.add_argument("--warnings", default=None)
    parser.add_argument(
        "--allow-missing-v1-images",
        action="store_true",
        help="write missing-image warning rows instead of failing",
    )
    args = parser.parse_args(argv)
    return attach_images(args)


if __name__ == "__main__":
    raise SystemExit(main())
