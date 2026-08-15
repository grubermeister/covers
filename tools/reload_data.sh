#!/usr/bin/env bash
# Run one explicit ASCC bundle import on staging. Invoke as the wocod user:
#   sudo -u wocod /srv/woco/tools/reload_data.sh tools/wip/out/v1_va
# Usually triggered remotely by tools/push_data.sh --import --state STATE.

set -euo pipefail

usage() {
  sed -n '2,4p' "$0" >&2
}

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BUNDLE_DIR="${1:-${WOCO_IMPORT_BUNDLE:-}}"
if [[ -z "$BUNDLE_DIR" ]]; then
  echo "reload_data.sh: missing bundle directory." >&2
  usage
  exit 2
fi

NORMALIZED_BUNDLE="${BUNDLE_DIR%/}"
if [[ "$NORMALIZED_BUNDLE" == "tools/wip/out" ]]; then
  echo "reload_data.sh: refusing to import bare tools/wip/out." >&2
  echo "Pass a concrete bundle such as tools/wip/out/v1_va." >&2
  exit 2
fi

if [[ ! -d "$NORMALIZED_BUNDLE" ]]; then
  echo "reload_data.sh: bundle directory not found: $NORMALIZED_BUNDLE" >&2
  exit 2
fi

# --truncate DELETEs all 14 catalog tables before reloading, so take a tagged
# snapshot first. Guarding here rather than in push_data.sh covers BOTH entry
# points -- the remote `push_data.sh --import` and a human running this script
# on the box -- and needs no sudo, since this script already runs as wocod.
# shellcheck source=tools/pre_change_backup.sh
. "$REPO_ROOT/tools/pre_change_backup.sh"
pre_change_backup "import-$(basename "$NORMALIZED_BUNDLE")" || exit 2

echo "[1/1] ascc import --truncate $NORMALIZED_BUNDLE"
./woco ascc import "$NORMALIZED_BUNDLE" --truncate

echo "Done."
