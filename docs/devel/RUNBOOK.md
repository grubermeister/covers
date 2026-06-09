# Operator Runbook

Day-to-day commands for running WorldCovers on staging.

Source of truth:

- Deployment flow: `.github/workflows/build-and-deploy.yml`
- App build steps: `tools/deploy.sh`
- Data refresh: `tools/reload_data.sh`
- Service definition: `tools/worldcovers.service`

## Service Management

WorldCovers runs as the `worldcovers` systemd service backed by gunicorn.

```sh
sudo systemctl status worldcovers
sudo systemctl stop worldcovers
sudo systemctl start worldcovers
sudo systemctl restart worldcovers
sudo journalctl -u worldcovers -f
```

Expected exit code for successful commands: `0`.

## Manual Deploy

From the staging host:

```sh
cd /srv/woco
sudo -n /bin/systemctl stop worldcovers
git fetch origin
git reset --hard origin/staging
./tools/deploy.sh
sudo -n /bin/systemctl start worldcovers
```

`tools/deploy.sh` syncs Python dependencies, runs migrations, builds the
frontend, and collects static files. It does not start or stop the service.

For the full unit-file update flow and sudoers requirements, see
[DEPLOY.md](DEPLOY.md).

## Data Refresh

From a local checkout with prepared `tools/wip/` and `backend/media/` data:

```sh
./tools/push_data.sh --dry-run
./tools/push_data.sh --import
```

`--import` runs `tools/reload_data.sh` on the server as `wocod`.

Current server-side reload sequence:

```sh
uv run python backend/manage.py import_ascc_bundle tools/wip/cache/ascc1
uv run python backend/manage.py apply_ascc2_overlay ...
```

This refresh does not call `wipe_user_data` and does not pass `--truncate`.
Existing rows are updated in place by the import and overlay commands.

## Auth Backups

Run before destructive staging refreshes when you need a portable copy of
users, groups, email addresses, collections, and assignments:

```sh
./woco backup_auth users.csv groups.csv emails.csv collections.csv assignments.csv
./woco restore_auth users.csv groups.csv emails.csv collections.csv assignments.csv
```

These files may contain email addresses and password hashes. Store them as
sensitive artifacts.

## Database Restore

Database backups live under `backups/` when present.

```sh
mysql -u wocod -p worldcovers < backups/worldcovers_YYYY-MM-DD.sql
./woco migrate
```

Expected exit code: `0`.

## Admin Checks

Spot-check these paths after deploys and data refreshes:

- `/admin/`
- `/admin/common/contribution/`
- `/search`
- `/help`

For management command details, see [TOOLS.md](TOOLS.md).

## Runtime Environment

Production and staging read:

- `/srv/woco/mysql.cnf`: database credentials.
- `/srv/woco/backend/.env`: `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, and any
  other decouple-backed Django settings.
