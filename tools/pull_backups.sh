#!/usr/bin/env bash
# Pull server backup snapshots to this workstation. Run from the repo root.
#
#   ./tools/pull_backups.sh                    # every configured host
#   ./tools/pull_backups.sh --host woco-dev    # one host
#   ./tools/pull_backups.sh --dry-run
#
# An on-box backup dies with the box. This is the second copy: it runs FROM the
# workstation (which can reach both boxes over passwordless SSH) rather than
# being pushed from the servers, so a compromised server cannot reach back into
# the local store.
#
# Exit status is the alarm: non-zero if any host's most recent backup is stale.
# Snapshots are still pulled in that case -- stale is better than nothing.
#
# SFTP is disabled on both boxes, so `scp` fails with
# "subsystem request failed on channel 0". Everything here uses rsync-over-ssh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_BASE="${WOCO_LOCAL_BACKUPS:-$(cd "$REPO_ROOT/.." && pwd)/backups}"
REMOTE_DIR="${WOCO_BACKUP_DIR:-/var/backups/woco}"
STALE_SECONDS="${WOCO_STALE_SECONDS:-129600}"   # 36h
KEEP_WEEKLIES="${WOCO_LOCAL_KEEP:-12}"

# name|ssh-target
HOSTS=(
  "woco-dev|${WOCO_DEV_SSH:-reese@172.238.189.147}"
  "prod|${WOCO_PROD_SSH:-reese@hellowoco.app}"
)

ONLY=""; DRY=0
while (( $# )); do
  case "$1" in
    --host)    ONLY="${2:-}"; shift ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,9p' "$0" >&2; exit 0 ;;
    *)         echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

log()  { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
bad()  { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

RC=0

for entry in "${HOSTS[@]}"; do
  NAME="${entry%%|*}"; TARGET="${entry#*|}"
  if [[ -n "$ONLY" && "$ONLY" != "$NAME" ]]; then continue; fi

  echo
  log "=== $NAME ($TARGET) ==="
  SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$TARGET")

  if ! "${SSH[@]}" true 2>/dev/null; then
    bad "  UNREACHABLE -- cannot ssh to $TARGET"; RC=1; continue
  fi

  STATUS="$("${SSH[@]}" "cat $REMOTE_DIR/STATUS.json 2>/dev/null" || true)"
  if [[ -z "$STATUS" ]]; then
    bad "  no STATUS.json -- is the backup system installed? (deploy/install-backup.sh)"; RC=1; continue
  fi

  LAST_SUCCESS="$(sed -n 's/.*"last_success"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' <<<"$STATUS")"
  FAILS="$(sed -n 's/.*"consecutive_failures"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' <<<"$STATUS")"
  if [[ -n "$LAST_SUCCESS" ]]; then
    AGE=$(( $(date -u +%s) - $(date -u -d "$LAST_SUCCESS" +%s) ))
    log "  last success $LAST_SUCCESS ($(( AGE / 3600 ))h ago), consecutive_failures=${FAILS:-0}"
    # Pull anyway: a stale backup is still worth having locally. The exit code
    # is what raises the alarm.
    if (( AGE > STALE_SECONDS )); then
      bad "  STALE: no successful backup in $(( AGE / 3600 ))h (threshold $(( STALE_SECONDS / 3600 ))h)"; RC=1
    fi
  else
    bad "  never succeeded"; RC=1
  fi

  LOCAL="$LOCAL_BASE/$NAME/snapshots"
  mkdir -p "$LOCAL"

  mapfile -t REMOTE_SNAPS < <("${SSH[@]}" "ls -1 $REMOTE_DIR/snapshots 2>/dev/null" || true)
  if (( ${#REMOTE_SNAPS[@]} == 0 )); then warn "  no snapshots on $NAME"; continue; fi

  # Newest untagged, plus every tagged snapshot: the tagged ones mark
  # pre-truncate/pre-wipe moments and are the whole reason for on-demand runs.
  WANT=()
  NEWEST="$(printf '%s\n' "${REMOTE_SNAPS[@]}" | grep -E '^[0-9-]+T[0-9]+Z$' | sort | tail -1 || true)"
  if [[ -n "$NEWEST" ]]; then WANT+=("$NEWEST"); fi
  while IFS= read -r s; do [[ -n "$s" ]] && WANT+=("$s"); done \
    < <(printf '%s\n' "${REMOTE_SNAPS[@]}" | grep -E '^[0-9-]+T[0-9]+Z-' || true)

  for snap in "${WANT[@]}"; do
    if [[ -d "$LOCAL/$snap" ]]; then log "  have  $snap"; continue; fi
    if (( DRY )); then log "  WOULD PULL $snap"; continue; fi

    PREV="$(ls -1 "$LOCAL" 2>/dev/null | sort | tail -1 || true)"
    LINK=()
    # --link-dest against the previous LOCAL snapshot, so the local store is
    # hardlinked too and its retention is independent of the server's.
    if [[ -n "$PREV" && -d "$LOCAL/$PREV" ]]; then LINK=(--link-dest="$LOCAL/$PREV"); fi

    log "  pull  $snap"
    rm -rf "$LOCAL/$snap.partial"
    rsync -a --info=stats2 "${LINK[@]}" \
      "$TARGET:$REMOTE_DIR/snapshots/$snap/" "$LOCAL/$snap.partial/" >/dev/null
    mv "$LOCAL/$snap.partial" "$LOCAL/$snap"

    # Verify the COPY, not just the transfer. This is the step that lets the
    # local store make the same claim the 2026-08-07 manual backup made.
    if ( cd "$LOCAL/$snap" && sha256sum -c --quiet SHA256SUMS ); then
      log "  ok    $snap (sha256 verified)"
      printf '%s\t%s\t%s\tOK\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NAME" "$snap" >> "$LOCAL_BASE/pull.log"
    else
      bad "  CHECKSUM MISMATCH on $snap -- local copy is damaged"
      mv "$LOCAL/$snap" "$LOCAL/$snap.CORRUPT"
      printf '%s\t%s\t%s\tCORRUPT\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NAME" "$snap" >> "$LOCAL_BASE/pull.log"
      RC=1
    fi
  done

  # Local retention: keep the newest N untagged, and every tagged snapshot
  # forever. There is no reason to discard a pre-change snapshot here.
  mapfile -t LOCAL_UNTAGGED < <(ls -1 "$LOCAL" 2>/dev/null | grep -E '^[0-9-]+T[0-9]+Z$' | sort -r || true)
  if (( ${#LOCAL_UNTAGGED[@]} > KEEP_WEEKLIES )); then
    for old in "${LOCAL_UNTAGGED[@]:$KEEP_WEEKLIES}"; do
      if (( DRY )); then log "  would prune local $old"; else log "  prune local $old"; rm -rf "${LOCAL:?}/${old:?}"; fi
    done
  fi
done

echo
if (( RC )); then
  bad "One or more hosts reported a problem. See above."
else
  log "All hosts current and verified."
fi
exit $RC
