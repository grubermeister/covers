#!/usr/bin/env bash
# worldcovers-backup-alert — OnFailure= handler for worldcovers-backup.service.
# Installed to /usr/local/sbin/worldcovers-backup-alert.
#
#   worldcovers-backup-alert <failed-unit-name>
#
# Neither box has an MTA (msmtp/mail/sendmail are all absent), so this reports
# through the channels that DO exist: the journal, an on-disk ALERT file the
# MOTD banner reads, and -- if configured -- an outbound dead-man's-switch ping.
set -uo pipefail

DEST="${WOCO_BACKUP_DIR:-/var/backups/woco}"
UNIT="${1:-worldcovers-backup.service}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

TAIL="$(journalctl -u "$UNIT" -n 20 --no-pager 2>/dev/null || echo '(journal unavailable)')"

logger -p daemon.err -t worldcovers-backup "BACKUP FAILED ($UNIT) at $NOW" 2>/dev/null || true

mkdir -p "$DEST" 2>/dev/null || true
{
  echo "$NOW  $UNIT FAILED"
  echo
  echo "$TAIL"
} > "$DEST/ALERT" 2>/dev/null || true

# The only genuinely out-of-band signal. Every other layer -- journal, ALERT
# file, MOTD banner, the weekly pull -- requires someone to show up and look.
# A dead-man's switch is what tells you the BOX is gone, not just the backup.
if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
  curl -fsS -m 10 --retry 3 --data-binary "$TAIL" "${HEALTHCHECK_URL}/fail" >/dev/null 2>&1 || true
fi

exit 0
