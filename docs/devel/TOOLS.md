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
| `build_ascc2_overlay.py` | Build ASCC2 overlay mapping data | `tools/build_ascc2_overlay.py` |
| `import_ascc_bundle` | Load an ASCC CSV bundle | `backend/common/management/commands/import_ascc_bundle.py` |
| `import_apmc_bundle` | Umbrella importer; delegates to ASCC today | `backend/common/management/commands/import_apmc_bundle.py` |
| `apply_ascc2_overlay` | Apply ASCC2 updates to an ASCC1 baseline | `backend/common/management/commands/apply_ascc2_overlay.py` |
| `wipe_user_data` | Clear submission/version/recycle-bin data | `backend/common/management/commands/wipe_user_data.py` |
| `prune_revisions` | Prune django-reversion rows safely | `backend/common/management/commands/prune_revisions.py` |
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
./tools/push_data.sh --import
```

Expected exit code: `0`.

Syncs local `tools/wip/` and `backend/media/` to the staging host. With
`--import`, it runs `/srv/woco/tools/reload_data.sh` remotely as `wocod`.

### `tools/reload_data.sh`

Run on the staging host as `wocod`:

```sh
sudo -u wocod /srv/woco/tools/reload_data.sh
```

Expected exit code: `0`.

Current sequence:

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

This reload does not call `wipe_user_data` and does not pass `--truncate`.
Existing rows are updated in place by the import and overlay commands.

### `tools/rebuild_staging_db.sh`

Run only when staging must be reset from scratch:

```sh
./tools/rebuild_staging_db.sh
```

Expected exit code: `0`.

This drops and recreates the staging database using
`tools/setup_worldcovers_db.sql`, then runs migrations. It is destructive.

## ASCC Pipeline Tools

The detailed ASCC PDF-to-bundle workflow is documented in [PIPELINE.md](PIPELINE.md).

Run these scripts from repo root. Their default paths resolve to `tools/wip/`
based on each script's location, not on the shell cwd:

```sh
uv run python tools/ascc_page_processor.py VA_ASCC_CTLG
uv run python tools/ascc_page_extract.py VA_ASCC_CTLG
uv run python tools/ascc_image_extract.py VA_ASCC_CTLG
uv run python tools/ascc_data_munger.py --input tools/wip/in/VA_ASCC_CTLG.csv --out-dir tools/wip/out/
```

Expected exit code for each successful step: `0`.

The API-dependent tools, `ascc_page_processor.py` stages `halves` and
`chunks` plus `ascc_page_extract.py`, default to OpenRouter:

```sh
OPENROUTER_API_KEY=<key> uv run python tools/ascc_page_processor.py VA_ASCC_CTLG
OPENROUTER_API_KEY=<key> uv run python tools/ascc_page_extract.py VA_ASCC_CTLG
```

Use the direct Anthropic Claude API with `--provider anthropic`:

```sh
ANTHROPIC_API_KEY=<key> uv run python tools/ascc_page_processor.py VA_ASCC_CTLG --provider anthropic
ANTHROPIC_API_KEY=<key> uv run python tools/ascc_page_extract.py VA_ASCC_CTLG --provider anthropic
```

Or select the provider through environment variables:

```sh
PIPELINE_LLM_PROVIDER=anthropic \
PIPELINE_LLM_MODEL=claude-sonnet-4-6 \
ANTHROPIC_API_KEY=<key> \
uv run python tools/ascc_page_extract.py VA_ASCC_CTLG
```

OpenRouter's default model is `anthropic/claude-sonnet-4.6`. Anthropic's
direct default model is `claude-sonnet-4-6`.

## Management Commands

### `import_ascc_bundle`

Load a Django-shape ASCC CSV bundle into catalog tables.

```sh
./woco import_ascc_bundle tools/wip/out --dry-run
./woco import_ascc_bundle tools/wip/out
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

### `apply_ascc2_overlay`

Apply the ASCC2 overlay bundle to an ASCC1 baseline import.

```sh
./woco apply_ascc2_overlay \
  --base-dir tools/wip/cache/ascc1 \
  --overlay-dir tools/wip/cache/ascc2_overlay_bundle \
  --overlay-map tools/wip/out/VA_ASCC2_overlay_map.csv \
  --v1-image-refs tools/wip/in/v1_VA_image_refs.csv \
  --region-abbrev VA \
  --ascc1-code ASCC1 \
  --ascc2-code ASCC2 \
  --audit-user-id 1 \
  --skip-missing-images \
  --dry-run
```

Run `--dry-run` before a real overlay. Expected exit code: `0`.

### `wipe_user_data`

Clear user-generated submissions, versions, and recycle bins while preserving
catalog tables, auth users, groups, collections, and collection assignments.

```sh
./woco wipe_user_data --dry-run
./woco wipe_user_data --no-input
```

Use this only when you intentionally need to clear submission, version, and
recycle-bin data before a separate destructive catalog refresh.

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
