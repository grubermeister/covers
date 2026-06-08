# Tools reference

Everything in the repo that isn't the Django app itself: host scripts, the systemd unit, and the Django management commands.

All Django command examples use `woco <cmd>` -- the project's CLI shim (a bash wrapper around `uv run woco`, which calls `woco_cli.py` -> Django's `execute_from_command_line`). Please do not use `django-admin` or `python manage.py` directly.

---

## Overview

| Tool | When to use | Location |
|------|-------------|----------|
| `deploy.sh` | Deploy a new build to the staging server | `tools/deploy.sh` |
| `worldcovers.service` | Systemd unit file for gunicorn | `tools/worldcovers.service` |
| `rebuild_staging_db.sh` | Wipe and recreate the staging DB | `tools/rebuild_staging_db.sh` |
| `setup_worldcovers_db.sql` | Create the DB schema from scratch | `tools/setup_worldcovers_db.sql` |
| `apmc_data_explorer.ipynb` | Browse raw APMC catalog data | `tools/apmc_data_explorer.ipynb` |
| `apmc_data_munger.ipynb` | Munge APMC data into import-ready CSVs | `tools/apmc_data_munger.ipynb` |
| `apmc_data_transforms.ipynb` | Apply transforms to APMC data | `tools/apmc_data_transforms.ipynb` |
| `import_v2_data` | Primary v2 catalog import (postmarks, covers, marks) | `woco import_v2_data` |
| `import_v2_data_versioned` | Same as above, wrapped in a django-reversion revision | `woco import_v2_data_versioned` |
| `list_v2_import_versions` | List all versioned v2 imports | `woco list_v2_import_versions` |
| `revert_v2_import_version` | Revert a versioned v2 import | `woco revert_v2_import_version` |
| `import_catalog_images` | Import postmark images from a CSV mapping | `woco import_catalog_images` |
| DateFormat admin import | Import date formats via Django admin | `/admin/postmarks/dateformat/import/` |
| `backup_auth` | Export user accounts, groups, and email addresses | `woco backup_auth` |
| `restore_auth` | Restore user accounts from a backup | `woco restore_auth` |
| `set_user_password` | Set a user's password without the shell | `woco set_user_password` |
| `backfill_listing_rate_values` | Backfill missing rate values on listing records | `woco backfill_listing_rate_values` |
| `backfill_listing_states` | Backfill missing state assignments on listing records | `woco backfill_listing_states` |
| `check_listing_admin` | Diagnostic: verify admin listing configuration | `woco check_listing_admin` |

---

## Host tools (`tools/`)

### `tools/deploy.sh`

**Purpose:** Deploy a new build to the staging server. Installs Python deps, runs migrations, builds the frontend, collects static files, then restarts the `worldcovers` systemd service.

**Who runs it:** The GitHub Actions CI workflow runs it over SSH on every push to `staging`. Humans can run it manually after `git pull`.

**Prerequisites:**
- `uv` installed on the host (Python toolchain manager; auto-installs the pinned Python version from `.python-version`)
- Node.js + npm installed on the host (for the frontend build)
- `mysql.cnf` present at the repo root on the host (see [BUILD.md](BUILD.md))
- `wocod` deploy user has the sudoers entries described in [RUNBOOK.md](RUNBOOK.md)

**Invocation (from repo root):**
```sh
tools/deploy.sh
```

**Side effects:** Migrations run, `frontend/dist/` is rebuilt, static files are collected, the `worldcovers` service is restarted. This is destructive to any running request that hasn't completed.

---

### `tools/worldcovers.service`

**Purpose:** The canonical systemd unit file for the gunicorn process that serves the Django app on the staging host.

**Install (one-time, on a fresh host):**
```sh
sudo install -m 644 tools/worldcovers.service /etc/systemd/system/worldcovers.service
sudo systemctl daemon-reload
sudo systemctl enable --now worldcovers
```

**Day-to-day commands:**
```sh
sudo systemctl restart worldcovers
sudo systemctl status worldcovers
sudo journalctl -u worldcovers -f
```

The unit file sets `PYTHONPATH`, `DJANGO_SETTINGS_MODULE`, and `DB_NAME`. It reads DB credentials from `/srv/woco/mysql.cnf` via Django's `read_default_file`. `DEBUG`, `SECRET_KEY`, and `ALLOWED_HOSTS` come from `/srv/woco/backend/.env`.

`tools/deploy.sh` installs an updated unit file automatically if it differs from the one on disk.

See [RUNBOOK.md](RUNBOOK.md) for the required sudoers entries and full host-bootstrap instructions.

---

### `tools/rebuild_staging_db.sh` and `tools/setup_worldcovers_db.sql`

**Purpose:** `rebuild_staging_db.sh` drops and recreates the staging database. `setup_worldcovers_db.sql` is the SQL it uses to create the schema from scratch.

**When to run:** Only when you need to reset the staging database to a clean state. This is destructive -- all data is lost.

**Prerequisites:** `mysql.cnf` must be present at the repo root (same file Django reads; see [BUILD.md](BUILD.md)).

**Invocation:**
```sh
tools/rebuild_staging_db.sh
```

After running, apply migrations to set up the schema: `woco migrate`. (`rebuild_staging_db.sh` already runs migrations as part of the rebuild; this is a manual fallback.)

---

### `tools/apmc_data_explorer.ipynb`, `tools/apmc_data_munger.ipynb`, `tools/apmc_data_transforms.ipynb`

Jupyter notebooks for working with APMC (American Philatelic Merchandise Catalog) source data.

| Notebook | Purpose |
|----------|---------|
| `apmc_data_explorer.ipynb` | Browse and inspect raw APMC catalog data |
| `apmc_data_munger.ipynb` | Munge raw APMC data into import-ready CSVs |
| `apmc_data_transforms.ipynb` | Apply transforms and produce the final CSV set for `import_v2_data` |

Open with Jupyter: `jupyter notebook tools/apmc_data_munger.ipynb`. These notebooks produce the CSV exports that land in `tools/wip/out/` and get passed to `import_v2_data --dir tools/wip/out`.

---

### `tools/wip/`

Scratch area -- contents are not stable. `tools/wip/in/` holds raw input data; `tools/wip/out/` holds the CSV exports consumed by `import_v2_data`. Do not commit production data here.

---

## Management commands (`backend/common/management/commands/`)

### `import_v2_data` -- primary v2 catalog import

The primary command for importing v2 catalog data. Works on a fresh database with no prior legacy import required. Idempotent: re-running updates existing records.

**Invocation:**
```sh
woco import_v2_data --dir tools/wip/out
```

**Flags:**

| Flag | Default | Effect |
|------|---------|--------|
| `--dir` / `-d` | `tools/wip/out` | Directory containing the required CSV exports |
| `--user` / `-u` | first superuser | Username for `created_by` / `modified_by` |
| `--missing-postmark-strategy` | `create` | What to do when a `postmarks.csv` row has no matching `Postmark`: `create` (stub), `skip`, or `error` |

**Required CSVs** (all must be present in `--dir`):

`colors.csv`, `shapes.csv`, `letterings.csv`, `framings.csv`, `post_offices.csv`, `covers.csv`, `ratemarks.csv`, `auxmarks.csv`, `postmarks.csv`, `date_observed.csv`, `postmark_ratemark.csv`, `cover_postmark.csv`, `mark_framing.csv`, `postmark_valuation.csv`

These are produced by the APMC notebooks in `tools/`.

**Import order:** lookups (colors, shapes, etc.) -> Covers -> Ratemarks -> Postmarks -> Auxmarks -> DateObserved -> junction tables (CoverPostmark, PostmarkRatemark, MarkFraming) -> PostmarkValuation.

**Known limitation:** `post_offices.csv` has a blank `region_id` column -- all PostOffice rows are assigned to a placeholder `Region("UNKNOWN")`. `postmark_valuation.csv` has empty `appraisal_date` so valuations are skipped.

**Recovery:** The command is idempotent -- re-run after fixing the CSV to update records. Use `--missing-postmark-strategy=error` to abort on the first missing Postmark instead of creating stubs.

---

### `import_v2_data_versioned`, `list_v2_import_versions`, `revert_v2_import_version`

Wrap `import_v2_data` inside a django-reversion revision so you can tag, list, and revert imports.

```sh
woco import_v2_data_versioned --dir tools/wip/out --tag my-import-label
woco list_v2_import_versions
woco revert_v2_import_version --tag my-import-label
```

Accepts the same `--dir` and `--user` flags as `import_v2_data`. `--tag` is a human-readable label for the revision. Use this instead of `import_v2_data` when you want to be able to roll back.

---

### `import_catalog_images` -- postmark image import

Import catalog-extracted images as `Image` records (`subject_type='MARKING'`).

**Prerequisites:** Image files must already be on disk under `MEDIA_ROOT` (typically `MEDIA_ROOT/<state>/<filename>`). The command reads the CSV for the mapping but does not copy files.

**Invocation:**
```sh
woco import_catalog_images                              # auto-discover *_image_mapping.csv in MEDIA_ROOT
woco import_catalog_images backend/media/               # all *.csv in a directory
woco import_catalog_images backend/media/VA*.csv        # shell-expanded file list
woco import_catalog_images path/to/one.csv --dry-run    # single file, no writes
```

**CSV columns:**

| Column | Required | Notes |
|--------|----------|-------|
| `storage_filename` | Yes | Path relative to `MEDIA_ROOT` (e.g. `iowa/IA-ABC-123-1.jpg`) |
| `display_order` | No | Integer, default `0` |
| `image_view` | No | `FULL` \| `DETAIL` \| `COMPARISON`, default `FULL` |
| `image_description` | No | Free text |

**Flags:**

| Flag | Default | Effect |
|------|---------|--------|
| `paths` (positional) | MEDIA_ROOT/`*_image_mapping.csv` | CSV files and/or directories; directories are scanned for `*.csv` |
| `-r`, `--recursive` | off | Recurse into subdirectories when a directory is given |
| `--user` | first superuser | Username for `uploaded_by` / `created_by` / `modified_by` |
| `--dry-run` | off | Parse and validate without writing; rolls back the transaction |
| `--truncate` | off | Delete all existing marking `Image` rows before importing |
| `--clean` | off | Rewrite each source CSV, dropping rows whose file is missing on disk |

**Side effects:** `Image.objects.update_or_create` -- creates on first run, updates on subsequent runs. Rows with unreadable files or invalid `image_view` are skipped with a log message.

Shared image utilities live at [backend/common/images.py](../backend/common/images.py).

---

### DateFormat admin import

Import date formats through the Django admin UI -- useful for re-importing or updating the `DateFormat` table without a management command.

**URL:** `/admin/postmarks/dateformat/import/`

**Required CSV columns:**

| Column | Notes |
|--------|-------|
| `date_format_id` | Primary key (from legacy `nTownmarkDateFormatID`) |
| `format_name` | From legacy `txtTownmarkDateFormat` |
| `format_description` | Optional; from legacy `memTownmarkDateFormat` |
| `created_by` | **User ID** (integer, required -- e.g. `1`) |
| `modified_by` | **User ID** (integer, required -- e.g. `1`) |

**Column mapping from the legacy export:**

| Legacy column | Import column |
|---------------|---------------|
| `nTownmarkDateFormatID` | `date_format_id` |
| `txtTownmarkDateFormat` | `format_name` |
| `memTownmarkDateFormat` | `format_description` |
| (none) | `created_by` (add manually) |
| (none) | `modified_by` (add manually) |

Drop `nOrder`, `ynActive`, `ynDeleted`, `dtEntered`, `dtUpdated` -- the importer ignores them.

**Quick conversion script** (generates an import-ready CSV from the legacy file):
```sh
woco convert_dateformat_csv \
  "frontend/public/Old Data/tblTownmarkDateFormat.csv" \
  --output dateformat_import.csv \
  --user-id 1
```

Then upload `dateformat_import.csv` at `/admin/postmarks/dateformat/import/`.

---

### `backup_auth` and `restore_auth` -- user account backup

Export and restore user accounts, groups, email addresses, state collections, and editor collection assignments using django-import-export resources.

```sh
woco backup_auth users.csv groups.csv emails.csv collections.csv assignments.csv
woco restore_auth users.csv groups.csv emails.csv collections.csv assignments.csv
```

Run `backup_auth` before destructive DB operations. `restore_auth` is idempotent and assignment restore is an exact mirror when `assignments.csv` is provided.

---

### `set_user_password` -- set a password without the shell

```sh
woco set_user_password <username> <new_password>
```

Sets a user's password directly. Useful when you can't reach the admin UI.

---

### `backfill_listing_rate_values` and `backfill_listing_states`

One-time backfill commands for filling in missing data on existing listing records after a schema change or data import gap. Safe to re-run (idempotent).

```sh
woco backfill_listing_rate_values
woco backfill_listing_states
```

---

### `check_listing_admin` -- diagnostic

Verifies that the Django admin listing configuration is consistent. Prints any mismatches to stdout. No writes.

```sh
woco check_listing_admin
```
