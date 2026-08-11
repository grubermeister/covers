#!/usr/bin/env bash
# Run this on the server after "git pull" (or in CI). From project root: ./deploy/deploy.sh
# Note: privileged operations (unit file install, daemon-reload, service restart) are handled
# by the caller; either the CI workflow or a sysadmin with sudo, not by this script.
set -e
cd "$(dirname "$0")/.."

echo "[1/4] Syncing Python dependencies (uv)..."
# --no-dev: skip the [dependency-groups] dev list (ipykernel, anthropic, ...).
# --frozen: fail if uv.lock is out of date instead of silently regenerating;
#           on a server we want the locked resolution or nothing.
uv sync --no-dev --frozen

echo "[2/4] Running migrations..."
uv run python backend/manage.py migrate --noinput

echo "[3/4] Building frontend (creates frontend/dist/)..."
# Load frontend/.env if present (not in git; create on server or set env vars in host dashboard).
if [ -f frontend/.env ]; then set -a; . frontend/.env; set +a; fi
# Clear dist so Vite can recreate it (avoids EACCES if dist was left by another user)
rm -rf frontend/dist
(cd frontend && npm ci && npm run build)

echo "[4/4] Collecting static files (Django)..."
uv run python backend/manage.py collectstatic --noinput

echo "Done."
