#!/usr/bin/env bash
# Rebuild staging database: ensure DB exists, migrate, create admin, run imports.
# Uses database "worldcovers" by default. One-time setup: run tools/setup_worldcovers_db.sql as MySQL root.
# Run from repo root. Requires mysql.cnf in repo root (with database=worldcovers) and CSV files in backend/imports/.
# Usage: WOCO_ADMIN_PASSWORD=... ./tools/rebuild_staging_db.sh [--no-import]
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

DB_NAME="${DB_NAME:-worldcovers}"
MYSQL_CNF="${REPO_ROOT}/mysql.cnf"
# Path to CSVs relative to backend/ (e.g. imports or /srv/woco/backend/imports on server)
IMPORT_DIR="${IMPORT_DIR:-imports}"

if [[ "${1:-}" == "--no-import" ]]; then
  SKIP_IMPORT=1
else
  SKIP_IMPORT=0
fi

echo "[1/5] Ensuring database '${DB_NAME}' exists..."
if [[ ! -f "$MYSQL_CNF" ]]; then
  echo "Error: mysql.cnf not found at $MYSQL_CNF. Create it from mysql.cnf.example." >&2
  exit 1
fi
# One-time: run tools/setup_worldcovers_db.sql as root to create DB and grant wocod
if ! mysql --defaults-file="$MYSQL_CNF" --database="${DB_NAME}" -e "SELECT 1;" 2>/dev/null; then
  echo "Error: Cannot use database '${DB_NAME}'. Create it and grant the mysql.cnf user access, e.g.:" >&2
  echo "  mysql -u root -p < tools/setup_worldcovers_db.sql" >&2
  exit 1
fi

export DB_NAME
echo "[2/5] Running migrations..."
uv run python backend/manage.py migrate --noinput

# The admin password comes from WOCO_ADMIN_PASSWORD and is only set when the
# user is first created — a re-run must never reset an existing admin's
# password (this box is internet-facing).
echo "[3/5] Ensuring admin user..."
if [[ -z "${WOCO_ADMIN_PASSWORD:-}" ]]; then
  echo "Error: WOCO_ADMIN_PASSWORD is not set. Export a strong password first, e.g.:" >&2
  echo "  WOCO_ADMIN_PASSWORD=\"\$(openssl rand -base64 18)\" ./tools/rebuild_staging_db.sh" >&2
  echo "  (and record it — it is only applied when the admin user is first created)" >&2
  exit 1
fi
WOCO_ADMIN_PASSWORD="$WOCO_ADMIN_PASSWORD" uv run python backend/manage.py shell -c "
import os;
from django.contrib.auth import get_user_model;
User = get_user_model();
u, created = User.objects.get_or_create(username='admin', defaults={'is_superuser': True, 'is_staff': True});
if created:
    u.set_password(os.environ['WOCO_ADMIN_PASSWORD']); u.save(); print('Admin user created.')
else:
    print('Admin user already exists; password left unchanged.')
"

echo "[4/5] Creating Site for Django..."
SITE_DOMAIN="${DJANGO_APP_HOSTNAME:-hellowoco.app}" uv run python backend/manage.py shell -c "
import os;
from django.contrib.sites.models import Site;
s, _ = Site.objects.get_or_create(pk=1, defaults={'domain': 'example.com', 'name': 'WorldCovers'});
s.domain = os.environ['SITE_DOMAIN'];
s.name = 'WorldCovers';
s.save();
print('Site updated.')
"

echo "[5/5] Running full import (reference + legacy + ASCC)..."
if [[ $SKIP_IMPORT -eq 1 ]]; then
  echo "Skipped (--no-import). Run manually: uv run python backend/manage.py import_apmc_bundle backend/imports --allow-missing"
else
  if [[ ! -d "backend/$IMPORT_DIR" ]]; then
    echo "Warning: Import dir backend/$IMPORT_DIR not found. Run import manually: uv run python backend/manage.py import_apmc_bundle backend/imports --allow-missing" >&2
  else
    uv run python backend/manage.py import_apmc_bundle "backend/$IMPORT_DIR" --allow-missing
  fi
fi

echo "Done. Restart the app (e.g. sudo systemctl restart worldcovers)."
