#!/usr/bin/env bash
# Run the ASCC PDF-to-import-bundle pipeline from repo root.
#
# Cwd: this script cd's to the repository root before running commands.
# Required input:
#   tools/wip/in/<BASE>.pdf
#   tools/wip/in/reference_works.csv
#   tools/wip/in/regions.csv
# Required env vars for vision stages:
#   OPENROUTER_API_KEY, or ANTHROPIC_API_KEY with --provider anthropic.
# Expected exit code on success: 0.
#
# Output:
#   tools/wip/cache/<BASE>_chunks/             reviewed chunk PNG handoff
#   tools/wip/cache/<BASE>.csv                 raw OCR CSV from vision
#   tools/wip/cache/<BASE>_catalog_rows.csv    verified catalog rows
#   backend/media/<region>/                    copied marking images
#   tools/wip/out/                             import_ascc_bundle-ready CSVs
#
# Usage:
#   ./tools/run_ascc_pipeline.sh VA_ASCC_CTLG
#   ./tools/run_ascc_pipeline.sh WV-ASCC-CTLG --reference-work-code ASCC1
#   ./tools/run_ascc_pipeline.sh WV-ASCC-CTLG --region-id 56
#   ./tools/run_ascc_pipeline.sh VA_ASCC_CTLG --provider anthropic --check-import

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BASE=""
PROVIDER=""
MODEL=""
PAGES=""
REGION_ID=""
REFERENCE_WORK_CODE="ASCC1"
INPUT_DIR="tools/wip/in"
OUT_DIR="tools/wip/out"
CHECK_IMPORT=0
CLEAN_OUT=1
SKIP_REVIEW=0
VERBOSE=0

usage() {
  sed -n '2,22p' "$0"
}

require_arg_value() {
  local flag="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    echo "$flag requires a value." >&2
    exit 2
  fi
  printf '%s' "$value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider)
      PROVIDER="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --model)
      MODEL="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --pages)
      PAGES="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --region-id)
      REGION_ID="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --reference-work-code)
      REFERENCE_WORK_CODE="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --input-dir)
      INPUT_DIR="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --check-import)
      CHECK_IMPORT=1
      shift
      ;;
    --no-clean-out)
      CLEAN_OUT=0
      shift
      ;;
    --skip-review)
      SKIP_REVIEW=1
      shift
      ;;
    -v|--verbose)
      VERBOSE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
    *)
      if [[ -n "$BASE" ]]; then
        echo "Unexpected positional argument: $1" >&2
        exit 2
      fi
      BASE="$1"
      shift
      ;;
  esac
done

if [[ -z "$BASE" ]]; then
  echo "Missing required BASE argument." >&2
  usage >&2
  exit 2
fi

if [[ "$PAGES" == *":pdf"* ]]; then
  echo "--pages with :pdf is not supported by this end-to-end runner." >&2
  echo "Use catalog page numbers so extract and image extraction receive the same filter." >&2
  exit 2
fi

PDF_PATH="${INPUT_DIR}/${BASE}.pdf"
CACHE_CSV="tools/wip/cache/${BASE}.csv"
CACHE_CATALOG_ROWS_CSV="tools/wip/cache/${BASE}_catalog_rows.csv"
CACHE_IMAGES_DIR="tools/wip/cache/${BASE}_images"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
}

run_cmd() {
  echo
  echo "==> $*"
  "$@"
}

region_slug() {
  uv run python -c 'import sys; sys.path.insert(0, "tools"); from munger.images import catalog_image_slug; print(catalog_image_slug(sys.argv[1]))' "$1"
}

clean_out_dir() {
  local dir="$1"
  mkdir -p "$dir"
  local stems=(
    colors
    letterings
    shapes
    regions
    reference_works
    post_offices
    post_office_regions
    markings
    covers
    cover_valuations
    dates_seen
    cover_markings
    citations
    images
    marking_lineage
  )
  local stem
  for stem in "${stems[@]}"; do
    rm -f "${dir%/}/${stem}.csv"
  done
}

require_file "$PDF_PATH"
require_file "${INPUT_DIR%/}/reference_works.csv"
require_file "${INPUT_DIR%/}/regions.csv"

REGION_SLUG="$(region_slug "$BASE")"
MEDIA_DIR="backend/media/${REGION_SLUG}"

processor_args=("$BASE")
extract_args=("$BASE")
image_args=("$BASE")

if [[ -n "$PROVIDER" ]]; then
  processor_args+=(--provider "$PROVIDER")
  extract_args+=(--provider "$PROVIDER")
fi
if [[ -n "$MODEL" ]]; then
  processor_args+=(--model "$MODEL")
  extract_args+=(--model "$MODEL")
fi
if [[ -n "$PAGES" ]]; then
  processor_args+=(--pages "$PAGES")
  extract_args+=(--pages "$PAGES")
  image_args+=(--pages "$PAGES")
fi
if [[ -n "$REGION_ID" ]]; then
  extract_args+=(--region-id "$REGION_ID")
fi
if [[ "$SKIP_REVIEW" -eq 1 ]]; then
  processor_args+=(--skip-review)
fi
if [[ "$VERBOSE" -eq 1 ]]; then
  processor_args+=(--verbose)
  extract_args+=(--verbose)
  image_args+=(--verbose)
fi

echo "ASCC pipeline"
echo "  base:       $BASE"
echo "  pdf:        $PDF_PATH"
echo "  cache csv:  $CACHE_CSV"
echo "  catalog rows: $CACHE_CATALOG_ROWS_CSV"
echo "  media dir:  $MEDIA_DIR"
echo "  out dir:    $OUT_DIR"
echo "  reference:  $REFERENCE_WORK_CODE"
if [[ -n "$REGION_ID" ]]; then
  echo "  region id:  $REGION_ID"
fi

run_cmd uv run python tools/ascc_page_processor.py "${processor_args[@]}"
run_cmd uv run python tools/ascc_page_extract.py "${extract_args[@]}"
run_cmd uv run python tools/ascc_image_extract.py "${image_args[@]}" \
  --ocr-rows "$CACHE_CSV" \
  --strict \
  --catalog-rows-out "$CACHE_CATALOG_ROWS_CSV"

mkdir -p "$MEDIA_DIR"
shopt -s nullglob
marking_images=("${CACHE_IMAGES_DIR}"/*.png)
if [[ "${#marking_images[@]}" -gt 0 ]]; then
  run_cmd cp "${marking_images[@]}" "$MEDIA_DIR/"
else
  echo
  echo "==> no marking images found under ${CACHE_IMAGES_DIR}; skipping media copy"
fi
shopt -u nullglob

if [[ "$CLEAN_OUT" -eq 1 ]]; then
  echo
  echo "==> cleaning known generated bundle CSVs from ${OUT_DIR}"
  clean_out_dir "$OUT_DIR"
fi

run_cmd uv run python tools/ascc_data_munger.py \
  --input "$CACHE_CATALOG_ROWS_CSV" \
  --input-dir "$INPUT_DIR" \
  --out-dir "$OUT_DIR" \
  --reference-work-code "$REFERENCE_WORK_CODE"

if [[ "$CHECK_IMPORT" -eq 1 ]]; then
  run_cmd uv run python backend/manage.py import_ascc_bundle "$OUT_DIR" --dry-run
fi

echo
echo "Done. Importable bundle is in ${OUT_DIR}."
echo "Next step: uv run python backend/manage.py import_ascc_bundle ${OUT_DIR} --dry-run"
