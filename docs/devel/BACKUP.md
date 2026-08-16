# Backups

Nightly database + media snapshots, on-box retention, a weekly pull to a
workstation, and a restore that has actually been performed.

Before 2026-08-15 there were no scheduled backups on either host. The only full
backup was the manual one-off in `backups/2026-08-07/`, on a single workstation.

## The two rules that matter

**1. Both halves, always.** `settings.py:185` sets `MEDIA_ROOT = BASE_DIR/"media"`,
so image bytes live on disk, not in the database. A SQL dump contains the
`images` rows and none of the files they point at. **Restoring only the database
yields a complete-looking catalog in which every image link is broken.** Every
snapshot carries both halves; `worldcovers-restore` restores both.

**2. Dumps are not engine-portable.** woco.dev runs MySQL 8.0.46, prod runs
MariaDB 10.11, and Django emits different DDL per engine from the same
migrations. A MariaDB dump aborts partway through a MySQL restore at `uuid`
columns and `longtext ... CHECK (json_valid(...))` — see `ISSUE-2026-08-10-01`.
`worldcovers-restore` refuses a family mismatch up front rather than failing
halfway.

## Layout

```
/var/backups/woco/
  snapshots/<name>/db/worldcovers.sql.zst   zstd -19 dump
  snapshots/<name>/db/dump.stderr           dumper stderr, kept as evidence
  snapshots/<name>/media/                   hardlinked media tree
  snapshots/<name>/MANIFEST.json            engine, row counts, media census
  snapshots/<name>/SHA256SUMS               dump + every media file
  latest -> snapshots/<name>                newest VERIFIED snapshot
  STATUS.json  LAST_SUCCESS  ALERT          monitoring surface
```

`<name>` is `YYYY-MM-DDTHHMMSSZ`, plus `-<tag>` for on-demand runs.

**Not** under `/srv/woco`: `deploy.sh` runs `git reset --hard` there, and a
recovery re-clone would take the backups with it.

## Everyday use

```sh
# check health without logging in
ssh reese@<host> 'cat /var/backups/woco/STATUS.json'

# on-demand snapshot before doing something destructive (no password needed)
sudo -u wocod /usr/local/sbin/worldcovers-backup --tag pre-something

# re-verify an existing snapshot end to end
sudo -u wocod /usr/local/sbin/worldcovers-backup --verify-only <name>

# pull to the workstation; non-zero exit if any host is stale
./tools/pull_backups.sh
```

Tagged runs **never prune**. That is what makes the NOPASSWD sudo grant
non-destructive by construction.

## Restore

```sh
# rehearsal: scratch database, service untouched, media untouched
sudo -u wocod /usr/local/sbin/worldcovers-restore \
  --snapshot <name> --into worldcovers_rehearsal

# the real thing
sudo -u wocod /usr/local/sbin/worldcovers-restore \
  --snapshot <name> --into worldcovers \
  --i-understand-this-overwrites $(hostname)
```

The live path stops gunicorn, verifies `SHA256SUMS`, checks the engine family,
drops and reloads the database, asserts every row count in the manifest, moves
the existing media tree aside as `media.pre-restore-<ts>` (a same-filesystem
rename — instant and reversible), restores media, and restarts.

**Row-count drift is reported, not fatal.** The manifest census is taken from
the live database a few seconds before `mysqldump`'s `START TRANSACTION`, so a
write inside that window is a legitimate off-by-a-few. A *missing table* is a
hard failure.

### Bare-metal recovery

1. Re-provision per `DEPLOY.md` (`deploy/provision.sh` installs backups too).
2. `rsync` a snapshot from the workstation to `/var/backups/woco/snapshots/`.
3. `worldcovers-restore --snapshot <name> --into worldcovers --i-understand-this-overwrites <host>`.

## Rehearsal

A backup nobody has restored is not a backup. Rehearse quarterly, and after any
change to the dump flags.

```sh
./tools/pull_backups.sh --host woco-dev
./tools/rehearse_restore.sh --host woco-dev --latest --with-media
```

`--with-media` runs `verify_media_integrity` against the restored tree: every
`Image` row must resolve to a file whose sha256 matches. That is the half a
database restore never exercises.

woco.dev rehearses trivially because this workstation runs the same MySQL
8.0.46. **Prod does not** — its MariaDB dumps cannot load here, so prod
rehearsal needs the standalone-tarball recipe in `backups/2026-08-07/README.md`.

> The workstation needs the same grant the installer applies on the box
> (`GRANT ALL ON \`worldcovers\_%\`.*`), or pass
> `WOCO_REHEARSAL_DB=test_worldcovers`.

### Rehearsal log

| Date | Snapshot | Result | Notes |
|---|---|---|---|
| 2026-08-15 | woco-dev/2026-08-15T165404Z | **PASS** | 43 tables, census exact. Media: 2,939 image rows over 2,567 distinct files — 0 missing, 0 corrupt, 0 size drift. First time the media half has ever been restored. |

## Monitoring

Five layers, because four of them have a blind spot.

| Layer | Catches | Blind to |
|---|---|---|
| `OnFailure=` → journal + `ALERT` | the script ran and failed | the timer never firing; a dead box |
| `STATUS.json` / `LAST_SUCCESS` age | missed runs, however caused | nobody reading it |
| MOTD banner at >36 h | anything, on your next login | not logging in |
| `pull_backups.sh` non-zero exit | staleness on either host, weekly | the workstation being off |
| **dead-man's switch** (optional) | **a dead box, a disabled timer, a full disk** | nothing |

Neither box has an MTA (`msmtp`, `mail`, `sendmail` all absent), so email is not
an option without installing one. Only the dead-man's switch is genuinely
out-of-band — everything else needs someone to show up. To enable:

```sh
echo 'HEALTHCHECK_URL=https://hc-ping.com/<uuid>' > /etc/worldcovers-backup.env
chmod 640 /etc/worldcovers-backup.env && chown root:wocod /etc/worldcovers-backup.env
```

## Retention

By rule, not by promotion — stateless and idempotent, so a multi-day outage
cannot produce a missing weekly or a double-promoted daily:

1. keep everything from the last 7 days
2. keep the newest snapshot in each of the last 4 ISO weeks
3. keep tagged snapshots: newest 10, and never one younger than 30 days
4. never delete the target of `latest`
5. refuse to prune if fewer than 2 *real* snapshots would remain

Pruning runs only under `--scheduled`, and only after a verified publish, so a
bad night cannot rotate away good snapshots.

Media snapshots are hardlinked against the **previous snapshot** (never the live
tree, so corrupting a live file cannot reach back into a backup). Measured on
woco.dev: two snapshots that would be 2.52 GB as independent copies occupy
1.26 GB — the second cost 2.8 MB.

## Protected destructive paths

`tools/reload_data.sh` and `tools/wipe_user_data.sh --reload` run
`ascc import --truncate`, which DELETEs all 14 catalog tables. Both now take a
tagged snapshot first and **refuse to run without one**
(`WOCO_ALLOW_UNBACKED_IMPORT=1` overrides, at your own risk).

## Gotchas

- **`sudo -u wocod` inherits the caller's working directory.** From `/home/reese`
  (0750), `wocod` cannot read it and GNU `find` aborts *after* doing its work.
  Both scripts `cd /` at startup. The systemd unit was never affected — it sets
  `WorkingDirectory=`.
- **SFTP is disabled on both boxes.** `scp` fails with `subsystem request failed
  on channel 0`. Use `rsync`, `ssh host 'cat f' >`, or `scp -O`.
- **`set -o pipefail` in the backup script is load-bearing.** `mysqldump | zstd`
  otherwise reports zstd's status, so a dump dying mid-table exits 0 and then
  rotates the good snapshots away. The trailer and table-count checks back it up.
- **A correct row `count` is not evidence a dump is complete.** Only the
  `-- Dump completed on` trailer is.

## Known gap: unreferenced media

The 2026-08-15 rehearsal found **5,126 of 7,693 media files have no `Image`
row**: 5,795 v1-legacy `Marking-*.jpg` files, 1,217 pending `vphc-*` contribution
scans (which become `Image` rows on approval), and media staged for states not
yet in the catalog (`oh`, `nj`, `ma`, `pa`). Pre-existing and unrelated to
backups, which deliberately preserve everything rather than deciding what is
garbage. Worth an audit, tracked separately.
