# Tools Reference

This document covers host scripts and checked-in Django management commands.

Source of truth for command availability:

- Host scripts live under `tools/`.
- Django commands live under `backend/common/management/commands/`.
- Run `./woco help` from repo root to list commands in the current checkout.

Use `./woco <command>` from repo root for Django management commands. The
`./woco` shim wraps `uv run woco`, which calls `woco_cli.py` and then Django's
`execute_from_command_line`.

## Overview

| Tool | Purpose | Location |
|------|---------|----------|
| `deploy.sh` | Sync deps, migrate, build frontend, collect static | `tools/deploy.sh` |
| `push_data.sh` | Sync local catalog data and media to staging | `tools/push_data.sh` |
| `reload_data.sh` | Reload staging catalog data on the server | `tools/reload_data.sh` |
| `worldcovers.service` | systemd unit for gunicorn | `tools/worldcovers.service` |
| `rebuild_staging_db.sh` | Drop and recreate the staging database | `tools/rebuild_staging_db.sh` |
| `setup_worldcovers_db.sql` | Create database/user grants | `tools/setup_worldcovers_db.sql` |
| `ascc_page_processor.py` | Render and split ASCC catalog pages | `tools/ascc_page_processor.py` |
| `ascc_page_extract.py` | Extract ASCC listing text from page chunks | `tools/ascc_page_extract.py` |
| `ascc_image_extract.py` | Extract marking images from page chunks | `tools/ascc_image_extract.py` |
| `ascc_data_munger.py` | Build Django-shape ASCC CSV bundles | `tools/ascc_data_munger.py` |
| `ascc import` | Load an ASCC CSV bundle | `backend/common/management/commands/import_ascc_bundle.py` |
| `import_apmc_bundle` | Umbrella importer; delegates to ASCC today | `backend/common/management/commands/import_apmc_bundle.py` |
| `wipe_user_data` | Clear submission/version/recycle-bin data | `backend/common/management/commands/wipe_user_data.py` |
| `purge_recycle_bin` | Permanently delete recycle-bin catalog rows | `backend/common/management/commands/purge_recycle_bin.py` |
| `prune_revisions` | Prune django-reversion rows safely | `backend/common/management/commands/prune_revisions.py` |
| `backup_marking` | Export one marking graph to JSON | `backend/common/management/commands/backup_marking.py` |
| `restore_marking` | Restore one marking graph from JSON | `backend/common/management/commands/restore_marking.py` |
| `backup_auth` | Export users and auth/collection config | `backend/common/management/commands/backup_auth.py` |
| `restore_auth` | Restore users and auth/collection config | `backend/common/management/commands/restore_auth.py` |
| `set_user_password` | Set a user's password from the CLI | `backend/common/management/commands/set_user_password.py` |

## Host Scripts

### `tools/deploy.sh`

Run from repo root on the staging host:

```sh
./tools/deploy.sh
```

Expected exit code: `0`.

Steps:

1. `uv sync --no-dev --frozen`
2. `uv run python backend/manage.py migrate --noinput`
3. `cd frontend && npm ci && npm run build`
4. `uv run python backend/manage.py collectstatic --noinput`

The script does not manage systemd. The caller must stop/start/restart the
service. In staging, `.github/workflows/build-and-deploy.yml` is the caller.

### `tools/push_data.sh`

Run from a local checkout:

```sh
./tools/push_data.sh
./tools/push_data.sh --dry-run
./tools/push_data.sh --import --state VA
./tools/push_data.sh --import --bundle-dir tools/wip/out/v1_va
```

Expected exit code: `0`.

Syncs local `tools/wip/` and `backend/media/` to the staging host. With
`--import`, it runs `/srv/woco/tools/reload_data.sh` remotely as `wocod` for
the selected bundle. `--state VA` resolves to `tools/wip/out/v1_va`.

### `tools/reload_data.sh`

Run on the staging host as `wocod`:

```sh
sudo -u wocod /srv/woco/tools/reload_data.sh tools/wip/out/v1_va
```

Expected exit code: `0`.

Current sequence:

```sh
./woco ascc import tools/wip/out/v1_va --truncate
```

This reload does not call `wipe_user_data`. It does pass `--truncate`, which
deletes all 14 catalog import tables, including covers, before reloading the
bundle. Run `wipe_user_data` first when submission, version, and recycle-bin
history must also be cleared.

### `tools/rebuild_staging_db.sh`

Run only when staging must be reset from scratch:

```sh
./tools/rebuild_staging_db.sh
```

Expected exit code: `0`.

This drops and recreates the staging database using
`tools/setup_worldcovers_db.sql`, then runs migrations. It is destructive.

## ASCC Pipeline Tools

The canonical ASCC v1-export-to-bundle workflow is documented in [PIPELINE.md](PIPELINE.md).
Use the state-centered wrapper for demos and normal runs:

```sh
./woco ascc doctor VA
./woco ascc munge VA
./woco ascc run VA --dry-run
./woco ascc run VA
```

The wrapper preserves the existing `tools/wip/in`, `tools/wip/cache`, and
`tools/wip/out` layout. For state `VA`, the main handoff files are:

```text
tools/wip/cache/v1/VA/catalog_rows.csv
tools/wip/cache/v1/VA/image_refs.csv
tools/wip/out/v1_va/
tools/wip/out/v1_va/source_marking_map.csv
tools/wip/out/v1_va/v1_pipeline_warnings.csv
tools/wip/cache/v1/VA/run.json
```

`./woco ascc munge VA` builds the bundle without importing it.
`./woco ascc run VA` runs the same munge step and then imports the generated
bundle. Pass `--dry-run` to validate through the importer and roll back.
`./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf` runs the legacy scanned
PDF OCR pipeline.

Clean generated cache/output files without touching source PDFs or seed CSVs:

```sh
./woco ascc clean VA
./woco ascc clean
```

With a state, `clean` removes generated cache files for that state plus
`tools/wip/out/<state>/` and `tools/wip/out/v1_<state>/`. Without a state, it
clears generated contents under `tools/wip/cache/` and `tools/wip/out/` for all
states while preserving placeholder files.

Expected exit code for each successful command: `0`.

The API-dependent OCR command, `./woco ascc ocr`, defaults to OpenRouter:

```sh
OPENROUTER_API_KEY=<key> ./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf
```

Use the direct Anthropic Claude API with `--provider anthropic`:

```sh
ANTHROPIC_API_KEY=<key> ./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf --provider anthropic
```

Or select the provider through environment variables:

```sh
PIPELINE_LLM_PROVIDER=anthropic \
PIPELINE_LLM_MODEL=claude-sonnet-4-6 \
ANTHROPIC_API_KEY=<key> \
./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf
```

OpenRouter's default model is `anthropic/claude-sonnet-4.6`. Anthropic's
direct default model is `claude-sonnet-4-6`.

## Management Commands

### `ascc import`

Load a Django-shape ASCC CSV bundle into catalog tables.

```sh
./woco ascc import tools/wip/out/v1_va --dry-run
./woco ascc import tools/wip/out/v1_va
```

Useful flags:

- `--dry-run`: validate and roll back.
- `--truncate`: delete catalog rows before loading.
- `--only colors,markings`: load selected stems in dependency order.
- `--allow-missing`: skip absent CSV stems.

Required stems for current bundles:

```text
colors
letterings
shapes
regions
reference_works
post_offices
post_office_regions
markings
dates_seen
citations
images
```

Optional stems:

```text
covers
cover_markings
cover_valuations
```

### `import_apmc_bundle`

Umbrella importer for the American Postal Markings Catalog. It delegates to
`import_ascc_bundle` in the current codebase.

```sh
./woco import_apmc_bundle tools/wip/out --dry-run
./woco import_apmc_bundle tools/wip/out --truncate
```

It accepts the same `--only`, `--allow-missing`, `--dry-run`, and `--truncate`
flags as `import_ascc_bundle`.

### `wipe_user_data`

Clear user-generated submissions, versions, and recycle bins while preserving
catalog tables, auth users, groups, collections, and collection assignments.

```sh
./woco wipe_user_data --dry-run
./woco wipe_user_data --no-input
```

Use this only when you intentionally need to clear submission, version, and
recycle-bin data before a separate destructive catalog refresh.

### `purge_recycle_bin`

Permanently hard-delete catalog markings and covers that are currently hidden
by recycle-bin rows. The command also deletes polymorphic image, date, and
citation rows for those subjects. It does not remove image files from disk and
does not prune django-reversion history.

```sh
./woco purge_recycle_bin --dry-run
./woco purge_recycle_bin --no-input
```

Use this when removed catalog records should no longer be restorable from the
editor recycle bin. The dry run reports what would be deleted and rolls back.
Expected exit code: `0`.

### `backup_marking` And `restore_marking`

Export one marking by `Marking.code` to a single JSON file, then restore the
same marking graph elsewhere. The JSON includes the marking, directly linked
covers, lookup rows, contribution rows, submission transactions, snapshot
versions, recycle-bin sidecars, dates, citations, and image metadata. It does
not copy image files; restored Image rows still point at
`MEDIA_ROOT/<storage_filename>`.

Run from repo root, with `backend/.env` and `mysql.cnf` present:

```sh
./woco backup_marking ASCC6-VA-M0001 backups/ASCC6-VA-M0001.json
./woco restore_marking backups/ASCC6-VA-M0001.json --dry-run
./woco restore_marking backups/ASCC6-VA-M0001.json
```

Matching auth users must already exist locally. `restore_marking` fails before
import when a contribution contributor username is missing. Expected exit
code: `0`.

### `prune_revisions`

Prune django-reversion storage while preserving the newest configured number
of Version rows per object. This command also purges legacy Version rows for
the custom audit/snapshot tables that are excluded from reversion tracking.

```sh
./woco prune_revisions --dry-run
./woco prune_revisions
```

The dry run reports what would be deleted and rolls back. Expected exit code:
`0`. Retention defaults live in `backend/woco/settings.py` as
`REVERSION_PRUNE_RETENTION_DAYS` and `REVERSION_PRUNE_KEEP_PER_OBJECT`.

### `backup_auth` And `restore_auth`

Export and restore users, groups, email addresses, collections, and collection
assignments.

```sh
./woco backup_auth users.csv groups.csv emails.csv collections.csv assignments.csv
./woco restore_auth users.csv groups.csv emails.csv collections.csv assignments.csv
./woco restore_auth users.csv groups.csv emails.csv collections.csv assignments.csv --dry-run
```

The exported files may contain email addresses and password hashes. Store them
as sensitive artifacts.

### `set_user_password`

Set a password without opening the Django shell.

```sh
./woco set_user_password <username> <new_password>
```

Expected exit code: `0`.
