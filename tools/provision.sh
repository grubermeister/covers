#!/usr/bin/env bash
# Provision a fresh Ubuntu 24.04 LTS host into a WorldCovers app server.
#
# This reproduces the prod host layout documented in docs/devel/DEPLOY.md
# (gunicorn under systemd behind nginx, MySQL 8, uv/Python 3.13, Node 22) so a
# disposable staging box (e.g. woco.dev) matches hellowoco.app without cloning
# its disk. It codifies the nginx / certbot / MySQL / user steps that previously
# lived only on the prod box.
#
# PREREQUISITE: the repo must already be checked out at /srv/woco. Clone it as
# root first (auth is yours to provide -- deploy key or token), then run this:
#
#   git clone <repo-url> /srv/woco && /srv/woco/tools/provision.sh
#
# This script does NOT issue the TLS cert: `.dev` forces HTTPS but certbot needs
# DNS pointing at this host first. After DNS resolves, run (see the printed
# next-steps and docs/devel/STAGING_WOCO_DEV.md):
#
#   certbot --nginx -d "$WOCO_HOSTNAME" --redirect -m <email> --agree-tos -n
#
# Idempotent: re-running is safe. Existing mysql.cnf / backend/.env are kept
# unless WOCO_FORCE=1 is set (a fresh secret/password would otherwise rotate).
#
# Tunables (env vars):
#   WOCO_HOSTNAME   public hostname for ALLOWED_HOSTS/CSRF (default: woco.dev)
#   WOCO_APP_USER   service account that owns /srv/woco (default: wocod)
#   WOCO_ROOT       repo checkout path (default: /srv/woco)
#   WOCO_REPO_REF   git ref to deploy (default: staging)
#   WOCO_FORCE      1 = regenerate mysql.cnf and backend/.env (default: unset)

set -euo pipefail

HOSTNAME_APP="${WOCO_HOSTNAME:-woco.dev}"
APP_USER="${WOCO_APP_USER:-wocod}"
ROOT="${WOCO_ROOT:-/srv/woco}"
REPO_REF="${WOCO_REPO_REF:-staging}"
FORCE="${WOCO_FORCE:-0}"
NODE_MAJOR=22

log() { printf '\n=== %s ===\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root (sudo $0)." >&2
  exit 1
fi
if [[ ! -f "${ROOT}/pyproject.toml" || ! -d "${ROOT}/tools" ]]; then
  echo "Repo not found at ${ROOT}. Clone it there first, then re-run." >&2
  echo "  git clone <repo-url> ${ROOT}" >&2
  exit 1
fi

log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  nginx certbot python3-certbot-nginx \
  mysql-server \
  git curl ca-certificates \
  build-essential pkg-config python3-dev \
  default-libmysqlclient-dev libjpeg-dev zlib1g-dev \
  ufw

log "Installing Node ${NODE_MAJOR} (NodeSource)"
if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | sed 's/v\([0-9]*\).*/\1/')" -lt $NODE_MAJOR ]]; then
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
  apt-get install -y nodejs
fi
node -v && npm -v

log "Creating service user '${APP_USER}'"
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$APP_USER"
fi
chown -R "${APP_USER}:${APP_USER}" "$ROOT"

log "Installing uv + Python 3.13 (as ${APP_USER})"
# uv lands in ~/.local/bin for the app user; deploy.sh and gunicorn run as that
# user. `uv sync` also auto-fetches the .python-version pin, but install it
# explicitly so the first sync is fast and deterministic.
sudo -u "$APP_USER" -H bash -lc '
  set -e
  command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.13
'

log "Checking out ${REPO_REF}"
sudo -u "$APP_USER" -H git -C "$ROOT" fetch origin --quiet || true
sudo -u "$APP_USER" -H git -C "$ROOT" checkout "$REPO_REF" || true

log "Setting up MySQL database, user, and grants"
# Fresh MySQL 8 authenticates root via auth_socket, so `mysql` as root works
# with no password. MySQL 8 does not auto-create users on GRANT, so CREATE USER
# explicitly. Mirrors tools/setup_worldcovers_db.sql plus the user creation.
MYSQL_CNF="${ROOT}/mysql.cnf"
if [[ -f "$MYSQL_CNF" && "$FORCE" != "1" ]]; then
  echo "mysql.cnf exists; reusing its password (WOCO_FORCE=1 to regenerate)."
  DB_PASS="$(awk -F'= *' '/^password/{print $2; exit}' "$MYSQL_CNF")"
else
  DB_PASS="$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))')"
fi
mysql <<SQL
CREATE DATABASE IF NOT EXISTS worldcovers CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${APP_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${APP_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON worldcovers.* TO '${APP_USER}'@'localhost';
GRANT ALL PRIVILEGES ON test_worldcovers.* TO '${APP_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

if [[ ! -f "$MYSQL_CNF" || "$FORCE" == "1" ]]; then
  log "Writing ${MYSQL_CNF}"
  cat > "$MYSQL_CNF" <<CNF
[client]
user = ${APP_USER}
password = ${DB_PASS}
default-character-set = utf8mb4
CNF
  chown "${APP_USER}:${APP_USER}" "$MYSQL_CNF"
  chmod 600 "$MYSQL_CNF"
fi

log "Writing ${ROOT}/backend/.env"
ENV_FILE="${ROOT}/backend/.env"
if [[ -f "$ENV_FILE" && "$FORCE" != "1" ]]; then
  echo "backend/.env exists; keeping it (WOCO_FORCE=1 to regenerate)."
else
  SECRET="$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(64)))')"
  cat > "$ENV_FILE" <<ENV
DJANGO_SETTINGS_MODULE=woco.settings
PYTHONPATH=backend
DEBUG=False
DB_NAME=worldcovers
DJANGO_APP_HOSTNAME=${HOSTNAME_APP}
DJANGO_SECRET_KEY=${SECRET}
# Demo box: never send real mail. Outbound mail prints to the gunicorn journal.
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=WorldCovers <no-reply@${HOSTNAME_APP}>
# Pipeline LLM keys intentionally blank: the ASCC pipeline runs locally, not
# on this box. Data arrives via tools/push_data.sh + reload_data.sh.
PIPELINE_LLM_PROVIDER=openrouter
PIPELINE_LLM_MODEL=
OPENROUTER_API_KEY=
ANTHROPIC_API_KEY=
ENV
  chown "${APP_USER}:${APP_USER}" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

log "Installing systemd unit"
install -m 644 "${ROOT}/tools/worldcovers.service" /etc/systemd/system/worldcovers.service
systemctl daemon-reload
systemctl enable worldcovers

log "Installing staging unit helper"
install -o root -g root -m 0755 "${ROOT}/tools/worldcovers-apply-unit.sh" /usr/local/sbin/worldcovers-apply-unit

log "Installing sudoers drop-in for ${APP_USER} (staging deploy commands only)"
cat > /etc/sudoers.d/wocod-deploy <<SUDO
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl stop worldcovers
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl start worldcovers
${APP_USER} ALL=(root) NOPASSWD: /usr/local/sbin/worldcovers-apply-unit
SUDO
chmod 440 /etc/sudoers.d/wocod-deploy
visudo -cf /etc/sudoers.d/wocod-deploy

log "Installing nginx site (HTTP only; certbot adds TLS later)"
sed "s/__HOSTNAME__/${HOSTNAME_APP}/g; s#__ROOT__#${ROOT}#g" \
  "${ROOT}/tools/nginx-woco.conf.template" > /etc/nginx/sites-available/woco
ln -sf /etc/nginx/sites-available/woco /etc/nginx/sites-enabled/woco
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "Configuring firewall"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

log "Building + migrating app (deploy.sh as ${APP_USER})"
sudo -u "$APP_USER" -H bash -lc "cd '${ROOT}' && export PATH=\"\$HOME/.local/bin:\$PATH\" && ./tools/deploy.sh"

log "Starting service"
systemctl restart worldcovers
sleep 2
systemctl --no-pager --full status worldcovers | head -n 12 || true

cat <<NEXT

=== Provisioning complete ===
App is live over HTTP (gunicorn 127.0.0.1:8000 via nginx) but '${HOSTNAME_APP}'
is a .dev domain, so browsers require HTTPS. Finish setup:

  1. Point DNS at this host: A record ${HOSTNAME_APP} -> $(hostname -I | awk '{print $1}')
  2. Once DNS resolves, issue the cert:
       certbot --nginx -d ${HOSTNAME_APP} --redirect -m <you@example.com> --agree-tos -n
  3. Load data (from your local checkout):
       WOCO_HOST=root@${HOSTNAME_APP} ./tools/push_data.sh --import

See docs/devel/STAGING_WOCO_DEV.md for the full runbook.
NEXT
