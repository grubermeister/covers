#!/usr/bin/env bash
# Shared pre-flight: take a tagged backup before a destructive catalog change.
# Sourced by tools/reload_data.sh and tools/wipe_user_data.sh -- not run directly.
#
# Both callers run `./woco ascc import --truncate`, which DELETEs all 14 catalog
# import tables (covers included) before reloading. Until this existed, that was
# an irreversible operation with no snapshot taken at either entry point --
# push_data.sh driving it remotely, or a human running the script on the box.
#
# Refusing by default (rather than warning) is deliberate and matches how the
# codebase already treats this operation: push_data.sh refuses --dry-run with
# --import precisely because there is no such thing as a trial truncate.

# pre_change_backup <tag-suffix>
#   Takes a snapshot tagged pre-<tag-suffix>. Returns non-zero (caller should
#   abort) if no backup tool is installed and the override is not set.
pre_change_backup() {
  local suffix="$1"
  local bin="${WOCO_BACKUP_BIN:-/usr/local/sbin/worldcovers-backup}"

  # Slug the suffix to match the backup tool's ^[a-z0-9][a-z0-9-]{0,40}$ rule.
  local tag
  tag="pre-$(printf '%s' "$suffix" | tr '[:upper:]_/.' '[:lower:]---' | tr -cd 'a-z0-9-')"
  tag="${tag:0:41}"

  if [[ -x "$bin" ]]; then
    echo "[pre-flight] tagged backup: $tag"
    if "$bin" --tag "$tag"; then
      return 0
    fi
    echo "pre-flight backup FAILED -- refusing to continue with a destructive import." >&2
    return 1
  fi

  if [[ "${WOCO_ALLOW_UNBACKED_IMPORT:-0}" == "1" ]]; then
    echo "[pre-flight] WARNING: no backup tool at $bin, continuing because" >&2
    echo "             WOCO_ALLOW_UNBACKED_IMPORT=1. Nothing here is reversible." >&2
    return 0
  fi

  cat >&2 <<MSG
Refusing to truncate-import with no backup.

  No backup tool found at: $bin

This operation DELETEs all 14 catalog tables before reloading, and there is no
dry run for it. Install the backup system first:

  ssh -t reese@<host>
  sudo /srv/woco/deploy/install-backup.sh

To override anyway (you will not be able to undo this), set:

  WOCO_ALLOW_UNBACKED_IMPORT=1
MSG
  return 1
}
