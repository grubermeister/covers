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
# With --import, the server-side reload_data.sh now takes a TAGGED BACKUP first
# (pre-import-<bundle>) and refuses to run if no backup tool is installed. See
# deploy/install-backup.sh and docs/devel/BACKUP.md. Override, at your own risk,
# with WOCO_ALLOW_UNBACKED_IMPORT=1.
#
# Prereq on the server: your SSH user needs passwordless sudo for exactly the
# two commands below. Use a scoped drop-in (replace <user> with your SSH user)
# rather than blanket NOPASSWD -- blanket sudo makes the SSH key root-equivalent:
#   # /etc/sudoers.d/<user>-rsync
#   <user> ALL=(root) NOPASSWD: /usr/bin/rsync
#   <user> ALL=(wocod) NOPASSWD: /srv/woco/tools/reload_data.sh
#
# Usage:
#   ./tools/push_data.sh              # push only
#   ./tools/push_data.sh --import --state VA
#                                     # push, then import tools/wip/out/v1_va as wocod
#   ./tools/push_data.sh --import --bundle-dir tools/wip/out/v1_va
#                                     # push, then import an explicit bundle as wocod
#   ./tools/push_data.sh --dry-run    # show what rsync would do
#                                     # (refuses --import: the import is never a dry run)

set -euo pipefail
cd "$(dirname "$0")/.."

DO_IMPORT=0
DRY_RUN=0
IMPORT_STATE=""
IMPORT_BUNDLE=""
RSYNC_EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --import)  DO_IMPORT=1 ;;
    --dry-run) DRY_RUN=1; RSYNC_EXTRA+=("--dry-run") ;;
    --state)
      if [[ -z "${2:-}" || "${2:-}" == --* ]]; then
        echo "push_data.sh: --state requires a value." >&2
        exit 2
      fi
      IMPORT_STATE="$2"
      shift
      ;;
    --bundle-dir)
      if [[ -z "${2:-}" || "${2:-}" == --* ]]; then
        echo "push_data.sh: --bundle-dir requires a value." >&2
        exit 2
      fi
      IMPORT_BUNDLE="$2"
      shift
      ;;
    -h|--help)
      sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

# --dry-run only previews the rsyncs; there is no dry-run for the import,
# which truncate-reloads the live DB. Refuse the combination outright.
if [[ $DRY_RUN -eq 1 && $DO_IMPORT -eq 1 ]]; then
  echo "push_data.sh: refusing --import with --dry-run; the import step is never a dry run." >&2
  exit 2
fi

if [[ -n "$IMPORT_STATE" && -n "$IMPORT_BUNDLE" ]]; then
  echo "push_data.sh: use either --state or --bundle-dir, not both." >&2
  exit 2
fi

if [[ -z "$IMPORT_STATE" && -z "$IMPORT_BUNDLE" && -n "${WOCO_IMPORT_BUNDLE:-}" ]]; then
  IMPORT_BUNDLE="$WOCO_IMPORT_BUNDLE"
fi

if [[ -n "$IMPORT_STATE" ]]; then
  IMPORT_STATE="$(printf '%s' "$IMPORT_STATE" | tr '[:lower:]' '[:upper:]')"
  if [[ ! "$IMPORT_STATE" =~ ^[A-Z][A-Z]$ ]]; then
    echo "push_data.sh: --state must be a two-letter abbreviation." >&2
    exit 2
  fi
  IMPORT_BUNDLE="tools/wip/out/v1_$(printf '%s' "$IMPORT_STATE" | tr '[:upper:]' '[:lower:]')"
fi

if [[ $DO_IMPORT -eq 1 ]]; then
  if [[ -z "$IMPORT_BUNDLE" ]]; then
    echo "push_data.sh: --import requires --state, --bundle-dir, or WOCO_IMPORT_BUNDLE." >&2
    exit 2
  fi
  NORMALIZED_IMPORT_BUNDLE="${IMPORT_BUNDLE%/}"
  if [[ "$NORMALIZED_IMPORT_BUNDLE" == "tools/wip/out" ]]; then
    echo "push_data.sh: refusing to import bare tools/wip/out." >&2
    echo "Pass --state STATE or --bundle-dir tools/wip/out/v1_state." >&2
    exit 2
  fi
  if [[ ! -d "$NORMALIZED_IMPORT_BUNDLE" ]]; then
    echo "push_data.sh: bundle directory not found: $NORMALIZED_IMPORT_BUNDLE" >&2
    exit 2
  fi
  IMPORT_BUNDLE="$NORMALIZED_IMPORT_BUNDLE"
fi

# Resolve the SSH target. Precedence: WOCO_HOST env > WOCO_DEPLOY_USER env >
# WOCO_DEPLOY_USER from .env. The .env is extracted with sed, not sourced --
# it is a Django-style env file whose unquoted values would break bash.
if [[ -z "${WOCO_DEPLOY_USER:-}" && -f .env ]]; then
  # Strip any trailing inline comment and whitespace so "alice  # ssh user"
  # yields "alice" instead of a silently broken SSH target.
  WOCO_DEPLOY_USER="$(sed -n 's/^WOCO_DEPLOY_USER=\([^#]*\).*/\1/p' .env | tail -1 | tr -d '[:space:]')"
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
  echo "==> running reload_data.sh as wocod on ${HOST} for ${IMPORT_BUNDLE}"
  ssh -t "$HOST" "sudo -u wocod ${REMOTE_ROOT}/tools/reload_data.sh ${IMPORT_BUNDLE}"
fi

echo "Done."
