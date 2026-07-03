#!/usr/bin/env bash
# One-command environment setup. Invoked via the woco shim:
#   ./woco setup dev    # local development environment (the README quickstart)
#   ./woco setup prod   # production/staging build steps (deploy/deploy.sh)
#
# 'dev' is idempotent and safe to re-run. On a fresh clone it scaffolds .env,
# creates or repairs mysql.cnf, creates the MySQL database/app user, builds the
# frontend, runs migrations, and collects static files.
#
# Optional non-interactive inputs:
#   WOCO_DB_NAME             default: DB_NAME from .env, then worldcovers
#   WOCO_DB_USER             default: wocod
#   WOCO_DB_PASSWORD         default: prompted, then generated if blank
#   WOCO_MYSQL_ROOT_PASSWORD default: use sudo mysql, or prompt on a tty
#   WOCO_SETUP_DB=1          re-run DB/user grants even when mysql.cnf exists
#
# 'prod' delegates to deploy/deploy.sh (uv sync --no-dev --frozen, migrate,
# frontend build, collectstatic). It does NOT provision a host; first-time
# host provisioning is deploy/provision.sh (see docs/devel/DEPLOY.md).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

usage() {
  echo "usage: ./woco setup <dev|prod>" >&2
  exit 2
}

MODE="${1:-}"
case "$MODE" in
  dev) ;;
  prod)
    echo "Running production build steps via deploy/deploy.sh ..."
    echo "Note: first-time host provisioning (user, MySQL, .env, systemd,"
    echo "nginx) is deploy/provision.sh -- see docs/devel/DEPLOY.md."
    exec "$REPO_ROOT/deploy/deploy.sh"
    ;;
  *) usage ;;
esac

read_env_file_value() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 0
  sed -n "s/^${key}=\([^#]*\).*/\1/p" "$file" | tail -1 | tr -d '[:space:]'
}

mysql_cnf_value() {
  local key="$1"
  [[ -f mysql.cnf ]] || return 0
  awk -F'= *' -v key="$key" '
    $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
      sub(/[[:space:]]+$/, "", $2)
      print $2
      exit
    }
  ' mysql.cnf
}

is_placeholder_password() {
  local value="$1"
  [[ -z "$value" || "$value" == "SuperSecureSecret123" || "$value" == "CHANGE_ME_BEFORE_RUNNING" ]]
}

validate_mysql_name() {
  local label="$1" value="$2"
  if [[ ! "$value" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "setup.sh: ${label} must contain only letters, digits, and underscore: ${value}" >&2
    exit 2
  fi
}

generate_db_password() {
  uv run python - <<'PY'
import secrets
import string

alphabet = string.ascii_letters + string.digits
print("".join(secrets.choice(alphabet) for _ in range(32)))
PY
}

write_mysql_cnf() {
  local db_user="$1" db_password="$2"
  cat > mysql.cnf <<CNF
[client]
user = ${db_user}
password = ${db_password}
default-character-set = utf8mb4
CNF
  chmod 600 mysql.cnf
}

write_bootstrap_sql() {
  local output_path="$1" db_name="$2" db_user="$3" db_password="$4"
  uv run python - "$output_path" "$db_name" "$db_user" "$db_password" <<'PY'
from pathlib import Path
import sys

output_path, db_name, db_user, db_password = sys.argv[1:5]

def quote_sql_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

db_ident = "`{}`".format(db_name)
test_db_ident = "`test_{}`".format(db_name)
user_literal = quote_sql_string(db_user)
password_literal = quote_sql_string(db_password)

sql = """CREATE DATABASE IF NOT EXISTS {db}
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS {user}@'localhost' IDENTIFIED BY {password};
ALTER USER {user}@'localhost' IDENTIFIED BY {password};
GRANT ALL PRIVILEGES ON {db}.* TO {user}@'localhost';
GRANT ALL PRIVILEGES ON {test_db}.* TO {user}@'localhost';
FLUSH PRIVILEGES;
""".format(
    db=db_ident,
    test_db=test_db_ident,
    user=user_literal,
    password=password_literal,
)

Path(output_path).write_text(sql, encoding="ascii")
PY
}

run_mysql_bootstrap() {
  local sql_file="$1"

  if [[ -n "${WOCO_MYSQL_ROOT_PASSWORD:-}" ]]; then
    mysql -u root --password="${WOCO_MYSQL_ROOT_PASSWORD}" < "$sql_file"
    return
  fi

  if sudo -n mysql -e "SELECT 1" >/dev/null 2>&1; then
    sudo mysql < "$sql_file"
    return
  fi

  if [[ -t 0 ]]; then
    echo
    echo "MySQL root access is needed to create the database and app user."
    echo "Leave the password blank to run 'sudo mysql' instead."
    local root_password=""
    read -r -s -p "MySQL root password: " root_password
    echo
    if [[ -n "$root_password" ]]; then
      mysql -u root --password="$root_password" < "$sql_file"
    else
      sudo mysql < "$sql_file"
    fi
    return
  fi

  cat >&2 <<'MSG'
setup.sh: cannot create the database without MySQL root access.
Set WOCO_MYSQL_ROOT_PASSWORD for an unattended run, or create the database
manually from tools/setup_worldcovers_db.sql and write matching credentials
to mysql.cnf.
MSG
  exit 2
}

echo "[1/6] Syncing Python dependencies (uv sync, includes dev group)..."
uv sync

echo "[2/6] Ensuring backend .env exists with a secret key..."
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "  created .env from .env.example"
fi
# Fill DJANGO_SECRET_KEY only if it is present-but-empty. Never overwrite an
# existing value. 'woco secretkey' works here because uv sync just installed
# Django; the value is passed via argv so shell-special characters are safe.
KEY="$("$REPO_ROOT/woco" secretkey)"
uv run python - .env "$KEY" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
key = sys.argv[2]
lines = path.read_text().splitlines()
changed = False
out = []
for line in lines:
    if line.strip() == "DJANGO_SECRET_KEY=":
        out.append("DJANGO_SECRET_KEY=" + key)
        changed = True
    else:
        out.append(line)
if changed:
    path.write_text("\n".join(out) + "\n")
    print("  set DJANGO_SECRET_KEY in .env")
else:
    print("  DJANGO_SECRET_KEY already set; leaving it unchanged")
PY

echo "[3/6] Ensuring MySQL database and mysql.cnf..."
EXISTING_DB_USER="$(mysql_cnf_value user || true)"
EXISTING_DB_PASSWORD="$(mysql_cnf_value password || true)"
SETUP_DB="${WOCO_SETUP_DB:-0}"

if [[ -f mysql.cnf && "$SETUP_DB" != "1" ]] && ! is_placeholder_password "$EXISTING_DB_PASSWORD"; then
  echo "  using existing mysql.cnf"
else
  DB_NAME_FROM_ENV="$(read_env_file_value DB_NAME .env || true)"
  DB_NAME_VALUE="${WOCO_DB_NAME:-${DB_NAME:-${DB_NAME_FROM_ENV:-worldcovers}}}"
  DB_USER_VALUE="${WOCO_DB_USER:-${EXISTING_DB_USER:-wocod}}"
  if [[ -n "${WOCO_DB_PASSWORD:-}" ]]; then
    DB_PASSWORD_VALUE="$WOCO_DB_PASSWORD"
  elif ! is_placeholder_password "$EXISTING_DB_PASSWORD"; then
    DB_PASSWORD_VALUE="$EXISTING_DB_PASSWORD"
  else
    DB_PASSWORD_VALUE=""
  fi

  if [[ -z "$DB_USER_VALUE" ]]; then
    DB_USER_VALUE="wocod"
  fi

  if [[ -t 0 ]]; then
    read -r -p "MySQL app database name [${DB_NAME_VALUE}]: " INPUT_DB_NAME
    DB_NAME_VALUE="${INPUT_DB_NAME:-$DB_NAME_VALUE}"
    read -r -p "MySQL app user [${DB_USER_VALUE}]: " INPUT_DB_USER
    DB_USER_VALUE="${INPUT_DB_USER:-$DB_USER_VALUE}"
    if [[ -z "$DB_PASSWORD_VALUE" ]]; then
      read -r -s -p "MySQL app password (leave blank to generate): " INPUT_DB_PASSWORD
      echo
      DB_PASSWORD_VALUE="$INPUT_DB_PASSWORD"
    fi
  fi

  if [[ -z "$DB_PASSWORD_VALUE" ]]; then
    DB_PASSWORD_VALUE="$(generate_db_password)"
    echo "  generated a random MySQL app password"
  fi

  validate_mysql_name "database name" "$DB_NAME_VALUE"
  validate_mysql_name "database user" "$DB_USER_VALUE"

  BOOTSTRAP_SQL="$(mktemp "${TMPDIR:-/tmp}/woco-db.XXXXXX.sql")"
  trap 'rm -f "${BOOTSTRAP_SQL:-}"' EXIT
  write_bootstrap_sql "$BOOTSTRAP_SQL" "$DB_NAME_VALUE" "$DB_USER_VALUE" "$DB_PASSWORD_VALUE"
  run_mysql_bootstrap "$BOOTSTRAP_SQL"
  write_mysql_cnf "$DB_USER_VALUE" "$DB_PASSWORD_VALUE"
  echo "  wrote mysql.cnf and ensured database '${DB_NAME_VALUE}' for user '${DB_USER_VALUE}'"
fi

echo "[4/6] Building frontend (npm ci && npm run build)..."
(cd frontend && npm ci && npm run build)

echo "[5/6] Running database migrations..."
"$REPO_ROOT/woco" migrate

echo "[6/6] Collecting static files..."
"$REPO_ROOT/woco" collectstatic --noinput

cat <<'MSG'

Setup complete. Start the dev server with:

  ./woco dev

MSG
