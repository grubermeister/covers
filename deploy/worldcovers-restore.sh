#!/usr/bin/env bash
# worldcovers-restore — restore a snapshot taken by worldcovers-backup.
# Installed to /usr/local/sbin/worldcovers-restore by deploy/install-backup.sh.
#
#   # rehearsal: load into a scratch DB, touch nothing else
#   worldcovers-restore --snapshot 2026-08-16T023007Z --into worldcovers_rehearsal
#
#   # the real thing: overwrite the live DB and media tree
#   worldcovers-restore --snapshot 2026-08-16T023007Z --into worldcovers \
#                       --i-understand-this-overwrites $(hostname)
#
# Run as the wocod user. A restore into any database other than $WOCO_DB_NAME is
# treated as a rehearsal: the service is left running and media is not touched.
#
# ALWAYS restore both halves together. The dump does not contain images --
# MEDIA_ROOT is a directory on disk (settings.py: MEDIA_ROOT = BASE_DIR/"media").
# A database-only restore yields a complete-looking catalog in which every image
# link is broken. See backups/2026-08-07/README.md.
set -euo pipefail

DEST="${WOCO_BACKUP_DIR:-/var/backups/woco}"
APP_ROOT="${WOCO_APP_ROOT:-/srv/woco}"
DB_NAME="${WOCO_DB_NAME:-worldcovers}"
MYSQL_CNF="${WOCO_MYSQL_CNF:-${APP_ROOT}/mysql.cnf}"
MEDIA_ROOT="${WOCO_MEDIA_ROOT:-${APP_ROOT}/backend/media}"
SERVICE="${WOCO_SERVICE:-worldcovers}"

# See the note in worldcovers-backup.sh: under `sudo -u wocod` the caller's
# working directory is inherited and may be unreadable to wocod, which makes
# GNU find fail after it has already done its work.
SELF="$(readlink -f "$0")"
cd /

SNAPSHOT=""; INTO=""; CONFIRM=""; SKIP_MEDIA=0

die() { echo "worldcovers-restore: $*" >&2; exit 1; }
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
usage() { sed -n '2,17p' "$SELF" >&2; }

while (( $# )); do
  case "$1" in
    --snapshot) SNAPSHOT="${2:-}"; shift ;;
    --into)     INTO="${2:-}"; shift ;;
    --i-understand-this-overwrites) CONFIRM="${2:-}"; shift ;;
    --skip-media) SKIP_MEDIA=1 ;;
    -h|--help)  usage; exit 0 ;;
    *)          die "unknown argument: $1" ;;
  esac
  shift
done

[[ -n "$SNAPSHOT" ]] || { usage; die "--snapshot is required"; }
[[ -n "$INTO" ]] || { usage; die "--into is required"; }
[[ "$INTO" =~ ^[A-Za-z0-9_]+$ ]] || die "invalid database name: $INTO"

SNAP_DIR="$DEST/snapshots/$SNAPSHOT"
[[ -d "$SNAP_DIR" ]] || die "no such snapshot: $SNAPSHOT (looked in $DEST/snapshots)"
[[ -f "$SNAP_DIR/MANIFEST.json" ]] || die "snapshot has no MANIFEST.json: $SNAPSHOT"

json_get() { sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$SNAP_DIR/MANIFEST.json" | head -1; }

IS_LIVE=0
if [[ "$INTO" == "$DB_NAME" ]]; then IS_LIVE=1; fi

if (( IS_LIVE )); then
  [[ "$CONFIRM" == "$(hostname)" ]] || die \
    "refusing to overwrite live database '$DB_NAME'.
  Pass --i-understand-this-overwrites $(hostname) if that is genuinely what you want.
  To rehearse instead, use --into ${DB_NAME}_rehearsal."
fi

MYSQL=(mysql --defaults-file="$MYSQL_CNF" --protocol=socket)

# --- 1. integrity ---------------------------------------------------------
log "[1/6] verifying snapshot integrity"
( cd "$SNAP_DIR" && sha256sum -c --quiet SHA256SUMS ) \
  || die "SHA256SUMS mismatch -- this snapshot is damaged, do not restore it"

DUMP_REL="$(json_get file)"
DUMP="$SNAP_DIR/$DUMP_REL"
[[ -f "$DUMP" ]] || die "dump missing from snapshot: $DUMP_REL"

# --- 2. engine family gate ------------------------------------------------
# ISSUE-2026-08-10-01 as a machine check rather than a paragraph in a README:
# woco.dev is MySQL 8.0 and prod is MariaDB 10.11, Django emits different DDL
# per engine from the same migrations, and a MariaDB dump aborts on MySQL at
# `uuid` columns and `longtext ... CHECK (json_valid(...))`. A cross-engine
# restore fails HALFWAY THROUGH, which is worse than not starting.
log "[2/6] engine check"
SNAP_FAMILY="$(sed -n 's/.*"family"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SNAP_DIR/MANIFEST.json" | head -1)"
SNAP_VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SNAP_DIR/MANIFEST.json" | head -1)"
LIVE_VERSION="$("${MYSQL[@]}" -N -B -e 'SELECT VERSION()')"
case "$LIVE_VERSION" in
  *MariaDB*|*mariadb*) LIVE_FAMILY="mariadb" ;;
  *)                   LIVE_FAMILY="mysql" ;;
esac

# Cross-check the manifest against the dump's own header, so a hand-edited or
# mismatched manifest cannot talk us past this gate.
# `zstdcat | head` makes head close the pipe, zstdcat take SIGPIPE, and
# `set -o pipefail` turn that into a fatal error. The trailing `|| true` is what
# keeps reading the first few lines of a large dump from killing the script.
HEADER_VERSION="$( { zstdcat "$DUMP" 2>/dev/null | head -40 | \
  sed -n 's/^-- Server version[[:space:]]*\(.*\)$/\1/p' | head -1; } || true )"
case "$HEADER_VERSION" in
  *MariaDB*|*mariadb*) HEADER_FAMILY="mariadb" ;;
  "")                  HEADER_FAMILY="$SNAP_FAMILY" ;;   # older dumps
  *)                   HEADER_FAMILY="mysql" ;;
esac

if [[ "$SNAP_FAMILY" != "$HEADER_FAMILY" ]]; then
  die "manifest says '$SNAP_FAMILY' but the dump header says '$HEADER_FAMILY' ($HEADER_VERSION).
  Refusing: the snapshot's own metadata disagrees with its contents."
fi
if [[ "$SNAP_FAMILY" != "$LIVE_FAMILY" ]]; then
  die "engine family mismatch -- refusing.
  snapshot: $SNAP_FAMILY ($SNAP_VERSION)
  server:   $LIVE_FAMILY ($LIVE_VERSION)
  Dumps are not portable between MySQL and MariaDB (ISSUE-2026-08-10-01):
  a MariaDB dump aborts on MySQL at 'uuid' columns and json_valid() CHECKs.
  Restore this snapshot on a $SNAP_FAMILY server instead."
fi
log "  snapshot=$SNAP_FAMILY $SNAP_VERSION  server=$LIVE_FAMILY $LIVE_VERSION  OK"

# --- 3. stop the app (live restores only) ---------------------------------
STOPPED=0
if (( IS_LIVE )); then
  log "[3/6] stopping $SERVICE"
  # This NOPASSWD grant already exists: /etc/sudoers.d/wocod-deploy (provision.sh).
  if sudo -n /bin/systemctl stop "$SERVICE"; then
    STOPPED=1
  else
    die "could not stop $SERVICE (the NOPASSWD grant in /etc/sudoers.d/wocod-deploy is missing?).
  Refusing to restore under a running app: it would write to a half-restored database."
  fi
  trap 'if (( STOPPED )); then log "restarting $SERVICE after failure"; sudo -n /bin/systemctl start "$SERVICE" || true; fi' EXIT
else
  log "[3/6] rehearsal into '$INTO' -- leaving $SERVICE running"
fi

# --- 4. load --------------------------------------------------------------
log "[4/6] loading into $INTO"
"${MYSQL[@]}" -e "CREATE DATABASE IF NOT EXISTS \`$INTO\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
if (( IS_LIVE )); then
  "${MYSQL[@]}" -e "DROP DATABASE \`$INTO\`; CREATE DATABASE \`$INTO\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
fi
zstdcat "$DUMP" | "${MYSQL[@]}" "$INTO"

# --- 5. assert ------------------------------------------------------------
# A missing table is a HARD failure. A row-count difference is REPORTED, not
# fatal: the manifest census is taken from the live DB a few seconds before
# mysqldump's START TRANSACTION, so a write inside that window is a legitimate
# off-by-a-few. Structure must match exactly; counts are evidence for a human.
log "[5/6] asserting against manifest"
python3 - "$SNAP_DIR/MANIFEST.json" "$INTO" "$MYSQL_CNF" <<'PY'
import json, subprocess, sys
manifest, dbname, cnf = sys.argv[1], sys.argv[2], sys.argv[3]
m = json.load(open(manifest))
expected = m["db"]["row_counts"]

def q(sql):
    out = subprocess.run(["mysql", f"--defaults-file={cnf}", "--protocol=socket",
                          "-N", "-B", "-e", sql, dbname],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()

live_tables = set(q("SELECT table_name FROM information_schema.tables "
                    f"WHERE table_schema='{dbname}' AND table_type='BASE TABLE'"))
missing = sorted(set(expected) - live_tables)
extra = sorted(live_tables - set(expected))
if missing:
    print(f"  FAIL: {len(missing)} table(s) missing after restore: {', '.join(missing[:8])}")
    sys.exit(1)
if extra:
    print(f"  note: {len(extra)} table(s) present that the manifest did not list: {', '.join(extra[:8])}")

sql = " UNION ALL ".join(f"SELECT '{t}', COUNT(*) FROM `{t}`" for t in expected)
actual = {}
for line in q(sql):
    name, cnt = line.split("\t")
    actual[name] = int(cnt)

drift = [(t, expected[t], actual[t]) for t in expected if actual.get(t) != expected[t]]
total_e, total_a = sum(expected.values()), sum(actual.values())
print(f"  {len(expected)} tables, {total_a} rows restored (manifest recorded {total_e})")
if drift:
    print(f"  {len(drift)} table(s) differ from the manifest census:")
    for t, e, a in drift[:12]:
        print(f"    {t:<34} manifest={e:<9} restored={a}")
    print("  (expected only if the database was written to during the dump window)")
else:
    print("  every table matches the manifest census exactly")
PY

# --- 6. media -------------------------------------------------------------
if (( IS_LIVE )) && (( ! SKIP_MEDIA )); then
  log "[6/6] restoring media"
  if [[ -d "$MEDIA_ROOT" ]]; then
    ASIDE="${MEDIA_ROOT}.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
    # A rename on the same filesystem: instant, and trivially reversible if the
    # restore turns out to be wrong.
    mv "$MEDIA_ROOT" "$ASIDE"
    log "  previous media moved aside: $ASIDE"
  fi
  mkdir -p "$MEDIA_ROOT"
  # A plain copy, NOT `cp -al`. Hardlinking the live tree to the snapshot would
  # make any in-place write corrupt the backup. 30 seconds is not worth that.
  rsync -a "$SNAP_DIR/media/" "$MEDIA_ROOT/"
  log "  $(find "$MEDIA_ROOT" -type f | wc -l) files restored to $MEDIA_ROOT"
else
  log "[6/6] media skipped ($( (( IS_LIVE )) && echo '--skip-media' || echo 'rehearsal' ))"
fi

if (( STOPPED )); then
  trap - EXIT
  log "starting $SERVICE"
  sudo -n /bin/systemctl start "$SERVICE"
fi

log "RESTORED $SNAPSHOT -> $INTO"
