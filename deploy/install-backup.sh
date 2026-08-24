#!/usr/bin/env bash
# Install the WorldCovers automated backup system on this host.
#
# This is the ONE step that needs root. Everything afterwards -- scheduled runs,
# on-demand tagged backups, pulls, rehearsals -- runs unprivileged as the wocod
# service account, which already owns mysql.cnf and the media tree.
#
#   ssh -t reese@<host>
#   sudo /srv/woco/deploy/install-backup.sh
#
# Idempotent: re-running is safe and is how you roll out a script change.
#
# Tunables (env vars):
#   WOCO_APP_USER    service account (default: wocod)
#   WOCO_ROOT        repo checkout path (default: /srv/woco)
#   WOCO_BACKUP_DIR  where snapshots live (default: /var/backups/woco)
#   WOCO_OPERATORS   space-separated humans granted on-demand backups
#                    (default: every member of the sudo group)
set -euo pipefail

APP_USER="${WOCO_APP_USER:-wocod}"
ROOT="${WOCO_ROOT:-/srv/woco}"
DEST="${WOCO_BACKUP_DIR:-/var/backups/woco}"

log() { printf '\n=== %s ===\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root (sudo $0)." >&2
  exit 1
fi
id "$APP_USER" >/dev/null 2>&1 || { echo "No such user: $APP_USER" >&2; exit 1; }
[[ -d "$ROOT/deploy" ]] || { echo "Not a checkout: $ROOT/deploy missing" >&2; exit 1; }

OPERATORS="${WOCO_OPERATORS:-$(getent group sudo | cut -d: -f4 | tr ',' ' ')}"

log "Installing scripts to /usr/local/sbin"
install -o root -g root -m 0755 "$ROOT/deploy/worldcovers-backup.sh"       /usr/local/sbin/worldcovers-backup
install -o root -g root -m 0755 "$ROOT/deploy/worldcovers-restore.sh"      /usr/local/sbin/worldcovers-restore
install -o root -g root -m 0755 "$ROOT/deploy/worldcovers-backup-alert.sh" /usr/local/sbin/worldcovers-backup-alert

log "Installing systemd units"
install -m 644 "$ROOT/deploy/worldcovers-backup.service"          /etc/systemd/system/worldcovers-backup.service
install -m 644 "$ROOT/deploy/worldcovers-backup.timer"            /etc/systemd/system/worldcovers-backup.timer
install -m 644 "$ROOT/deploy/worldcovers-backup-alert@.service"   /etc/systemd/system/worldcovers-backup-alert@.service

log "Installing MOTD staleness banner"
install -o root -g root -m 0755 "$ROOT/deploy/99-worldcovers-backup" /etc/update-motd.d/99-worldcovers-backup

log "Creating $DEST"
# NOT inside $ROOT: deploy.sh does `git reset --hard` there, and a future
# `git clean -xdf` or a recovery re-clone would take the backups with it.
# /var/backups is the FHS location and is on the same filesystem as the media
# tree, which is what makes rsync --link-dest hardlinks possible.
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$DEST" "$DEST/snapshots" "$DEST/.incoming"

log "Granting $APP_USER rights on rehearsal databases"
# provision.sh grants only worldcovers.* and test_worldcovers.*, so a restore
# rehearsal into e.g. worldcovers_rehearsal would fail with ERROR 1044. The
# escaped \_ is a literal underscore (a bare _ is a single-character wildcard),
# so this widens to the worldcovers_* family and nothing else.
mysql <<SQL
GRANT ALL PRIVILEGES ON \`worldcovers\_%\`.* TO '${APP_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

log "Installing sudoers drop-in (on-demand backups for operators)"
# Lets an operator (or an agent acting as one) take a pre-change snapshot with
# no password. Safe ONLY because: the script is root-owned 0755, tagged runs
# never prune, and --tag is validated against ^[a-z0-9][a-z0-9-]{0,40}$.
# If any of those three properties is ever relaxed, withdraw this grant.
{
  for op in $OPERATORS; do
    id "$op" >/dev/null 2>&1 || continue
    echo "${op} ALL=(${APP_USER}) NOPASSWD: /usr/local/sbin/worldcovers-backup"
  done
} > /etc/sudoers.d/worldcovers-backup
chmod 440 /etc/sudoers.d/worldcovers-backup
# A malformed sudoers file locks everyone out of sudo. provision.sh:165 already
# learned this; do not remove the check.
visudo -cf /etc/sudoers.d/worldcovers-backup

log "Ensuring operators can read backups for the weekly pull"
for op in $OPERATORS; do
  id "$op" >/dev/null 2>&1 || continue
  if ! id -nG "$op" | tr ' ' '\n' | grep -qx "$APP_USER"; then
    usermod -aG "$APP_USER" "$op"
    echo "  added $op to the $APP_USER group (they must re-login for it to take effect)"
  fi
done

log "Enabling the timer"
systemctl daemon-reload
systemctl enable --now worldcovers-backup.timer
systemctl list-timers --no-pager worldcovers-backup.timer || true

log "Taking the first backup now (proving it works before you walk away)"
# A backup system that has never run is not a backup system. If this fails, it
# fails HERE, in front of a human, rather than silently at 02:30 tomorrow.
if systemctl start worldcovers-backup.service; then
  systemctl status --no-pager --lines=0 worldcovers-backup.service || true
  LATEST="$(readlink "$DEST/latest" 2>/dev/null || echo '')"
  if [[ -n "$LATEST" ]]; then
    echo
    echo "First snapshot: $LATEST"
    sed -n '1,14p' "$DEST/$LATEST/MANIFEST.json" 2>/dev/null || true
  fi
else
  echo "!! First backup FAILED. Diagnose before leaving this host:" >&2
  echo "   journalctl -u worldcovers-backup -n 50 --no-pager" >&2
  exit 1
fi

cat <<NEXT

=== Installed ===
  timer      worldcovers-backup.timer   (nightly 02:30 UTC, Persistent=true)
  snapshots  $DEST/snapshots
  status     $DEST/STATUS.json
  restore    /usr/local/sbin/worldcovers-restore --snapshot <name> --into <db>
  docs       $ROOT/docs/devel/BACKUP.md

Optional dead-man's switch (recommended -- neither box has an MTA, so this is
the only signal that reaches you when the BOX is gone, not just the backup):
  echo 'HEALTHCHECK_URL=https://hc-ping.com/<uuid>' > /etc/worldcovers-backup.env
  chmod 640 /etc/worldcovers-backup.env && chown root:${APP_USER} /etc/worldcovers-backup.env
NEXT
