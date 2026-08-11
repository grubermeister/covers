# User-safe catalog refresh (drop + re-import without losing user data)

`drop_ascc_state` deletes a state's catalog tree **including** the
user-submitted covers, images, dates, citations, and contributions hanging
off its markings — and post-policy bundles contain no covers, so nothing
puts them back. This runbook wraps the drop/re-import in the batch
backup/restore commands so user content survives.

Related: `docs/devel/PIPELINE.md` (bundle production), `AUTH_SYNC.md`
(auth tables move separately via `backup_auth`/`restore_auth`).

## Sequence

```bash
# 0. Preflight — enumerate what will be preserved; check the warnings.
python backend/manage.py backup_user_markings ./tools/wip/user-backups --list-only

# 1. Backup (fail-fast: any export error aborts before anything is dropped).
python backend/manage.py backup_user_markings ./tools/wip/user-backups

# 2. Drop each region being refreshed (dry-run first).
./woco ascc drop USA-VA1 --dry-run
./woco ascc drop USA-VA1

# 3. Re-import the re-run bundles (natural-key incremental).
python backend/manage.py import_apmc_bundle ./tools/wip/out/

# 4. Restore user content (dry-run first; failures don't stop the batch).
python backend/manage.py restore_user_markings ./tools/wip/user-backups --dry-run
python backend/manage.py restore_user_markings ./tools/wip/user-backups

# 5. Review ./tools/wip/user-backups/restore_report.json — the `failures`
#    list is the editor review queue (markings whose codes no longer resolve
#    after the re-run are reported, never silently dropped).
```

## Caveats

- **Markings without a `code` cannot be backed up** (`backup_marking` is
  code-keyed). The backup manifest lists their pks; they will be lost by a
  drop unless handled manually first.
- **Covers linked to no marking** are unreachable from per-marking backups;
  also listed in the manifest.
- **Image binaries are not copied** — backups carry `storage_filename`
  metadata only. Media files under `MEDIA_ROOT` are untouched by the drop,
  so restored rows re-link to the files already on disk. Verify a sample
  image renders after restore.
- **Restoring an editor-edited marking re-applies its backed-up field
  values** over the freshly imported row (editor data wins over munger
  output for markings a person actually touched). This is intentional;
  it also means such rows do not pick up munger fixes — the restore report
  tells you which rows those are.
- Prove the full sequence on a **local copy of the production/dev DB**
  before running it on woco.dev or prod.
