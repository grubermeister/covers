#!/usr/bin/env bash
# Wipe user-generated submission data (contributions, drafts, versions, recycle
# bins). Leaves the 14 catalog tables, auth Users, and editor Collection
# assignments untouched.
#
#   ./tools/wipe_user_data.sh              # wipe (prompts for confirmation)
#   ./tools/wipe_user_data.sh --dry-run    # report counts, change nothing
#   ./tools/wipe_user_data.sh --reload tools/wip/out/v1_va
#                                            # wipe (no prompt) THEN reload catalog
#
# --reload imports the explicit bundle afterward so you end up with a fresh,
# catalog-only system in one step. WOCO_IMPORT_BUNDLE may supply the bundle for
# non-interactive runs.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Run from repo root so 'uv run' finds pyproject.toml and .venv.
cd "$REPO_ROOT"

if [[ "${1:-}" == "--reload" ]]; then
  BUNDLE_DIR="${2:-${WOCO_IMPORT_BUNDLE:-}}"
  if [[ -z "$BUNDLE_DIR" ]]; then
    echo "wipe_user_data.sh: --reload requires a bundle directory." >&2
    echo "Example: ./tools/wipe_user_data.sh --reload tools/wip/out/v1_va" >&2
    exit 2
  fi
  NORMALIZED_BUNDLE="${BUNDLE_DIR%/}"
  if [[ "$NORMALIZED_BUNDLE" == "tools/wip/out" ]]; then
    echo "wipe_user_data.sh: refusing to import bare tools/wip/out." >&2
    exit 2
  fi
  # --reload wipes user data AND truncate-imports the catalog: the most
  # destructive path in the repo. Snapshot before either half runs.
  # shellcheck source=tools/pre_change_backup.sh
  . "$REPO_ROOT/tools/pre_change_backup.sh"
  pre_change_backup "wipe-$(basename "$NORMALIZED_BUNDLE")" || exit 2

  echo "[1/2] wipe_user_data --no-input"
  uv run python backend/manage.py wipe_user_data --no-input
  echo "[2/2] ascc import --truncate $NORMALIZED_BUNDLE"
  ./woco ascc import "$NORMALIZED_BUNDLE" --truncate
  echo "Done. Fresh catalog-only system."
else
  uv run python backend/manage.py wipe_user_data "$@"
fi
