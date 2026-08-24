#!/usr/bin/env bash
# worldcovers-backup — nightly verified database + media snapshot.
# Installed to /usr/local/sbin/worldcovers-backup by deploy/install-backup.sh.
#
#   worldcovers-backup --scheduled        # nightly: verify -> publish -> prune
#   worldcovers-backup --tag <slug>       # pre-change snapshot; NEVER prunes
#   worldcovers-backup --verify-only NAME # re-verify an existing snapshot
#   worldcovers-backup --dry-run          # print what would happen, touch nothing
#
# Runs as the wocod user, which owns both /srv/woco/mysql.cnf and the media
# tree, so no sudo is needed at runtime. Root is required only at install.
#
# Layout under $WOCO_BACKUP_DIR (default /var/backups/woco):
#   snapshots/<name>/db/worldcovers.sql.zst   compressed dump
#   snapshots/<name>/db/dump.stderr           dumper stderr, kept as evidence
#   snapshots/<name>/media/                   hardlinked media tree
#   snapshots/<name>/MANIFEST.json            engine, row counts, media census
#   snapshots/<name>/SHA256SUMS               dump + every media file
#   latest -> snapshots/<name>                newest VERIFIED snapshot
#   STATUS.json, LAST_SUCCESS, ALERT          monitoring surface
#
# `set -o pipefail` below is load-bearing: `mysqldump | zstd > f` reports
# zstd's exit status without it, so a dump that dies mid-table would exit 0
# and then rotate away the good snapshots. Do not remove it.
set -euo pipefail

DEST="${WOCO_BACKUP_DIR:-/var/backups/woco}"
APP_ROOT="${WOCO_APP_ROOT:-/srv/woco}"
DB_NAME="${WOCO_DB_NAME:-worldcovers}"
MYSQL_CNF="${WOCO_MYSQL_CNF:-${APP_ROOT}/mysql.cnf}"
MEDIA_ROOT="${WOCO_MEDIA_ROOT:-${APP_ROOT}/backend/media}"
ZSTD_LEVEL="${WOCO_ZSTD_LEVEL:-19}"
LOCK_WAIT="${WOCO_LOCK_WAIT:-1800}"

# `sudo -u wocod worldcovers-backup ...` inherits the CALLER's working
# directory, which wocod typically cannot read (e.g. /home/reese, mode 0750).
# GNU find then aborts with "Failed to restore initial working directory" after
# it has already done its work. Resolve $0 first, then move somewhere every user
# can read. The systemd unit sets WorkingDirectory=, so this only bites the
# interactive and pre-import-hook paths -- which is exactly where it matters.
SELF="$(readlink -f "$0")"
cd /

MODE=""
TAG=""
VERIFY_TARGET=""
DRY_RUN=0
DEEP=0

die() { echo "worldcovers-backup: $*" >&2; exit 1; }
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

usage() { sed -n '2,9p' "$SELF" >&2; }

# ---------------------------------------------------------------------------
# Retention selector.
#
# Deliberately a pure function over a list of snapshot names on stdin, writing
# the names to DELETE on stdout. Kept pure so tools/tests/test_backup_scripts.py
# can exercise it with no server and no filesystem. Reached via the internal
# --select-prunable mode.
#
# Rules (by rule, not by promotion -- stateless and idempotent, so a multi-day
# outage cannot produce a missing weekly or a double-promoted daily, and
# re-running never changes the answer):
#   1. keep everything from the last 7 days
#   2. keep the newest snapshot in each of the last 4 ISO weeks
#   3. keep tagged snapshots: newest 10, and never one younger than 30 days
#   4. never delete the target of `latest`
#   5. refuse to prune at all if fewer than 2 snapshots would remain
# ---------------------------------------------------------------------------
name_to_epoch() {
  # 2026-08-15T023007Z[-tag] -> epoch seconds, or empty if unparseable.
  # Seconds are optional so names written before the format gained them still
  # parse (and so remain prunable rather than silently immortal).
  local n="$1" d t s
  [[ "$n" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})?Z ]] || return 0
  d="${BASH_REMATCH[1]}"; t="${BASH_REMATCH[2]}:${BASH_REMATCH[3]}"; s="${BASH_REMATCH[4]:-00}"
  date -u -d "${d} ${t}:${s} UTC" +%s 2>/dev/null || true
}

name_to_tag() {
  # Always returns 0. Under `set -e`, a helper that returns non-zero for the
  # ordinary "no tag" case would abort the caller mid-loop.
  local n="$1"
  if [[ "$n" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{4,6}Z-(.+)$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
  return 0
}

select_prunable() {
  local now keep_latest names=() n ep tag
  now="${WOCO_BACKUP_NOW:-$(date -u +%s)}"
  keep_latest="${1:-}"

  while IFS= read -r n; do
    if [[ -n "$n" ]]; then names+=("$n"); fi
  done

  local -A keep=()
  local -A week_best=()
  local tagged=()
  local real_total=0

  for n in "${names[@]}"; do
    ep="$(name_to_epoch "$n")"
    # An unparseable name is never deleted. Better to leak a directory than to
    # delete something we do not understand. Such names are also excluded from
    # the rule-5 floor below -- two junk directories must not be able to satisfy
    # "keep at least 2" and license pruning every real snapshot.
    if [[ -z "$ep" ]]; then keep["$n"]=1; continue; fi
    real_total=$(( real_total + 1 ))
    tag="$(name_to_tag "$n")"
    local age=$(( now - ep ))

    if [[ -n "$tag" ]]; then
      tagged+=("$ep|$n")
      if (( age < 30 * 86400 )); then keep["$n"]=1; fi   # rule 3 (young tagged)
      continue
    fi

    if (( age <= 7 * 86400 )); then keep["$n"]=1; fi     # rule 1

    # rule 2: newest untagged per ISO week, for the last 4 weeks
    if (( age <= 28 * 86400 )); then
      local wk; wk="$(date -u -d "@$ep" +%G-%V)"
      local cur="${week_best[$wk]:-}"
      if [[ -z "$cur" || "$ep" -gt "${cur%%|*}" ]]; then week_best["$wk"]="$ep|$n"; fi
    fi
  done

  local v
  for v in "${week_best[@]}"; do keep["${v#*|}"]=1; done

  # rule 3: newest 10 tagged, regardless of age
  if (( ${#tagged[@]} > 0 )); then
    while IFS= read -r v; do keep["${v#*|}"]=1; done \
      < <(printf '%s\n' "${tagged[@]}" | sort -t'|' -k1,1nr | head -10)
  fi

  if [[ -n "$keep_latest" ]]; then keep["$keep_latest"]=1; fi   # rule 4

  local prunable=()
  for n in "${names[@]}"; do
    if [[ -z "${keep[$n]:-}" ]]; then prunable+=("$n"); fi
  done

  # rule 5 -- counted over real snapshots only (every prunable name is real,
  # since unparseable ones were forced into `keep` above).
  if (( real_total - ${#prunable[@]} < 2 )); then return 0; fi
  if (( ${#prunable[@]} > 0 )); then printf '%s\n' "${prunable[@]}"; fi
  return 0
}

# ---------------------------------------------------------------------------
# Status / monitoring surface
# ---------------------------------------------------------------------------
status_get() {
  local key="$1"
  [[ -f "$DEST/STATUS.json" ]] || { printf ''; return 0; }
  sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" \
    "$DEST/STATUS.json" | head -1
}

status_write() {
  local last_success="$1" last_failure="$2" last_snapshot="$3" fails="$4"
  cat > "$DEST/STATUS.json.tmp" <<EOF
{
  "schema": 1,
  "host": "$(hostname)",
  "last_success": "${last_success}",
  "last_failure": "${last_failure}",
  "last_snapshot": "${last_snapshot}",
  "consecutive_failures": ${fails}
}
EOF
  mv -f "$DEST/STATUS.json.tmp" "$DEST/STATUS.json"
  chmod 0640 "$DEST/STATUS.json"
}

record_failure() {
  local reason="$1" fails
  fails="$(status_get consecutive_failures)"; fails="${fails:-0}"
  status_write "$(status_get last_success)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
               "$(status_get last_snapshot)" "$(( fails + 1 ))"
  printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$reason" > "$DEST/ALERT"
  logger -p daemon.err -t worldcovers-backup "FAILED: $reason" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while (( $# )); do
  case "$1" in
    --scheduled)      MODE="scheduled" ;;
    --tag)            MODE="tag"; TAG="${2:-}"; shift ;;
    --verify-only)    MODE="verify"; VERIFY_TARGET="${2:-}"; shift ;;
    --dry-run)        DRY_RUN=1 ;;
    --deep)           DEEP=1 ;;
    --select-prunable) MODE="select-prunable" ;;   # internal, for tests
    -h|--help)        usage; exit 0 ;;
    *)                die "unknown argument: $1" ;;
  esac
  shift
done

[[ -n "$MODE" ]] || { usage; exit 2; }

if [[ "$MODE" == "select-prunable" ]]; then
  select_prunable "${WOCO_KEEP_LATEST:-}"
  exit 0
fi

if [[ "$MODE" == "tag" ]]; then
  [[ -n "$TAG" ]] || die "--tag requires a slug"
  # Strict slug: this script is reachable over a NOPASSWD sudo grant, and the
  # tag lands in a path. Nothing else about that grant is safe if this is not.
  [[ "$TAG" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] \
    || die "invalid tag '$TAG' (want ^[a-z0-9][a-z0-9-]{0,40}\$)"
fi

# Weekly deep pass: Sunday closes the same-size-same-mtime hole that rsync's
# quick-check cannot see, without hashing the whole media tree every night.
if [[ "$MODE" == "scheduled" && "$(date -u +%u)" == "7" ]]; then DEEP=1; fi

MYSQL=(mysql --defaults-file="$MYSQL_CNF" --protocol=socket)
if command -v mariadb-dump >/dev/null 2>&1; then DUMP_BIN="mariadb-dump"; else DUMP_BIN="mysqldump"; fi

# ---------------------------------------------------------------------------
# verify_snapshot <dir> -- structural checks against the artifact itself
# ---------------------------------------------------------------------------
verify_snapshot() {
  local dir="$1" expect_tables="${2:-}"
  local dumpfile="$dir/db/${DB_NAME}.sql.zst"

  [[ -f "$dumpfile" ]] || { echo "missing dump file"; return 1; }
  zstd -t "$dumpfile" >/dev/null 2>&1 || { echo "zstd integrity check failed"; return 1; }

  # mysqldump writes this only after the last table is flushed, so its presence
  # is the single strongest signal that the dump is complete rather than
  # truncated. A dump killed at table 10 of 40 will not have it.
  # Wrapped so an early-exiting `grep -q` cannot SIGPIPE the decompressor and
  # have `set -o pipefail` report a complete dump as truncated.
  local trailer
  trailer="$( { zstdcat "$dumpfile" 2>/dev/null | tail -c 400; } || true )"
  if ! grep -q -- '-- Dump completed on' <<<"$trailer"; then
    echo "dump trailer missing (truncated dump)"; return 1
  fi

  local got
  got="$(zstdcat "$dumpfile" | grep -c '^CREATE TABLE' || true)"
  if [[ -n "$expect_tables" && "$got" != "$expect_tables" ]]; then
    echo "table count mismatch: dump has $got, server has $expect_tables"; return 1
  fi

  if [[ -s "$dir/db/dump.stderr" ]]; then
    local unexpected
    unexpected="$(grep -v -E \
      -e 'Using a password on the command line' \
      -e 'Warning: Forcing --lock-tables=0' \
      -e '^$' "$dir/db/dump.stderr" || true)"
    if [[ -n "$unexpected" ]]; then
      echo "dumper wrote to stderr: $(head -2 <<<"$unexpected" | tr '\n' ' ')"; return 1
    fi
  fi

  return 0
}

if [[ "$MODE" == "verify" ]]; then
  [[ -n "$VERIFY_TARGET" ]] || die "--verify-only requires a snapshot name"
  d="$DEST/snapshots/$VERIFY_TARGET"
  [[ -d "$d" ]] || die "no such snapshot: $VERIFY_TARGET"
  if msg="$(verify_snapshot "$d")"; then
    ( cd "$d" && sha256sum -c --quiet SHA256SUMS ) || die "SHA256SUMS mismatch"
    log "OK $VERIFY_TARGET"
    exit 0
  else
    die "verification failed: $msg"
  fi
fi

# ---------------------------------------------------------------------------
# Pre-flight. Everything here aborts BEFORE anything is written.
# ---------------------------------------------------------------------------
[[ -r "$MYSQL_CNF" ]] || die "cannot read $MYSQL_CNF (run as the wocod user)"
[[ -d "$MEDIA_ROOT" ]] || die "media root not found: $MEDIA_ROOT"

mkdir -p "$DEST/snapshots" "$DEST/.incoming"

"${MYSQL[@]}" -e 'SELECT 1' "$DB_NAME" >/dev/null 2>&1 \
  || { record_failure "database unreachable"; die "cannot reach database $DB_NAME"; }

MEDIA_BYTES="$(du -sb "$MEDIA_ROOT" | cut -f1)"
AVAIL_BYTES=$(( $(df -Pk "$DEST" | awk 'NR==2 {print $4}') * 1024 ))
NEED_BYTES=$(( MEDIA_BYTES * 2 + 1073741824 ))
if (( AVAIL_BYTES < NEED_BYTES )); then
  # A backup that fills / takes the site down: it would cause the outage it
  # exists to prevent. Refuse rather than half-write.
  record_failure "insufficient disk: need $NEED_BYTES, have $AVAIL_BYTES"
  die "insufficient disk on $DEST: need $(( NEED_BYTES / 1048576 ))MB, have $(( AVAIL_BYTES / 1048576 ))MB"
fi

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
NAME="$STAMP"; if [[ -n "$TAG" ]]; then NAME="$STAMP-$TAG"; fi
INC="$DEST/.incoming/$NAME"
FINAL="$DEST/snapshots/$NAME"

if (( DRY_RUN )); then
  log "DRY RUN -- would create $FINAL"
  log "  dump:  $DUMP_BIN $DB_NAME -> db/${DB_NAME}.sql.zst (zstd -$ZSTD_LEVEL)"
  log "  media: $MEDIA_ROOT ($MEDIA_BYTES bytes) link-dest=$(readlink "$DEST/latest" 2>/dev/null || echo none) deep=$DEEP"
  if [[ "$MODE" == "scheduled" ]]; then
    log "  prune: $(ls -1 "$DEST/snapshots" 2>/dev/null | WOCO_KEEP_LATEST="$(basename "$(readlink -f "$DEST/latest" 2>/dev/null || echo '')")" "$SELF" --select-prunable | tr '\n' ' ')"
  else
    log "  prune: skipped (tagged runs never prune)"
  fi
  exit 0
fi

# A nightly run and a pre-import run must queue, not collide. -w, not -n.
exec 9>"$DEST/.lock"
flock -w "$LOCK_WAIT" 9 || die "another backup is running (waited ${LOCK_WAIT}s)"

trap 'rc=$?; if (( rc )); then rm -rf "$INC"; record_failure "exit $rc during $NAME"; fi' EXIT

rm -rf "$INC"; mkdir -p "$INC/db"

# --- 1. census ------------------------------------------------------------
# Taken from the live DB immediately before the dump, so it is accurate to
# within the few seconds before START TRANSACTION. On these boxes at 02:30 UTC
# that window is idle; on a write-heavy server it would not be, which is why
# worldcovers-restore treats a row-count difference as a REPORT and only a
# missing table as a hard failure.
log "[1/6] census"
mapfile -t TABLES < <("${MYSQL[@]}" -N -B -e \
  "SELECT table_name FROM information_schema.tables
    WHERE table_schema='$DB_NAME' AND table_type='BASE TABLE' ORDER BY table_name")
TABLE_COUNT="${#TABLES[@]}"
(( TABLE_COUNT > 0 )) || die "database $DB_NAME reports zero tables"

COUNT_SQL=""
for t in "${TABLES[@]}"; do
  COUNT_SQL+="SELECT '$t', COUNT(*) FROM \`$t\` UNION ALL "
done
COUNT_SQL="${COUNT_SQL% UNION ALL }"
mapfile -t ROWCOUNTS < <("${MYSQL[@]}" -N -B -e "$COUNT_SQL" "$DB_NAME")

# --- 2. dump --------------------------------------------------------------
log "[2/6] dump ($DUMP_BIN, $TABLE_COUNT tables)"
# Flag set frozen to the one proven by the 2026-08-07 backup and the 2026-08-10
# restore rehearsal, plus --no-tablespaces (PROCESS is required without it as of
# 8.0.21, and wocod deliberately lacks it).
# Deliberately absent: --databases (keeps the dump loadable into a scratch DB
# for rehearsal), --events (none exist; a privilege error would fail the run for
# nothing), --set-gtid-purged (MySQL-only, MariaDB's dumper rejects it -- the
# same engine-portability trap as ISSUE-2026-08-10-01, one layer down).
set +e
"$DUMP_BIN" --defaults-file="$MYSQL_CNF" \
  --single-transaction --routines --triggers --no-tablespaces \
  --default-character-set=utf8mb4 --hex-blob \
  "$DB_NAME" 2>"$INC/db/dump.stderr" \
  | zstd -q -"$ZSTD_LEVEL" -T0 -o "$INC/db/${DB_NAME}.sql.zst"
DUMP_RC=("${PIPESTATUS[@]}")
set -e
(( DUMP_RC[0] == 0 )) || die "$DUMP_BIN failed (rc=${DUMP_RC[0]}): $(head -3 "$INC/db/dump.stderr" | tr '\n' ' ')"
(( DUMP_RC[1] == 0 )) || die "zstd failed (rc=${DUMP_RC[1]})"

# --- 3. verify the dump before it is allowed anywhere near `latest` --------
log "[3/6] verify dump"
if ! msg="$(verify_snapshot "$INC" "$TABLE_COUNT")"; then die "dump verification failed: $msg"; fi

DUMP_BYTES_C="$(stat -c%s "$INC/db/${DB_NAME}.sql.zst")"
DUMP_BYTES_U="$(zstdcat "$INC/db/${DB_NAME}.sql.zst" | wc -c)"

WARNINGS=""
# `readlink -f` canonicalizes a path that does not exist and still exits 0, so
# checking the symlink and its target explicitly is what keeps PREV empty on a
# first run (rather than the literal string ".../latest").
PREV=""
if [[ -L "$DEST/latest" ]]; then
  cand="$(readlink -f "$DEST/latest" 2>/dev/null || true)"
  if [[ -n "$cand" && -d "$cand" ]]; then PREV="$cand"; fi
fi
if [[ -n "$PREV" && -f "$PREV/MANIFEST.json" ]]; then
  prev_u="$(sed -n 's/.*"bytes_uncompressed"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' "$PREV/MANIFEST.json" | head -1)"
  if [[ -n "$prev_u" ]] && (( prev_u > 0 )) && (( DUMP_BYTES_U * 2 < prev_u )); then
    # A legitimate pre-truncate tagged run would trip a hard rule here, so this
    # can only ever warn. But a silent 90% shrink is exactly what a botched
    # import looks like, and it belongs in the log and the manifest.
    WARNINGS="dump shrank to $(( DUMP_BYTES_U * 100 / prev_u ))% of previous"
    log "WARNING: $WARNINGS"
  fi
fi

# --- 4. media -------------------------------------------------------------
# DB first, media second, and the order is a correctness property, not a
# convenience. The dump snapshots at T; this rsync finishes at T+n. A file
# created in that window lands on disk with no DB row -- a harmless orphan.
# The reverse order yields DB rows pointing at files that were never copied --
# a broken image link, which is the exact failure the 2026-08-07 README warns
# about. Do not "optimize" this by reordering.
log "[4/6] media (deep=$DEEP)"
RSYNC_ARGS=(-a --numeric-ids --exclude='.DS_Store' --exclude='_DELETE.ME')
# --link-dest points at the previous SNAPSHOT, never the live tree, so
# corrupting a live file cannot reach back into an existing backup.
if [[ -n "$PREV" && -d "$PREV/media" ]]; then RSYNC_ARGS+=(--link-dest="$PREV/media"); fi
if (( DEEP )); then RSYNC_ARGS+=(--checksum); fi
rsync "${RSYNC_ARGS[@]}" "$MEDIA_ROOT/" "$INC/media/"

MEDIA_FILES="$(find "$INC/media" -type f | wc -l)"
MEDIA_TOTAL="$(find "$INC/media" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}')"

# --- 5. manifest + hashes -------------------------------------------------
log "[5/6] manifest"
ENGINE_VERSION="$("${MYSQL[@]}" -N -B -e 'SELECT VERSION()')"
case "$ENGINE_VERSION" in
  *MariaDB*|*mariadb*) ENGINE_FAMILY="mariadb" ;;
  *)                   ENGINE_FAMILY="mysql" ;;
esac
GIT_SHA="$(git -C "$APP_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
GIT_REF="$(git -C "$APP_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
DUMP_SHA="$(sha256sum "$INC/db/${DB_NAME}.sql.zst" | cut -d' ' -f1)"

{
  printf '{\n  "schema": 1,\n'
  printf '  "host": "%s",\n' "$(hostname)"
  printf '  "taken_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "tag": %s,\n' "$([[ -n "$TAG" ]] && printf '"%s"' "$TAG" || printf 'null')"
  printf '  "engine": {"family": "%s", "version": "%s"},\n' "$ENGINE_FAMILY" "$ENGINE_VERSION"
  printf '  "git": {"ref": "%s", "sha": "%s"},\n' "$GIT_REF" "$GIT_SHA"
  printf '  "link_dest": %s,\n' "$([[ -n "$PREV" ]] && printf '"%s"' "$(basename "$PREV")" || printf 'null')"
  printf '  "deep": %s,\n' "$( (( DEEP )) && echo true || echo false )"
  printf '  "warnings": [%s],\n' "$([[ -n "$WARNINGS" ]] && printf '"%s"' "$WARNINGS")"
  printf '  "db": {\n'
  printf '    "file": "db/%s.sql.zst",\n' "$DB_NAME"
  printf '    "sha256": "%s",\n' "$DUMP_SHA"
  printf '    "bytes_compressed": %s,\n' "$DUMP_BYTES_C"
  printf '    "bytes_uncompressed": %s,\n' "$DUMP_BYTES_U"
  printf '    "table_count": %s,\n' "$TABLE_COUNT"
  printf '    "row_counts": {\n'
  first=1
  for line in "${ROWCOUNTS[@]}"; do
    tname="${line%%$'\t'*}"; tcount="${line##*$'\t'}"
    (( first )) || printf ',\n'; first=0
    printf '      "%s": %s' "$tname" "$tcount"
  done
  printf '\n    }\n  },\n'
  printf '  "media": {"file_count": %s, "byte_total": %s, "root": "%s"}\n' \
    "$MEDIA_FILES" "$MEDIA_TOTAL" "$MEDIA_ROOT"
  printf '}\n'
} > "$INC/MANIFEST.json"

( cd "$INC" && { sha256sum "db/${DB_NAME}.sql.zst"; find media -type f -print0 | sort -z | xargs -0 -r sha256sum; } > SHA256SUMS )

chmod 0640 "$INC/MANIFEST.json" "$INC/SHA256SUMS" "$INC/db/${DB_NAME}.sql.zst"
chmod 0750 "$INC" "$INC/db"

# --- 6. atomic publish, then prune ----------------------------------------
log "[6/6] publish"
# `mv dir existing_dir` would nest rather than replace, quietly producing
# snapshots/<name>/<name>. Refuse instead.
if [[ -e "$FINAL" ]]; then die "snapshot $NAME already exists -- refusing to overwrite"; fi
mv "$INC" "$FINAL"
ln -sfn "snapshots/$NAME" "$DEST/latest.tmp" && mv -Tf "$DEST/latest.tmp" "$DEST/latest"

status_write "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(status_get last_failure)" "$NAME" 0
touch "$DEST/LAST_SUCCESS"
rm -f "$DEST/ALERT"

# Dead-man's switch: the monitor alerts on the ABSENCE of this ping, which is
# what makes it the only signal that survives the box being gone. Send the run
# summary as the body so the dashboard shows what succeeded, not just that
# something did. Never fatal -- a monitoring outage must not fail a good backup.
if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
  curl -fsS -m 10 --retry 3 --data-binary \
    "$NAME db=$(( DUMP_BYTES_C / 1024 ))KB media=${MEDIA_FILES}f/$(( MEDIA_TOTAL / 1048576 ))MB tables=$TABLE_COUNT${WARNINGS:+ warn=$WARNINGS}" \
    "$HEALTHCHECK_URL" >/dev/null 2>&1 || true
fi

if [[ "$MODE" == "scheduled" ]]; then
  # Prune ONLY after a verified publish, so a night that produced a bad dump
  # can never rotate away the good ones. Tagged runs never reach here at all,
  # which is what makes the NOPASSWD sudo grant non-destructive by construction.
  mapfile -t DOOMED < <(ls -1 "$DEST/snapshots" | WOCO_KEEP_LATEST="$NAME" "$SELF" --select-prunable)
  for d in "${DOOMED[@]}"; do
    [[ -n "$d" && -d "$DEST/snapshots/$d" ]] || continue
    log "prune $d"
    # Safe on hardlinked trees: rm frees only blocks whose link count hits zero,
    # so removing an older snapshot a newer one links against costs nothing.
    rm -rf "${DEST:?}/snapshots/${d:?}"
  done
else
  log "prune skipped (tagged run)"
fi

trap - EXIT
log "OK $NAME  db=$(( DUMP_BYTES_C / 1024 ))KB media=${MEDIA_FILES}f/$(( MEDIA_TOTAL / 1048576 ))MB tables=$TABLE_COUNT"
