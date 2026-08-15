#!/usr/bin/env bash
# Rehearse a restore from a pulled snapshot, on this workstation. Repo root.
#
#   ./tools/rehearse_restore.sh --host woco-dev --latest --with-media
#   ./tools/rehearse_restore.sh --host woco-dev --snapshot 2026-08-16T023007Z
#
# A backup nobody has restored is not a backup. This is the step that turns
# "the dump exists and hashes match" into "this snapshot demonstrably rebuilds
# the site", and it is the only thing that exercises the MEDIA half -- which,
# as of the 2026-08-07 manual backup, had never been restored anywhere.
#
# woco.dev runs MySQL 8.0.46 and so does this workstation, so its rehearsal is a
# plain local load into a scratch database -- no container, no tarball, no root.
# Prod runs MariaDB 10.11; its dumps are NOT loadable here (ISSUE-2026-08-10-01)
# and worldcovers-restore refuses them, so prod rehearsal needs the standalone
# MariaDB tarball recipe in backups/2026-08-07/README.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
LOCAL_BASE="${WOCO_LOCAL_BACKUPS:-$(cd "$REPO_ROOT/.." && pwd)/backups}"
SCRATCH_DB="${WOCO_REHEARSAL_DB:-worldcovers_rehearsal}"
MYSQL_CNF="${WOCO_MYSQL_CNF:-$REPO_ROOT/mysql.cnf}"

HOST="woco-dev"; SNAPSHOT=""; LATEST=0; WITH_MEDIA=0; KEEP=0
while (( $# )); do
  case "$1" in
    --host)       HOST="${2:-}"; shift ;;
    --snapshot)   SNAPSHOT="${2:-}"; shift ;;
    --latest)     LATEST=1 ;;
    --with-media) WITH_MEDIA=1 ;;
    --keep)       KEEP=1 ;;
    -h|--help)    sed -n '2,8p' "$0" >&2; exit 0 ;;
    *)            echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { echo "rehearse_restore: $*" >&2; exit 1; }

SNAP_BASE="$LOCAL_BASE/$HOST/snapshots"
[[ -d "$SNAP_BASE" ]] || die "no local snapshots for '$HOST' -- run ./tools/pull_backups.sh first"

if (( LATEST )); then
  SNAPSHOT="$(ls -1 "$SNAP_BASE" | grep -E '^[0-9-]+T[0-9]+Z$' | sort | tail -1 || true)"
fi
[[ -n "$SNAPSHOT" ]] || die "specify --snapshot NAME or --latest"
SNAP_DIR="$SNAP_BASE/$SNAPSHOT"
[[ -d "$SNAP_DIR" ]] || die "no such local snapshot: $SNAP_DIR"

FAMILY="$(sed -n 's/.*"family"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SNAP_DIR/MANIFEST.json" | head -1)"
if [[ "$FAMILY" != "mysql" ]]; then
  die "snapshot engine is '$FAMILY'; this workstation runs MySQL.
  Cross-engine restore is not possible (ISSUE-2026-08-10-01). For a MariaDB
  snapshot, use the standalone-tarball recipe in backups/2026-08-07/README.md."
fi

log "rehearsing $HOST/$SNAPSHOT into $SCRATCH_DB"

# worldcovers-restore does the real work: SHA256SUMS, the engine gate, the load,
# and the manifest assertions. Reuse it rather than reimplementing -- rehearsing
# with different code than the real restore would rehearse the wrong thing.
WOCO_BACKUP_DIR="$LOCAL_BASE/$HOST" \
WOCO_MYSQL_CNF="$MYSQL_CNF" \
WOCO_DB_NAME="__none__" \
  ./deploy/worldcovers-restore.sh --snapshot "$SNAPSHOT" --into "$SCRATCH_DB"

if (( WITH_MEDIA )); then
  log "verifying the media half against the restored database"
  # The half a DB restore never exercises: does every Image row in this snapshot
  # resolve to a file, with the recorded sha256?
  DB_NAME="$SCRATCH_DB" uv run python backend/manage.py verify_media_integrity \
    --media-root "$SNAP_DIR/media" --check-checksums
fi

if (( KEEP )); then
  log "PASS -- $SCRATCH_DB left in place (--keep)"
else
  mysql --defaults-file="$MYSQL_CNF" -e "DROP DATABASE IF EXISTS \`$SCRATCH_DB\`"
  log "PASS -- scratch database dropped"
fi

cat <<DONE

Record this in docs/devel/BACKUP.md:
  $(date -u +%Y-%m-%d)  $HOST/$SNAPSHOT  PASS  (media=$( (( WITH_MEDIA )) && echo yes || echo no ))
DONE
