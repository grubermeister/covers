"""Path and reference-work policy for the ASCC pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_OCR_REFERENCE_WORK = "ASCC5"
DEFAULT_V1_REFERENCE_WORK = "ASCC6"

IMPORT_STEMS = (
    "colors",
    "letterings",
    "shapes",
    "regions",
    "reference_works",
    "post_offices",
    "post_office_regions",
    "markings",
    "covers",
    "cover_valuations",
    "dates_seen",
    "cover_markings",
    "citations",
    "images",
    "marking_lineage",
)


@dataclass(frozen=True)
class PipelineRoots:
    repo_root: Path
    tools_dir: Path
    wip_dir: Path
    wip_in: Path
    wip_cache: Path
    wip_out: Path
    backend_media: Path


@dataclass(frozen=True)
class StatePaths:
    state: str
    basename: str
    pdf: Path
    ocr_rows: Path
    catalog_rows: Path
    images_dir: Path
    image_report: Path
    bundle_dir: Path
    compare_dir: Path
    compare_ledger: Path
    manifest: Path
    media_dir: Path


@dataclass(frozen=True)
class V1StatePaths:
    state: str
    catalog_rows: Path
    slice_rows: Path
    image_refs: Path
    bundle_dir: Path
    report: Path
    manifest: Path
    media_dir: Path


def normalize_state(value: str) -> str:
    state = str(value or "").strip().upper()
    if len(state) != 2 or not state.isalpha():
        raise ValueError("STATE must be a two-letter abbreviation, for example VA.")
    return state


def ocr_state_paths(state: str, roots: PipelineRoots) -> StatePaths:
    state = normalize_state(state)
    return StatePaths(
        state=state,
        basename=state,
        pdf=roots.wip_in / f"{state}.pdf",
        ocr_rows=roots.wip_cache / f"{state}_ocr_rows.csv",
        catalog_rows=roots.wip_cache / f"{state}_catalog_rows.csv",
        images_dir=roots.wip_cache / f"{state}_images",
        image_report=roots.wip_cache / f"{state}_subchunks_report.csv",
        bundle_dir=roots.wip_out / state.lower(),
        compare_dir=roots.wip_cache / "compare" / state,
        compare_ledger=roots.wip_cache / "compare" / state / f"review_ledger_{state}.csv",
        manifest=roots.wip_cache / f"{state}_run.json",
        media_dir=roots.backend_media / state.lower(),
    )


def v1_state_paths(state: str, roots: PipelineRoots) -> V1StatePaths:
    state = normalize_state(state)
    cache_dir = roots.wip_cache / "v1" / state
    return V1StatePaths(
        state=state,
        catalog_rows=cache_dir / "catalog_rows.csv",
        slice_rows=cache_dir / "slice.csv",
        image_refs=cache_dir / "image_refs.csv",
        bundle_dir=roots.wip_out / f"v1_{state.lower()}",
        report=roots.wip_out / f"v1_{state.lower()}" / "v1_reconciliation_report.csv",
        manifest=cache_dir / "run.json",
        media_dir=roots.backend_media / state.lower(),
    )

