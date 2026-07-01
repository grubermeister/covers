#!/usr/bin/env bash
# Install the staging systemd unit from the checked-out repo.
#
# Install this file as root:
#   install -o root -g root -m 0755 /srv/woco/tools/worldcovers-apply-unit.sh /usr/local/sbin/worldcovers-apply-unit
#
# Sudoers entry for staging:
#   wocod ALL=(root) NOPASSWD: /usr/local/sbin/worldcovers-apply-unit
#
# This helper is for the disposable staging host only. Production unit changes
# are applied manually by a root operator after review.

set -euo pipefail

SOURCE="/srv/woco/tools/worldcovers.service"
TARGET="/etc/systemd/system/worldcovers.service"
EXPECTED_OWNER="wocod:wocod"

die() {
  echo "worldcovers-apply-unit: $*" >&2
  exit 1
}

require_line() {
  local expected="$1"
  grep -Fx -- "$expected" "$SOURCE" >/dev/null || die "missing required line: $expected"
}

reject_key() {
  local key="$1"
  if grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "$SOURCE"; then
    die "forbidden systemd key: $key"
  fi
}

if [ "$(id -u)" -ne 0 ]; then
  die "must run as root"
fi

[ -f "$SOURCE" ] || die "source not found: $SOURCE"
[ -f "$TARGET" ] || die "target not found: $TARGET"

owner="$(stat -c '%U:%G' "$SOURCE")"
[ "$owner" = "$EXPECTED_OWNER" ] || die "source owner is $owner, expected $EXPECTED_OWNER"

require_line "[Service]"
require_line "User=wocod"
require_line "Group=wocod"
require_line "WorkingDirectory=/srv/woco/backend"
require_line "ExecStart=/srv/woco/.venv/bin/gunicorn woco.wsgi:application --bind 127.0.0.1:8000 --workers 5"

reject_key "ExecStartPre"
reject_key "ExecStartPost"
reject_key "ExecReload"
reject_key "PermissionsStartOnly"
reject_key "AmbientCapabilities"
reject_key "SupplementaryGroups"
reject_key "RootDirectory"
reject_key "RootImage"

systemd-analyze verify "$SOURCE"
/usr/bin/install -m 644 "$SOURCE" "$TARGET"
/bin/systemctl daemon-reload

echo "Installed $TARGET"
