# Deployment

This document describes the secure hosted deploy flow for WorldCovers.

Source of truth:

- `.github/workflows/build-and-deploy.yml`: staging deploy to `woco.dev`
- `.github/workflows/deploy-prod.yml`: production deploy to `hellowoco.app`
- `deploy/deploy.sh`: unprivileged app build and migration steps
- `deploy/worldcovers.service`: gunicorn systemd service definition
- `deploy/worldcovers-apply-unit.sh`: staging-only root-owned unit helper

## Hosts

Both hosts keep the repo at `/srv/woco`, owned by `wocod:wocod`.
GitHub Actions deploys over SSH as `wocod`; it never SSHes as `root`.

```text
staging:
  host: woco.dev
  branch: staging
  secret: STAGING_DEPLOY_SSH_KEY
  unit updates: sudo /usr/local/sbin/worldcovers-apply-unit

production:
  host: hellowoco.app
  branch: main
  secret: PROD_DEPLOY_SSH_KEY
  unit updates: manual root apply only
```

Expected host layout:

```text
/srv/woco/
  backend/
  deploy/
  frontend/
  tools/
  mysql.cnf
  backend/.env
  .venv/
  backups/
```

## GitHub Actions Deploy Flow

Build job on both branches:

```sh
bash tools/fingerprint.sh
uv sync --no-dev --frozen
cd frontend && npm ci && npm run build
uv run python backend/manage.py check
```

Staging deploy over SSH as `wocod`:

```sh
git -C /srv/woco fetch origin
git -C /srv/woco reset --hard origin/staging
git -C /srv/woco clean -fd backend/common/migrations
sudo -n /usr/local/sbin/worldcovers-apply-unit  # only if the unit differs
sudo -n /bin/systemctl stop worldcovers
cd /srv/woco && ./deploy/deploy.sh
sudo -n /bin/systemctl start worldcovers
```

Production deploy over SSH as `wocod`:

```sh
git -C /srv/woco fetch origin
git -C /srv/woco reset --hard origin/main
git -C /srv/woco clean -fd backend/common/migrations
diff -q /srv/woco/deploy/worldcovers.service /etc/systemd/system/worldcovers.service
sudo -n /bin/systemctl stop worldcovers
cd /srv/woco && ./deploy/deploy.sh
sudo -n /bin/systemctl start worldcovers
```

Production deploy fails before stopping the service if the checked-in unit file
differs from the installed unit. A root operator must review and apply that
change manually.

## What deploy/deploy.sh Does

Run from repo root on the server:

```sh
./deploy/deploy.sh
```

Expected exit code: `0`.

Steps:

1. `uv sync --no-dev --frozen`
2. `uv run python backend/manage.py migrate --noinput`
3. `cd frontend && npm ci && npm run build`
4. `uv run python backend/manage.py collectstatic --noinput`

The script does not stop, start, or restart systemd. The caller owns service
lifecycle.

## Sudoers

Production allows only service stop and start:

```text
wocod ALL=(ALL) NOPASSWD: /bin/systemctl stop worldcovers
wocod ALL=(ALL) NOPASSWD: /bin/systemctl start worldcovers
```

Staging allows service stop, service start, and the audited root-owned helper:

```text
wocod ALL=(ALL) NOPASSWD: /bin/systemctl stop worldcovers
wocod ALL=(ALL) NOPASSWD: /bin/systemctl start worldcovers
wocod ALL=(root) NOPASSWD: /usr/local/sbin/worldcovers-apply-unit
```

Do not grant direct CI sudo access to `/usr/bin/install` or
`/bin/systemctl daemon-reload` on either host.

## Staging Unit Helper

Install or refresh the helper as root on `woco.dev`:

```sh
cd /srv/woco
install -o root -g root -m 0755 deploy/worldcovers-apply-unit.sh /usr/local/sbin/worldcovers-apply-unit
visudo -cf /etc/sudoers.d/wocod-deploy
```

Expected exit code: `0`.

The helper validates `/srv/woco/deploy/worldcovers.service`, runs
`systemd-analyze verify`, installs to `/etc/systemd/system/worldcovers.service`,
and runs `systemctl daemon-reload`.

The helper is staging-only. It is intentionally not part of the production
sudoers policy.

## Production Manual Unit Apply

When production deploy blocks because the unit changed, review the diff and run
as root on `hellowoco.app`:

```sh
diff -u /etc/systemd/system/worldcovers.service /srv/woco/deploy/worldcovers.service
systemd-analyze verify /srv/woco/deploy/worldcovers.service
install -m 644 /srv/woco/deploy/worldcovers.service /etc/systemd/system/worldcovers.service
systemctl daemon-reload
systemctl restart worldcovers
systemctl status worldcovers
```

Expected exit code: `0`.

## Deploy Key Rotation

Generate separate keys for the two deploy boundaries:

```sh
ssh-keygen -t ed25519 -C worldcovers-actions-staging -f worldcovers-actions-staging
ssh-keygen -t ed25519 -C worldcovers-actions-prod -f worldcovers-actions-prod
```

Install only `worldcovers-actions-staging.pub` for
`wocod@woco.dev`. Install only `worldcovers-actions-prod.pub` for
`wocod@hellowoco.app`.

Update GitHub Actions secrets:

```text
STAGING_DEPLOY_SSH_KEY = private key from worldcovers-actions-staging
PROD_DEPLOY_SSH_KEY = private key from worldcovers-actions-prod
```

After both new deploys succeed, delete the old shared `DEPLOY_SSH_KEY` secret
and remove its public key from all server `authorized_keys` files, including
root on staging if it was added there.

## SSH Policy

Production should set:

```text
PermitRootLogin no
```

Staging should set `PermitRootLogin no` after `wocod` deploys work. Use
`PermitRootLogin prohibit-password` only while recovering or provisioning the
host.

## Review Checklist For Deploy PRs

Use this checklist on any PR that changes workflows, SSH users, hosts, sudoers,
deploy keys, branch routing, or systemd files:

- Separate deploy keys per host and privilege boundary.
- Never promote a CI key from service-account access to root access.
- Avoid shared secrets across environments.
- Treat systemd unit updates as privileged infrastructure changes.
- Make production fail closed before stopping the service.
- Keep the repo and generated text ASCII-only.

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
