#!/usr/bin/env bash
# Run the catalog data + image imports on staging. Invoke as the wocod user:
#   sudo -u wocod /srv/woco/tools/reload_data.sh
# Usually triggered remotely by tools/push_data.sh --import.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Run from repo root so 'uv run' finds pyproject.toml and .venv.
cd "$REPO_ROOT"

echo "[1/1] import_ascc_bundle --truncate"
uv run python backend/manage.py import_ascc_bundle tools/wip/out --truncate

#echo "[2/2] import_catalog_images"
#uv run python backend/manage.py import_catalog_images

echo "Done."
