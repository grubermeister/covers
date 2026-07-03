"""State-centered ASCC pipeline CLI used by ./woco ascc.

This module keeps the demo-facing workflow small:

    ./woco ascc doctor VA
    ./woco ascc munge VA
    ./woco ascc run VA --dry-run
    ./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf

It preserves the existing tools/wip/in, tools/wip/cache, and tools/wip/out
layout. The older OCR tools remain stage implementations; this CLI owns the
public argument names, canonical filenames, and run manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import csv
from pathlib import Path

from dotenv import load_dotenv

from ascc_pipeline import checks as pipeline_checks
from ascc_pipeline import commands as pipeline_commands
from ascc_pipeline import manifest as pipeline_manifest
from ascc_pipeline import paths as pipeline_paths


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
WIP_DIR = TOOLS_DIR / "wip"
WIP_IN = WIP_DIR / "in"
WIP_CACHE = WIP_DIR / "cache"
WIP_OUT = WIP_DIR / "out"
BACKEND_MEDIA = REPO_ROOT / "backend" / "media"

IMPORT_STEMS = pipeline_paths.IMPORT_STEMS
DEFAULT_OCR_REFERENCE_WORK = pipeline_paths.DEFAULT_OCR_REFERENCE_WORK
DEFAULT_V1_REFERENCE_WORK = pipeline_paths.DEFAULT_V1_REFERENCE_WORK
StatePaths = pipeline_paths.StatePaths
V1StatePaths = pipeline_paths.V1StatePaths


class AsccCliError(Exception):
    """Raised for user-correctable ASCC CLI failures."""


def pipeline_roots() -> pipeline_paths.PipelineRoots:
    return pipeline_paths.PipelineRoots(
        repo_root=REPO_ROOT,
        tools_dir=TOOLS_DIR,
        wip_dir=WIP_DIR,
        wip_in=WIP_IN,
        wip_cache=WIP_CACHE,
        wip_out=WIP_OUT,
        backend_media=BACKEND_MEDIA,
    )


def state_paths(state: str) -> StatePaths:
    try:
        return pipeline_paths.ocr_state_paths(state, pipeline_roots())
    except ValueError as exc:
        raise AsccCliError(str(exc)) from exc


def v1_state_paths(state: str) -> V1StatePaths:
    try:
        return pipeline_paths.v1_state_paths(state, pipeline_roots())
    except ValueError as exc:
        raise AsccCliError(str(exc)) from exc


def normalize_state(value: str) -> str:
    try:
        return pipeline_paths.normalize_state(value)
    except ValueError as exc:
        raise AsccCliError(str(exc)) from exc


def discover_state_pdf(state: str) -> tuple[Path | None, str | None]:
    """Return the canonical or unique matching PDF for state.

    The canonical path is tools/wip/in/<STATE>.pdf. If it does not exist,
    exactly one case-insensitive tools/wip/in/<STATE>*.pdf match is accepted.
    """
    state = normalize_state(state)
    canonical = WIP_IN / f"{state}.pdf"
    if canonical.exists():
        return canonical, None
    matches = sorted(
        p for p in WIP_IN.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".pdf"
        and p.name.upper().startswith(state)
    ) if WIP_IN.exists() else []
    if not matches:
        return None, f"missing {canonical}"
    if len(matches) > 1:
        names = ", ".join(str(p) for p in matches)
        return None, f"multiple matching PDFs: {names}"
    return matches[0], None


def copy_state_pdf(state: str, source: Path | None) -> Path:
    """Ensure tools/wip/in/<STATE>.pdf exists and return that path."""
    paths = state_paths(state)
    WIP_IN.mkdir(parents=True, exist_ok=True)
    if source is None:
        found, error = discover_state_pdf(paths.state)
        if error:
            raise AsccCliError(error)
        source = found
    source = Path(source).expanduser()
    if not source.exists():
        raise AsccCliError(f"PDF not found: {source}")
    if source.resolve() != paths.pdf.resolve():
        shutil.copy2(source, paths.pdf)
    return paths.pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./woco ascc")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check v1 ASCC import prerequisites")
    doctor.add_argument("state")
    add_v1_options(doctor)

    munge = sub.add_parser("munge", help="build a fresh v2 bundle from v1 exports")
    munge.add_argument("state")
    add_v1_options(munge)

    run = sub.add_parser("run", help="build a fresh v2 bundle from v1 exports and import it")
    run.add_argument("state")
    add_v1_options(run)
    add_run_import_options(run)

    ocr = sub.add_parser("ocr", help="run the legacy PDF OCR pipeline")
    ocr.add_argument("state")
    ocr.add_argument("--pdf", type=Path, default=None)
    ocr.add_argument(
        "--force",
        action="store_true",
        help=(
            "rebuild OCR and catalog rows even when STATE_ocr_rows.csv or "
            "STATE_catalog_rows.csv already exists"
        ),
    )
    add_shared_options(ocr, include_import_check=True)

    compare = sub.add_parser("compare", help="run the legacy OCR compare ledger")
    compare.add_argument("state")
    add_shared_options(compare, include_import_check=False)

    import_bundle = sub.add_parser(
        "import",
        add_help=False,
        help="load an ASCC CSV bundle into Django",
    )
    import_bundle.add_argument(
        "import_args",
        nargs=argparse.REMAINDER,
        metavar="ARG",
        help=(
            "arguments passed to import_ascc_bundle, for example: "
            "tools/wip/out/v1_va --dry-run"
        ),
    )

    clean = sub.add_parser("clean", help="delete generated ASCC cache and output files")
    clean.add_argument("state", nargs="?")

    return parser


def add_shared_options(parser: argparse.ArgumentParser, include_import_check: bool) -> None:
    parser.add_argument("--provider", choices=("openrouter", "anthropic"), default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--pages", default=None)
    parser.add_argument("--reference-work", default=DEFAULT_OCR_REFERENCE_WORK)
    parser.add_argument("--legacy-status", choices=("active", "approved"), default="active")
    if include_import_check:
        parser.add_argument(
            "--import-check",
            choices=("auto", "always", "never"),
            default="auto",
            help=(
                "Run ./woco ascc import as a dry-run check after bundle "
                "generation. Use never to skip the database check."
            ),
        )


def add_v1_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference-work", default=DEFAULT_V1_REFERENCE_WORK)
    parser.add_argument(
        "--v1-image-root",
        type=Path,
        default=None,
        help=(
            "directory containing files named by tblTownmarkImages.txtFilename "
            "(default: V1_IMAGE_ROOT, backups/images/<state-name>, or "
            "tools/wip/in/v1_images)"
        ),
    )
    parser.add_argument(
        "--allow-missing-v1-images",
        action="store_true",
        default=False,
        help="write report rows for missing v1 image files instead of failing",
    )
    parser.add_argument(
        "--strict",
        dest="allow_missing_v1_images",
        action="store_false",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )


def add_run_import_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the generated bundle through import_ascc_bundle and roll back",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="delete catalog rows before importing the generated bundle",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated import stems to load in dependency order",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="skip missing bundle CSV stems during import",
    )
    parser.add_argument(
        "--skip-report",
        default=None,
        help="path for skipped-row diagnostics",
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["import"]:
        try:
            return command_import(argparse.Namespace(import_args=argv[1:]))
        except AsccCliError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return command_doctor(args)
        if args.command == "run":
            return command_run(args)
        if args.command == "munge":
            return command_munge(args)
        if args.command == "ocr":
            return command_ocr_run(args)
        if args.command == "compare":
            return command_compare(args)
        if args.command == "import":
            return command_import(args)
        if args.command == "clean":
            return command_clean(args)
    except AsccCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def command_doctor(args) -> int:
    return command_v1_doctor(args)


def command_run(args) -> int:
    paths, image_root = munge_v1_bundle(args)
    load_result = import_generated_bundle(paths, args)
    write_v1_run_manifest(
        paths,
        args,
        image_root,
        skipped_import_check(),
        load_result,
    )
    print()
    print(f"v1 catalog rows: {paths.catalog_rows}")
    print(f"v1 bundle:       {paths.bundle_dir}")
    print(f"v1 manifest:     {paths.manifest}")
    return 0


def command_ocr_run(args) -> int:
    paths = state_paths(args.state)
    copy_state_pdf(paths.state, args.pdf)
    require_doctor(paths.state, args.provider, args.reference_work)

    if args.force or not paths.catalog_rows.exists():
        if args.force or not paths.ocr_rows.exists():
            run_command(page_processor_cmd(paths, args))
            run_command(page_extract_cmd(paths, args))
        else:
            print()
            print(f"using existing OCR rows: {paths.ocr_rows}")
        run_command(image_extract_cmd(paths, args))
    else:
        print()
        print(f"using existing catalog rows: {paths.catalog_rows}")
    copied_images = copy_marking_images(paths)
    clean_bundle_dir(paths.bundle_dir)
    run_command(munger_cmd(paths, args))
    run_command(compare_cmd(paths, args))
    import_result = maybe_import_check(paths, args.import_check)
    write_run_manifest(paths, args, copied_images, import_result)
    print()
    print(f"catalog rows:   {paths.catalog_rows}")
    print(f"bundle:         {paths.bundle_dir}")
    print(f"compare ledger: {paths.compare_ledger}")
    print(f"manifest:       {paths.manifest}")
    return 0


def command_munge(args) -> int:
    paths, image_root = munge_v1_bundle(args)
    write_v1_run_manifest(
        paths,
        args,
        image_root,
        skipped_import_check(),
        skipped_load(),
    )
    print()
    print(f"v1 catalog rows: {paths.catalog_rows}")
    print(f"v1 bundle:       {paths.bundle_dir}")
    print(f"v1 manifest:     {paths.manifest}")
    return 0


def command_compare(args) -> int:
    paths = state_paths(args.state)
    require_file(paths.catalog_rows, "catalog rows")
    require_dir(paths.bundle_dir, "bundle")
    run_command(compare_cmd(paths, args))
    return 0


def command_import(args) -> int:
    if not args.import_args:
        raise AsccCliError(
            "usage: ./woco ascc import <directory> "
            "[--dry-run] [--truncate] [--only STEMS] [--allow-missing]"
        )
    run_command(import_bundle_cmd(args.import_args))
    return 0


def command_v1_doctor(args) -> int:
    state = normalize_state(args.state)
    checks = v1_doctor_checks(
        state,
        resolve_v1_image_root(args.v1_image_root, state),
        args.allow_missing_v1_images,
        args.reference_work,
    )
    print_doctor(f"v1 {state}", checks)
    return 0 if not any(c["required"] and not c["ok"] for c in checks) else 2


def munge_v1_bundle(args) -> tuple[V1StatePaths, Path]:
    paths = v1_state_paths(args.state)
    image_root = resolve_v1_image_root(args.v1_image_root, paths.state)
    require_v1_doctor(
        paths.state,
        image_root,
        args.allow_missing_v1_images,
        args.reference_work,
    )
    paths.catalog_rows.parent.mkdir(parents=True, exist_ok=True)
    clean_bundle_dir(paths.bundle_dir)
    run_command(v1_catalog_cmd(paths, args))
    run_command(munger_cmd(paths, args))
    run_command(v1_image_cmd(paths, args, image_root))
    run_command(v1_overlay_cmd(paths, args, image_root, preserve_images=True))
    return paths, image_root


def command_clean(args) -> int:
    removed = clean_generated(args.state)
    scope = normalize_state(args.state) if args.state else "all states"
    print(f"cleaned ASCC generated files for {scope}: {removed} item(s)")
    return 0


def doctor_checks(
    state: str,
    provider: str | None = None,
    reference_work: str = DEFAULT_OCR_REFERENCE_WORK,
) -> list[dict[str, object]]:
    paths = state_paths(state)
    found_pdf, pdf_error = discover_state_pdf(state)
    selected_provider = provider or os.environ.get("PIPELINE_LLM_PROVIDER") or "openrouter"
    if selected_provider == "anthropic":
        credential_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
        credential_name = "ANTHROPIC_API_KEY"
    else:
        credential_ok = bool(os.environ.get("OPENROUTER_API_KEY"))
        credential_name = "OPENROUTER_API_KEY"
    db_ok, db_message = check_db()
    return [
        check_item("pdf", found_pdf is not None, str(found_pdf or pdf_error), True),
        check_path("reference works", WIP_IN / "reference_works.csv", True),
        pipeline_checks.check_reference_work(WIP_IN, reference_work, True),
        check_path("regions", WIP_IN / "regions.csv", True),
        check_path("legacy states", WIP_IN / "tblStates.csv", True),
        check_path("legacy rows", WIP_IN / "tblRawStateData.csv", True),
        check_path("legacy images", WIP_IN / "tblTownmarkImages.csv", True),
        check_item("pdftoppm", shutil.which("pdftoppm") is not None, shutil.which("pdftoppm") or "not on PATH", True),
        check_item(credential_name, credential_ok, "set" if credential_ok else "missing", True),
        check_item("wip in", WIP_IN.exists(), str(WIP_IN), True),
        check_item("wip cache", WIP_CACHE.exists(), str(WIP_CACHE), True),
        check_item("wip out", WIP_OUT.exists(), str(WIP_OUT), True),
        check_item("database", db_ok, db_message, False),
        check_item("catalog rows", paths.catalog_rows.exists(), str(paths.catalog_rows), False),
        check_item("bundle", paths.bundle_dir.exists(), str(paths.bundle_dir), False),
        check_item("compare ledger", paths.compare_ledger.exists(), str(paths.compare_ledger), False),
    ]


def v1_doctor_checks(
    state: str,
    image_root: Path,
    allow_missing_images: bool,
    reference_work: str = DEFAULT_V1_REFERENCE_WORK,
) -> list[dict[str, object]]:
    paths = v1_state_paths(state)
    db_ok, db_message = check_db()
    image_required = not allow_missing_images
    return [
        check_path("reference works", WIP_IN / "reference_works.csv", True),
        pipeline_checks.check_reference_work(WIP_IN, reference_work, True),
        check_path("regions", WIP_IN / "regions.csv", True),
        check_path("legacy states", WIP_IN / "tblStates.csv", True),
        check_path("legacy rows", WIP_IN / "tblRawStateData.csv", True),
        check_path("legacy images", WIP_IN / "tblTownmarkImages.csv", True),
        check_path("v1 image root", image_root, image_required),
        check_item("wip in", WIP_IN.exists(), str(WIP_IN), True),
        check_item("wip cache", WIP_CACHE.exists(), str(WIP_CACHE), True),
        check_item("wip out", WIP_OUT.exists(), str(WIP_OUT), True),
        check_item("database", db_ok, db_message, False),
        check_item("v1 catalog rows", paths.catalog_rows.exists(), str(paths.catalog_rows), False),
        check_item("v1 bundle", paths.bundle_dir.exists(), str(paths.bundle_dir), False),
    ]


def check_path(name: str, path: Path, required: bool) -> dict[str, object]:
    return pipeline_checks.check_path(name, path, required)


def check_item(name: str, ok: bool, detail: str, required: bool) -> dict[str, object]:
    return pipeline_checks.check_item(name, ok, detail, required)


def print_doctor(state: str, checks: list[dict[str, object]]) -> None:
    print(f"ASCC doctor {state}")
    for check in checks:
        if check["ok"]:
            status = "OK"
        elif check["required"]:
            status = "FAIL"
        else:
            status = "SKIP"
        print(f"  {status:<4} {check['name']:<18} {check['detail']}")


def require_doctor(
    state: str,
    provider: str | None,
    reference_work: str = DEFAULT_OCR_REFERENCE_WORK,
) -> None:
    failures = [
        c for c in doctor_checks(
            state,
            provider=provider,
            reference_work=reference_work,
        )
        if c["required"] and not c["ok"]
    ]
    if failures:
        details = "; ".join(f"{c['name']}: {c['detail']}" for c in failures)
        raise AsccCliError(details)


def require_v1_doctor(
    state: str,
    image_root: Path,
    allow_missing_images: bool,
    reference_work: str = DEFAULT_V1_REFERENCE_WORK,
) -> None:
    failures = [
        c for c in v1_doctor_checks(
            state,
            image_root,
            allow_missing_images,
            reference_work,
        )
        if c["required"] and not c["ok"]
    ]
    if failures:
        details = "; ".join(f"{c['name']}: {c['detail']}" for c in failures)
        raise AsccCliError(details)


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise AsccCliError(f"missing {label}: {path}")


def require_dir(path: Path, label: str) -> None:
    if not path.is_dir():
        raise AsccCliError(f"missing {label}: {path}")


def page_processor_cmd(paths: StatePaths, args) -> list[str]:
    cmd = [sys.executable, str(TOOLS_DIR / "ascc_page_processor.py"), paths.basename]
    add_provider_args(cmd, args)
    if args.pages:
        cmd.extend(["--pages", args.pages])
    return cmd


def page_extract_cmd(paths: StatePaths, args) -> list[str]:
    cmd = [
        sys.executable,
        str(TOOLS_DIR / "ascc_page_extract.py"),
        paths.basename,
        "--output-csv",
        str(paths.ocr_rows),
    ]
    add_provider_args(cmd, args)
    if args.pages:
        cmd.extend(["--pages", args.pages])
    return cmd


def image_extract_cmd(paths: StatePaths, args) -> list[str]:
    cmd = [
        sys.executable,
        str(TOOLS_DIR / "ascc_image_extract.py"),
        paths.basename,
        "--ocr-rows",
        str(paths.ocr_rows),
        "--catalog-rows-out",
        str(paths.catalog_rows),
        "--strict",
    ]
    if args.pages:
        cmd.extend(["--pages", args.pages])
    return cmd


def munger_cmd(paths: StatePaths, args) -> list[str]:
    return [
        sys.executable,
        str(TOOLS_DIR / "ascc_data_munger.py"),
        "--input",
        str(paths.catalog_rows),
        "--input-dir",
        str(WIP_IN),
        "--out-dir",
        str(paths.bundle_dir),
        "--reference-work-code",
        args.reference_work,
        "--region-abbrev",
        paths.state,
    ]


def v1_catalog_cmd(paths: V1StatePaths, args) -> list[str]:
    return [
        sys.executable,
        str(TOOLS_DIR / "v1_catalog_rows.py"),
        paths.state,
        "--raw",
        str(WIP_IN / "tblRawStateData.csv"),
        "--states",
        str(WIP_IN / "tblStates.csv"),
        "--images",
        str(WIP_IN / "tblTownmarkImages.csv"),
        "--slice-out",
        str(paths.slice_rows),
        "--catalog-rows-out",
        str(paths.catalog_rows),
        "--image-refs-out",
        str(paths.image_refs),
        "--region-abbrev",
        paths.state,
    ]


def v1_overlay_cmd(
    paths: V1StatePaths,
    args,
    image_root: Path,
    preserve_images: bool = False,
) -> list[str]:
    cmd = [
        sys.executable,
        str(TOOLS_DIR / "v1_bundle_overlay.py"),
        "--state",
        paths.state,
        "--slice",
        str(paths.slice_rows),
        "--image-refs",
        str(paths.image_refs),
        "--bundle-dir",
        str(paths.bundle_dir),
        "--v1-image-root",
        str(image_root),
        "--media-dir",
        str(paths.media_dir),
        "--report",
        str(paths.report),
    ]
    if args.allow_missing_v1_images:
        cmd.append("--allow-missing-v1-images")
    if preserve_images:
        cmd.append("--preserve-images")
    return cmd


def v1_image_cmd(paths: V1StatePaths, args, image_root: Path) -> list[str]:
    cmd = [
        sys.executable,
        str(TOOLS_DIR / "v1_attach_images.py"),
        "--state",
        paths.state,
        "--image-refs",
        str(paths.image_refs),
        "--bundle-dir",
        str(paths.bundle_dir),
        "--v1-image-root",
        str(image_root),
        "--media-dir",
        str(paths.media_dir),
        "--report",
        str(paths.report),
    ]
    if args.allow_missing_v1_images:
        cmd.append("--allow-missing-v1-images")
    return cmd


def compare_cmd(paths: StatePaths, args) -> list[str]:
    return [
        sys.executable,
        str(TOOLS_DIR / "ascc_compare.py"),
        paths.state,
        "--all",
        "--status",
        args.legacy_status,
        "--catalog-rows",
        str(paths.catalog_rows),
        "--bundle-dir",
        str(paths.bundle_dir),
    ]


def import_bundle_cmd(import_args: list[str]) -> list[str]:
    return pipeline_commands.import_bundle_cmd(REPO_ROOT, import_args)


def run_import_args(args) -> list[str]:
    return pipeline_commands.run_import_args(args)


def import_generated_bundle(paths: V1StatePaths, args) -> dict[str, object]:
    import_args = [str(paths.bundle_dir), *run_import_args(args)]
    run_command(import_bundle_cmd(import_args))
    return {
        "mode": "dry-run" if args.dry_run else "load",
        "status": "passed",
        "check": "import_ascc_bundle",
        "args": import_args,
    }


def skipped_import_check() -> dict[str, object]:
    return {"mode": "manual", "status": "skipped", "check": "dry-run-additive"}


def skipped_load() -> dict[str, object]:
    return {"mode": "manual", "status": "skipped", "check": "additive-load"}


def add_provider_args(cmd: list[str], args) -> None:
    if args.provider:
        cmd.extend(["--provider", args.provider])
    if args.model:
        cmd.extend(["--model", args.model])


def run_command(cmd: list[str]) -> None:
    pipeline_commands.run_command(cmd, REPO_ROOT)


def clean_bundle_dir(bundle_dir: Path) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for stem in IMPORT_STEMS:
        (bundle_dir / f"{stem}.csv").unlink(missing_ok=True)


def clean_generated(state: str | None = None) -> int:
    """Delete generated ASCC cache/out artifacts.

    Without state, this clears generated files under tools/wip/cache and
    tools/wip/out for all states. It intentionally does not touch tools/wip/in,
    because that directory holds input PDFs and seed/reference CSVs.
    """
    if state:
        return clean_generated_state(normalize_state(state))
    removed = 0
    removed += clean_directory_contents(WIP_CACHE)
    removed += clean_directory_contents(WIP_OUT)
    WIP_CACHE.mkdir(parents=True, exist_ok=True)
    WIP_OUT.mkdir(parents=True, exist_ok=True)
    return removed


def clean_generated_state(state: str) -> int:
    removed = 0
    paths = state_paths(state)
    cache_prefixes = state_cache_prefixes(state)
    if WIP_CACHE.exists():
        for path in WIP_CACHE.iterdir():
            if path.name == "compare":
                continue
            if any(path.name.startswith(prefix) for prefix in cache_prefixes):
                removed += remove_path(path)
    removed += remove_path(paths.compare_dir)
    removed += remove_path(paths.bundle_dir)
    removed += remove_path(WIP_CACHE / "v1" / state)
    removed += remove_path(WIP_OUT / f"v1_{state.lower()}")
    return removed


def state_cache_prefixes(state: str) -> tuple[str, ...]:
    """Return cache filename prefixes owned by one state.

    Supports the canonical wrapper prefix, e.g. VA_ocr_rows.csv, plus the
    historical basename style, e.g. VA-ASCC-CTLG_chunks.
    """
    return (
        f"{state}_",
        f"{state}-",
        f"{state}.",
    )


def clean_directory_contents(directory: Path) -> int:
    removed = 0
    if not directory.exists():
        return removed
    for path in directory.iterdir():
        if path.name == "_DELETE.ME":
            continue
        removed += remove_path(path)
    return removed


def remove_path(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return 1


def copy_marking_images(paths: StatePaths) -> int:
    paths.media_dir.mkdir(parents=True, exist_ok=True)
    images = sorted(paths.images_dir.glob("*.png"))
    for image in images:
        shutil.copy2(image, paths.media_dir / image.name)
    print()
    print(f"copied marking images: {len(images)} -> {paths.media_dir}")
    return len(images)


def check_db() -> tuple[bool, str]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "backend" / "manage.py"),
        "shell",
        "-c",
        "from django.db import connection; connection.ensure_connection()",
    ]
    try:
        subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=True,
        )
    except Exception as exc:
        return False, f"not available ({exc.__class__.__name__})"
    return True, "available"


def maybe_import_check(paths: StatePaths, mode: str) -> dict[str, object]:
    check_kind = "dry-run-additive"
    if mode == "never":
        return {"mode": mode, "status": "skipped", "check": check_kind}
    db_ok, db_message = check_db()
    if mode == "auto" and not db_ok:
        print()
        print(f"import dry-run skipped: database {db_message}")
        return {
            "mode": mode,
            "status": "skipped",
            "check": check_kind,
            "reason": db_message,
        }
    cmd = import_bundle_cmd([
        str(paths.bundle_dir),
        "--dry-run",
    ])
    run_command(cmd)
    return {"mode": mode, "status": "passed", "check": check_kind}


def count_csv_rows(path: Path) -> int | None:
    return pipeline_manifest.count_csv_rows(path)


def image_status_counts(path: Path) -> dict[str, int]:
    return pipeline_manifest.image_status_counts(path)


def write_run_manifest(
    paths: StatePaths,
    args,
    copied_images: int,
    import_result: dict[str, object],
) -> None:
    """Write tools/wip/cache/<STATE>_run.json.

    Example output shape:
    {
      "state": "VA",
      "paths": {"catalog_rows": "tools/wip/cache/VA_catalog_rows.csv"},
      "counts": {"catalog_rows": 1596},
      "import_check": {
        "mode": "auto",
        "status": "passed",
        "check": "dry-run-additive"
      }
    }
    """
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "state": paths.state,
        "provider": args.provider or os.environ.get("PIPELINE_LLM_PROVIDER") or "openrouter",
        "model": args.model or os.environ.get("PIPELINE_LLM_MODEL") or "",
        "reference_work": args.reference_work,
        "legacy_status": args.legacy_status,
        "paths": {
            "pdf": str(paths.pdf),
            "ocr_rows": str(paths.ocr_rows),
            "catalog_rows": str(paths.catalog_rows),
            "images_dir": str(paths.images_dir),
            "bundle": str(paths.bundle_dir),
            "compare_ledger": str(paths.compare_ledger),
        },
        "counts": {
            "ocr_rows": count_csv_rows(paths.ocr_rows),
            "catalog_rows": count_csv_rows(paths.catalog_rows),
            "bundle_markings": count_csv_rows(paths.bundle_dir / "markings.csv"),
            "compare_ledger": count_csv_rows(paths.compare_ledger),
            "copied_images": copied_images,
        },
        "image_status_counts": image_status_counts(paths.image_report),
        "import_check": import_result,
    }
    paths.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def resolve_v1_image_root(value: Path | None, state: str | None = None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    env_value = os.environ.get("V1_IMAGE_ROOT")
    if env_value:
        return Path(env_value).expanduser()
    backup_root = REPO_ROOT / "backups" / "images"
    if state:
        state_dir = backup_root / v1_state_image_slug(state)
        if state_dir.exists():
            return state_dir
    if backup_root.exists():
        return backup_root
    return WIP_IN / "v1_images"


def v1_state_image_slug(state: str) -> str:
    """Return the backups/images directory slug for a state abbrev.

    The backup image tree is keyed by lower-case state names with spaces
    replaced by dashes, for example:
    backups/images/virginia
    backups/images/west-virginia
    """
    state = normalize_state(state)
    states_path = WIP_IN / "tblStates.csv"
    if states_path.is_file():
        csv.field_size_limit(10 ** 9)
        with states_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if (row.get("txtStateAbv") or "").strip().upper() == state:
                    name = (row.get("txtState") or "").strip()
                    if name:
                        return slugify_v1_image_dir(name)
    return state.lower()


def slugify_v1_image_dir(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return slug.strip("-")


def write_v1_run_manifest(
    paths: V1StatePaths,
    args,
    image_root: Path,
    import_result: dict[str, object],
    load_result: dict[str, object],
) -> None:
    """Write tools/wip/cache/v1/<STATE>/run.json.

    Example output shape:
    {
      "state": "VA",
      "source": "v1",
      "paths": {"catalog_rows": "tools/wip/cache/v1/VA/catalog_rows.csv"},
      "counts": {"catalog_rows": 1596}
    }
    """
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "state": paths.state,
        "source": "v1",
        "mode": "munger-plus-v1-reconciliation",
        "reference_work": args.reference_work,
        "v1_image_root": str(image_root),
        "paths": {
            "slice": str(paths.slice_rows),
            "catalog_rows": str(paths.catalog_rows),
            "image_refs": str(paths.image_refs),
            "bundle": str(paths.bundle_dir),
            "image_report": str(paths.report),
            "reconciliation_report": str(paths.report),
        },
        "counts": {
            "slice": count_csv_rows(paths.slice_rows),
            "catalog_rows": count_csv_rows(paths.catalog_rows),
            "image_refs": count_csv_rows(paths.image_refs),
            "bundle_markings": count_csv_rows(paths.bundle_dir / "markings.csv"),
            "bundle_images": count_csv_rows(paths.bundle_dir / "images.csv"),
            "image_report": count_csv_rows(paths.report),
            "reconciliation_report": count_csv_rows(paths.report),
        },
        "import_check": import_result,
        "load": load_result,
    }
    paths.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
