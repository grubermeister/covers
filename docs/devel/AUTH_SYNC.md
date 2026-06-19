# Auth sync — copy users/logins between WoCo hosts

Goal: let people log into a secondary host (e.g. the `woco.dev` review box) with
the **same credentials** they already use on `hellowoco.app`. Two Django
management commands move the auth/config data:

- `backup_auth` — **export** users (incl. password hashes), groups, email
  addresses, state Collections, and Collection assignments to one JSON file.
- `restore_auth` — **import** that JSON on the target host.

> SSO is the long-term plan; this back-and-forth is the interim mechanism and is
> intentionally not made more complicated than export → import.

The `woco` shim in the repo root is shorthand for
`uv run python backend/manage.py`, so `/srv/woco/woco backup_auth …` works on a
deployed box. Run with **no args** to see the expected arguments.

## ⚠️ Sensitivity

The backup file contains **email addresses and password hashes**. Treat it like
a secret: move it only over SSH/scp, keep it out of git, and delete it from both
hosts once the restore is confirmed. (`backup_auth` prints this reminder too.)

## Source side — export on `hellowoco.app` (prod)

```bash
# On the prod host, as the app user, with uv on PATH:
sudo -u <appuser> -H bash -lc \
  'export PATH=$HOME/.local/bin:$PATH && cd /srv/woco && ./woco backup_auth /tmp/woco-auth.json'
```

Produces `/tmp/woco-auth.json`. (Use `--emit-csv /tmp/woco-auth/` instead for a
directory of CSVs if you prefer to inspect them.)

## Move the file

```bash
# Pull prod -> local, then push local -> woco.dev (never store it long-term):
scp <prod>:/tmp/woco-auth.json ./woco-auth.json
scp ./woco-auth.json root@172.238.189.147:/tmp/woco-auth.json
```

## Target side — restore on `woco.dev`

```bash
# Dry-run first (validates + rolls back, commits nothing):
sudo -u wocod -H bash -lc \
  'export PATH=$HOME/.local/bin:$PATH && cd /srv/woco && ./woco restore_auth /tmp/woco-auth.json --dry-run'

# Then for real:
sudo -u wocod -H bash -lc \
  'export PATH=$HOME/.local/bin:$PATH && cd /srv/woco && ./woco restore_auth /tmp/woco-auth.json'
```

`restore_auth` runs inside a single transaction. It imports groups → users →
emails → collections → assignments. Notes:

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

## Prerequisite (prod access)

Running `backup_auth` on `hellowoco.app` requires SSH access to the prod host.
If you don't have it yet, send your SSH **public key** to Michael to add to the
prod host's `authorized_keys`. Reese's key:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKNGHm/OjKF0joBSY+5j++fU94plM8DGv3mKOETTovHQ reese@DESKTOP-MVE0I6R
```

Alternatively, Michael can run `backup_auth` on prod himself and send the JSON;
the `restore_auth` step on `woco.dev` is then self-contained.
