# Deployment

This document describes the current staging deployment for
`https://hellowoco.app`.

Source of truth:

- `.github/workflows/build-and-deploy.yml` controls the hosted deploy flow.
- `tools/deploy.sh` performs the unprivileged app build steps.
- `tools/worldcovers.service` defines the gunicorn systemd service.

## Current Staging Host

- Host: `hellowoco.app` on Ubuntu LTS
- Branch: `staging`
- Repo path: `/srv/woco`
- App user: `wocod`
- Service: `worldcovers`
- Frontend build: `frontend/dist/`, generated on deploy and not committed

Expected host layout:

```text
/srv/woco/
  backend/
  frontend/
  tools/
  mysql.cnf
  backend/.env
  .venv/
  backups/
```

Required config files:

- `/srv/woco/mysql.cnf`: MySQL credentials read by Django through
  `read_default_file`.
- `/srv/woco/backend/.env`: Django runtime config read by python-decouple,
  including `DEBUG`, `SECRET_KEY`, and `ALLOWED_HOSTS`.

## What GitHub Actions Does

On push to `staging`, `.github/workflows/build-and-deploy.yml` runs a build
job and then a deploy job.

Build job:

```sh
bash tools/fingerprint.sh
uv sync --no-dev --frozen
cd frontend && npm ci && npm run build
uv run python backend/manage.py check
```

Deploy job, over SSH as `wocod`:

```sh
sudo -n /bin/systemctl stop worldcovers
git -C /srv/woco fetch origin
git -C /srv/woco reset --hard origin/staging
sudo -n /usr/bin/install -m 644 /srv/woco/tools/worldcovers.service /etc/systemd/system/worldcovers.service
sudo -n /bin/systemctl daemon-reload
cd /srv/woco && ./tools/deploy.sh
sudo -n /bin/systemctl start worldcovers
```

The unit install and daemon reload run only when the checked-in unit differs
from the installed unit.

## What tools/deploy.sh Does

Run from repo root on the server:

```sh
./tools/deploy.sh
```

Expected exit code: `0`.

Steps:

1. `uv sync --no-dev --frozen`
2. `uv run python backend/manage.py migrate --noinput`
3. `cd frontend && npm ci && npm run build`
4. `uv run python backend/manage.py collectstatic --noinput`

The script does not stop, start, or restart systemd. The caller owns service
lifecycle. In staging, the GitHub Actions deploy job is that caller.

## Sudoers

The `wocod` user needs only the privileged commands that the workflow runs:

```text
wocod ALL=(ALL) NOPASSWD: /bin/systemctl stop worldcovers
wocod ALL=(ALL) NOPASSWD: /bin/systemctl start worldcovers
wocod ALL=(ALL) NOPASSWD: /bin/systemctl daemon-reload
wocod ALL=(ALL) NOPASSWD: /usr/bin/install -m 644 /srv/woco/tools/worldcovers.service /etc/systemd/system/worldcovers.service
```

Keep the command paths in sudoers aligned with the workflow.

## Manual Deploy

Manual deploy from the staging host:

```sh
cd /srv/woco
sudo -n /bin/systemctl stop worldcovers
git fetch origin
git reset --hard origin/staging
if ! diff -q tools/worldcovers.service /etc/systemd/system/worldcovers.service >/dev/null 2>&1; then
  sudo -n /usr/bin/install -m 644 /srv/woco/tools/worldcovers.service /etc/systemd/system/worldcovers.service
  sudo -n /bin/systemctl daemon-reload
fi
./tools/deploy.sh
sudo -n /bin/systemctl start worldcovers
```

Expected exit code: `0`.

## Pushing Catalog Data To Staging

Catalog data is outside git. `tools/push_data.sh` syncs local work files to
the host:

```sh
./tools/push_data.sh
./tools/push_data.sh --dry-run
./tools/push_data.sh --import
```

It syncs:

- `tools/wip/` to `/srv/woco/tools/wip/`
- `backend/media/` to `/srv/woco/backend/media/`

With `--import`, it then runs `/srv/woco/tools/reload_data.sh` as `wocod`.
That reload script is the source of truth for data refresh behavior.

Current reload sequence:

```sh
uv run python backend/manage.py import_ascc_bundle tools/wip/cache/ascc1
uv run python backend/manage.py apply_ascc2_overlay \
  --base-dir tools/wip/cache/ascc1 \
  --overlay-dir tools/wip/cache/ascc2_overlay_bundle \
  --overlay-map tools/wip/out/VA_ASCC2_overlay_map.csv \
  --v1-image-refs tools/wip/in/v1_VA_image_refs.csv \
  --region-abbrev VA \
  --ascc1-code ASCC1 \
  --ascc2-code ASCC2 \
  --audit-user-id "${WOCO_ASCC_AUDIT_USER_ID:-1}" \
  --skip-missing-images
```

This reload no longer calls `wipe_user_data` and no longer passes
`--truncate` to `import_ascc_bundle`. Existing rows are updated in place by the
import and overlay commands.

## Troubleshooting

Check service state:

```sh
sudo systemctl status worldcovers
sudo journalctl -u worldcovers -f
```

Check deploy prerequisites:

```sh
cd /srv/woco
uv --version
node --version
npm --version
test -f mysql.cnf
test -f backend/.env
```

If a page returns 502, inspect the systemd journal first. Look for gunicorn
worker timeouts, Python tracebacks, missing environment values, or failed
database connections.
