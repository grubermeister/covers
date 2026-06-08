#!/usr/bin/env bash
# Run the catalog data + image imports on staging. Invoke as the wocod user:
#   sudo -u wocod /srv/woco/tools/reload_data.sh
# Usually triggered remotely by tools/push_data.sh --import.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Run from repo root so 'uv run' finds pyproject.toml and .venv.
cd "$REPO_ROOT"

echo "[1/2] import_ascc_bundle ASCC1 --truncate"
uv run python backend/manage.py import_ascc_bundle tools/wip/cache/ascc1 --truncate

echo "[2/2] apply_ascc2_overlay"
uv run python backend/manage.py apply_ascc2_overlay \
  --base-dir tools/wip/cache/ascc1 \
  --overlay-dir tools/wip/cache/ascc2_overlay_bundle \
  --overlay-map tools/wip/out/VA_ASCC2_overlay_map.csv \
  --v1-image-refs tools/wip/in/v1_VA_image_refs.csv \
  --region-abbrev VA \
  --ascc1-code ASCC1 \
  --ascc2-code ASCC2 \
  --audit-user-id "${WOCO_ASCC_AUDIT_USER_ID:-1}" \
  --skip-missing-images

#echo "[2/2] import_catalog_images"
#uv run python backend/manage.py import_catalog_images

echo "Done."
