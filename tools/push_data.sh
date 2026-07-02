#!/usr/bin/env bash
# Push local tools/wip/ and backend/media/ to hellowoco.app staging.
#
# Files land on the server owned wocod:wocod (via `sudo rsync` + --chown) so
# the app runtime can read them without a manual chown dance. With --import,
# also triggers /srv/woco/tools/reload_data.sh as the wocod user.
#
# No username is baked into this script. Set WOCO_DEPLOY_USER in the repo-root
# .env (gitignored; see .env.example), or export WOCO_HOST=user@host to
# override the full target. Files on the server are always owned by wocod
# regardless of who pushes.
#
# Prereq on the server: your SSH user needs passwordless sudo for exactly the
# two commands below. Use a scoped drop-in (replace <user> with your SSH user)
# rather than blanket NOPASSWD — blanket sudo makes the SSH key root-equivalent:
#   # /etc/sudoers.d/<user>-rsync
#   <user> ALL=(root) NOPASSWD: /usr/bin/rsync
#   <user> ALL=(wocod) NOPASSWD: /srv/woco/tools/reload_data.sh
#
# Usage:
#   ./tools/push_data.sh              # push only
#   ./tools/push_data.sh --import     # push, then run imports as wocod
#   ./tools/push_data.sh --dry-run    # show what rsync would do
#                                     # (refuses --import: the import is never a dry run)

set -euo pipefail
cd "$(dirname "$0")/.."

DO_IMPORT=0
DRY_RUN=0
RSYNC_EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --import)  DO_IMPORT=1 ;;
    --dry-run) DRY_RUN=1; RSYNC_EXTRA+=("--dry-run") ;;
    -h|--help)
      sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# --dry-run only previews the rsyncs; there is no dry-run for the import,
# which truncate-reloads the live DB. Refuse the combination outright.
if [[ $DRY_RUN -eq 1 && $DO_IMPORT -eq 1 ]]; then
  echo "push_data.sh: refusing --import with --dry-run — the import step is never a dry run." >&2
  exit 2
fi

# Resolve the SSH target. Precedence: WOCO_HOST env > WOCO_DEPLOY_USER env >
# WOCO_DEPLOY_USER from .env. The .env is extracted with sed, not sourced —
# it is a Django-style env file whose unquoted values would break bash.
if [[ -z "${WOCO_DEPLOY_USER:-}" && -f .env ]]; then
  WOCO_DEPLOY_USER="$(sed -n 's/^WOCO_DEPLOY_USER=//p' .env | tail -1)"
fi
if [[ -z "${WOCO_HOST:-}" ]]; then
  if [[ -z "${WOCO_DEPLOY_USER:-}" ]]; then
    echo "push_data.sh: no deploy user configured." >&2
    echo "Set WOCO_DEPLOY_USER=<your-ssh-user> in .env (see .env.example)," >&2
    echo "or export WOCO_HOST=user@host to override the full target." >&2
    exit 2
  fi
  WOCO_HOST="${WOCO_DEPLOY_USER}@hellowoco.app"
fi
HOST="$WOCO_HOST"
REMOTE_ROOT="${WOCO_REMOTE_ROOT:-/srv/woco}"

# -a           preserve timestamps/symlinks
# --delete     mirror source (removes server-side files missing locally)
# --chown=...  force ownership on server (requires remote rsync running as root)
# --chmod=...  dirs: u=rwx, g/o=rx ; files: u=rw, g/o=r
# --rsync-path="sudo -n rsync"  run remote rsync as root via passwordless sudo
#
# Two flag sets: tools/wip is MIRRORED (--delete) so the import bundle on
# the server matches local byte-for-byte; backend/media is ADDITIVE (no
# --delete) so server-side files (manual uploads, prior catalog generations)
# survive a re-push.
RSYNC_FLAGS_COMMON=(
  -a --info=progress2
  --chown=wocod:wocod
  --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r
  --rsync-path="sudo -n rsync"
  --exclude='.DS_Store'
)

# wip: mirror + skip munger intermediate work dirs that the server never
# reads. *_images/ is the per-marking PNG dump from ascc_image_extract.py
# (Step 11 of the munger has already copied those into backend/media/<state>/
# by the time we push). *_subchunks/ and *_subchunks_report.csv are
# diagnostic outputs from the same tool. None of them are in
# import_ascc_bundle's ASCC_LOAD_ORDER.
RSYNC_FLAGS_WIP=(
  "${RSYNC_FLAGS_COMMON[@]}"
  --delete
  --exclude='*_images/'
  --exclude='*_subchunks/'
  --exclude='*_subchunks_report.csv'
  "${RSYNC_EXTRA[@]}"
)

# media: additive only.
RSYNC_FLAGS_MEDIA=(
  "${RSYNC_FLAGS_COMMON[@]}"
  "${RSYNC_EXTRA[@]}"
)

push_tree() {
  local src="$1" dst="$2"
  shift 2
  local flags=("$@")
  echo "==> rsync ${src} -> ${HOST}:${dst}"
  rsync "${flags[@]}" "${src}/" "${HOST}:${dst}/"
}

push_tree "tools/wip"     "${REMOTE_ROOT}/tools/wip"     "${RSYNC_FLAGS_WIP[@]}"
push_tree "backend/media" "${REMOTE_ROOT}/backend/media" "${RSYNC_FLAGS_MEDIA[@]}"

if [[ $DO_IMPORT -eq 1 ]]; then
  echo "==> running reload_data.sh as wocod on ${HOST}"
  ssh -t "$HOST" "sudo -u wocod ${REMOTE_ROOT}/tools/reload_data.sh"
fi

echo "Done."
