# Operator Runbook

Day-to-day commands for running WorldCovers on staging and production.

Source of truth:

- Staging deployment flow: `.github/workflows/build-and-deploy.yml`
- Production deployment flow: `.github/workflows/deploy-prod.yml`
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

From the host as `wocod`; use `origin/staging` on staging and `origin/main`
on production:

```sh
cd /srv/woco
git fetch origin
git reset --hard origin/staging
sudo -n /bin/systemctl stop worldcovers
./tools/deploy.sh
sudo -n /bin/systemctl start worldcovers
```

`tools/deploy.sh` syncs Python dependencies, runs migrations, builds the
frontend, and collects static files. It does not start or stop the service.

For the full key-rotation, unit-file update, and sudoers requirements, see
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
uv run python backend/manage.py import_ascc_bundle tools/wip/out --truncate
```

This refresh does not call `wipe_user_data`. It does pass `--truncate`, which
deletes all 14 catalog import tables before reloading the bundle. Run
`wipe_user_data` first when submission, version, and recycle-bin history must
also be cleared.

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

## Revision Maintenance

Run the one-time django-reversion baseline after the skip-list code is live.
The order matters because excluded audit/snapshot models should not get
baseline revisions.

```sh
cd /srv/woco
./woco createinitialrevisions --comment "Initial baseline revision."
```

Expected exit code: `0`. The command prints per-model counts.

Run revision pruning manually as needed, for example monthly. No systemd timer
or cron job is installed for this yet.

```sh
cd /srv/woco
./woco prune_revisions --dry-run
./woco prune_revisions
```

Expected exit code: `0`. The dry run reports counts and rolls back. The
retention policy lives in `backend/woco/settings.py` as
`REVERSION_PRUNE_RETENTION_DAYS` and `REVERSION_PRUNE_KEEP_PER_OBJECT`.

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
