#!/usr/bin/env bash
# Legacy compatibility wrapper for the old ASCC PDF OCR runner.
#
# New command:
#   ./woco ascc ocr STATE [--pdf tools/wip/in/STATE.pdf]
#
# This wrapper accepts the old BASE positional argument, derives STATE from
# its first two letters, and delegates to ./woco ascc ocr. Unsupported old
# flags fail with a clear message instead of silently changing output paths.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

usage() {
  sed -n '2,8p' "$0"
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

BASE=""
INPUT_DIR="tools/wip/in"
CMD_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider|--model|--pages)
      value="$(require_arg_value "$1" "${2:-}")"
      CMD_ARGS+=("$1" "$value")
      shift 2
      ;;
    --reference-work-code)
      value="$(require_arg_value "$1" "${2:-}")"
      CMD_ARGS+=(--reference-work "$value")
      shift 2
      ;;
    --input-dir)
      INPUT_DIR="$(require_arg_value "$1" "${2:-}")"
      shift 2
      ;;
    --check-import)
      CMD_ARGS+=(--import-check always)
      shift
      ;;
    --region-id|--out-dir|--no-clean-out|--skip-review|-v|--verbose)
      echo "run_ascc_pipeline.sh: $1 is not supported by the simplified wrapper." >&2
      echo "Use ./woco ascc ocr STATE for the supported OCR interface." >&2
      exit 2
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
  echo "Missing required BASE or STATE argument." >&2
  usage >&2
  exit 2
fi

STATE="$(printf '%s' "${BASE:0:2}" | tr '[:lower:]' '[:upper:]')"
if [[ ! "$STATE" =~ ^[A-Z][A-Z]$ ]]; then
  echo "run_ascc_pipeline.sh: could not derive a two-letter state from $BASE." >&2
  exit 2
fi

if [[ "$BASE" == *.pdf ]]; then
  CMD_ARGS+=(--pdf "$BASE")
elif [[ "$BASE" != "$STATE" ]]; then
  CMD_ARGS+=(--pdf "${INPUT_DIR%/}/${BASE}.pdf")
fi

echo "run_ascc_pipeline.sh is legacy; delegating to ./woco ascc ocr."
exec ./woco ascc ocr "$STATE" "${CMD_ARGS[@]}"
