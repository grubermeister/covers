# Auth sync — copy users/logins between WoCo hosts

Goal: let people log into a secondary host (e.g. the `woco.dev` review box) with
the **same credentials** they already use on `hellowoco.app`. Two Django
management commands move the auth/config data:

- `backup_auth` — **export** users (incl. password hashes), groups, email
  addresses, state Collections, and Collection assignments to one JSON file.
- `restore_auth` — **import** that JSON on the target host.

> SSO is the long-term plan; this back-and-forth is the interim mechanism and is
> intentionally not made more complicated than export -> import.

The `woco` shim in the repo root is shorthand for
`uv run python backend/manage.py`, so `/srv/woco/woco backup_auth` works on a
deployed box. Run with **no args** to see the expected arguments.

## ⚠️ Sensitivity

The backup file contains **email addresses and password hashes**. Treat it like
a secret: move it only over SSH/scp, keep it out of git, and delete it from both
hosts once the restore is confirmed. (`backup_auth` prints this reminder too.)

## Source-side Export

```bash
# On the prod host, as the app user, with uv on PATH:
sudo -u <appuser> -H bash -lc \
  'export PATH=$HOME/.local/bin:$PATH && cd /srv/woco && ./woco backup_auth /tmp/woco-auth.json'
```

Produces `/tmp/woco-auth.json`. (Use `--emit-csv /tmp/woco-auth/` instead for a
directory of CSVs if you prefer to inspect them.)

## Move the file

```bash
# Pull prod -> local, then push local -> target (never store it long-term):
# Root login should be disabled on the box — connect as your own user (in the sudo group).
scp <prod>:/tmp/woco-auth.json ./woco-auth.json
scp ./woco-auth.json <your-user>@<target-server>:/tmp/woco-auth.json
```

## Target-side Restore

```bash
# Dry-run first (validates + rolls back, commits nothing):
sudo -u wocod -H bash -lc \
  'export PATH=$HOME/.local/bin:$PATH && cd /srv/woco && ./woco restore_auth /tmp/woco-auth.json --dry-run'

# Then for real:
sudo -u wocod -H bash -lc \
  'export PATH=$HOME/.local/bin:$PATH && cd /srv/woco && ./woco restore_auth /tmp/woco-auth.json'
```

`restore_auth` runs inside a single transaction. It imports groups > users >
emails > collections > assignments. 

Notes:
- **Collections** whose name conflicts with a Region the target host already
  owns are **skipped** (and their assignments with them) — the command prints
  which were skipped. This is expected on a box that only carries a subset of
  states.
- **Assignments** are mirrored: rows not present in the backup are removed, so
  the target ends up matching the source's assignment set.
- Existing target users with the same natural key are updated in place (so the
  box's own `admin` is preserved unless prod also defines it).

## Cleanup

```bash
rm -f ./woco-auth.json
sudo -u wocod -H bash -lc 'rm -f /tmp/woco-auth.json'   # on woco.dev
# and remove /tmp/woco-auth.json on prod
```
