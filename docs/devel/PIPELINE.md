# ASCC Catalog Pipeline

This runbook documents the public ASCC workflow exposed through `./woco ascc`.
Run commands from the repo root (`/Users/mpc/Developer/worldcovers` in a local
checkout, `/srv/woco` on staging). Unless noted otherwise, the expected
successful exit code is `0`.

The canonical path uses the legacy v1 export as source data, builds a v2 import
bundle, applies supported v1 split-column evidence into that bundle, and
optionally imports it. The scanned-PDF OCR pipeline is independent and is
explicitly named `./woco ascc ocr`.

## Command Summary

```sh
./woco ascc doctor VA
./woco ascc munge VA
./woco ascc run VA --dry-run
./woco ascc run VA
./woco ascc import tools/wip/out/v1_va --dry-run
./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf
./woco ascc clean VA
```

Use `doctor` before a run to catch missing inputs. Use `munge` when you only
want the CSV bundle. Use `run` when you want the same bundle build followed by
an import. Use `--dry-run` on `run` to validate through the importer and roll
back.

## Codebase Overview

`./woco` is the only normal operator entrypoint. It dispatches `./woco ascc`
to `tools/ascc_cli.py`, which owns public command names, argument parsing, and
user-facing help.

`tools/ascc_pipeline/` owns shared orchestration policy:

- `paths.py`: state normalization, bundle/cache/media paths, import stems, and
  the defaults `ASCC6` for v1 and `ASCC5` for OCR.
- `commands.py`: subprocess and import command construction.
- `checks.py`: doctor check records and reference-work validation.
- `manifest.py`: CSV counting helpers used by run manifests.

Stage scripts remain implementation units. The v1 stage scripts build
v1-derived bundles and warnings. The OCR stage scripts process scanned PDFs
into catalog rows and then bundles. Direct script use is for debugging and must
pass explicit inputs; it must not rely on hidden VA, ASCC1, ASCC2, or flat
`tools/wip/out` defaults.

Bundle import stays in Django through `import_ascc_bundle`. Deployment scripts
move files and then import one explicit bundle directory; they do not guess
`tools/wip/out`.

## Canonical V1 Flow

Use this path when the source of truth is the legacy v1 export. It reads
`tblRawStateData.txtRawStateData`, creates synthetic catalog rows, runs the
munger, attaches v1 images, and applies supported v1 split-column fields before
import.

By default, `munge` and `run` cite `ASCC6`, the sixth edition. Pass
`--reference-work CODE` only when a different citation is intended.

Required inputs in `tools/wip/in/`:

```text
tblStates.csv
tblRawStateData.csv
tblTownmarkImages.csv
regions.csv
reference_works.csv
```

Optional image roots:

- `--v1-image-root <dir>`: explicit directory containing files named by
  `tblTownmarkImages.txtFilename`.
- `V1_IMAGE_ROOT`: environment fallback.
- `backups/images/<state-name>/`: used by default when present.
- `tools/wip/in/v1_images`: final fallback.

If legacy image files are not available, pass `--allow-missing-v1-images`.
Missing images are written to the image report instead of aborting.

### 1. Check Inputs

```sh
./woco ascc doctor VA
```

`doctor` checks the v1 seed files, `tools/wip` directories, the selected image
root, and database availability. The database check is informational for
`doctor`; it is not required to build a bundle.

### 2. Build The Bundle

```sh
./woco ascc munge VA
```

For state `VA`, `munge` writes:

```text
tools/wip/cache/v1/VA/catalog_rows.csv
tools/wip/cache/v1/VA/image_refs.csv
tools/wip/cache/v1/VA/slice.csv
tools/wip/cache/v1/VA/run.json
tools/wip/out/v1_va/
tools/wip/out/v1_va/source_marking_map.csv
tools/wip/out/v1_va/v1_pipeline_warnings.csv
```

The munger receives `--region-abbrev VA` explicitly. That matters because v1
catalog rows are stored under the generic filename `catalog_rows.csv`; without
the explicit region, the munger would derive `CA` from the filename.

The v1 overlay records unsupported split-column evidence in
`v1_pipeline_warnings.csv` instead of importing unsupported values silently.

### 3. Validate Or Import

Dry-run the generated bundle:

```sh
./woco ascc run VA --dry-run
```

Build and commit the generated bundle:

```sh
./woco ascc run VA
```

`run` always performs the v1 bundle build first, then calls the importer on
`tools/wip/out/v1_<state>`. Import flags are passed through:

```sh
./woco ascc run VA --dry-run --allow-missing
./woco ascc run VA --truncate
./woco ascc run VA --only markings,images
./woco ascc run VA --skip-report tools/wip/out/v1_va/skips.csv
```

Use `--truncate` only when the target catalog tables should be replaced.

To import an already-built bundle without re-running the v1 build:

```sh
./woco ascc import tools/wip/out/v1_va --dry-run
./woco ascc import tools/wip/out/v1_va
```

## Scanned PDF OCR Flow

Use this path only when the source is a scanned ASCC state PDF rather than the
legacy v1 export.

Required inputs in `tools/wip/in/`:

```text
<STATE>.pdf
regions.csv
reference_works.csv
tblStates.csv
tblRawStateData.csv
tblTownmarkImages.csv
```

Required tools and environment:

- `pdftoppm` must be on `PATH`.
- Set `OPENROUTER_API_KEY` for the default OpenRouter provider.
- Or set `ANTHROPIC_API_KEY` and pass `--provider anthropic`.
- Optional: set `PIPELINE_LLM_PROVIDER` and `PIPELINE_LLM_MODEL`.

Run the OCR wrapper:

```sh
OPENROUTER_API_KEY=<key> ./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf
ANTHROPIC_API_KEY=<key> ./woco ascc ocr VA --pdf ~/Downloads/va-catalog.pdf --provider anthropic
```

For state `VA`, `ocr` writes:

```text
tools/wip/in/VA.pdf
tools/wip/cache/VA_ocr_rows.csv
tools/wip/cache/VA_catalog_rows.csv
tools/wip/cache/VA_images/
tools/wip/cache/VA_run.json
tools/wip/out/va/
backend/media/va/
```

`ocr` resumes by default:

- If `tools/wip/cache/VA_catalog_rows.csv` exists, it skips OCR and image-count
  verification and resumes at munger.
- If only `tools/wip/cache/VA_ocr_rows.csv` exists, it skips page processing
  and OCR extraction and resumes at image-count verification.
- Pass `--force` to rebuild OCR rows and catalog rows from the PDF.

Other `ocr` flags:

- `--pages <range>`: process only the given page range of the PDF.
- `--model <model>`: override the provider's default vision model.
- `--import-check auto|always|never`: control the post-build dry-run
  import validation (default: `auto`).

## OCR Internals

Most users should use `./woco ascc ocr`. The lower-level steps below are useful
for debugging the scanned-PDF path.

The OCR basename is the input PDF stem, for example `VA_ASCC_CTLG`. It must
start with the two-letter region abbreviation.

```sh
uv run python tools/ascc_page_processor.py VA_ASCC_CTLG --stages render
uv run python tools/ascc_page_processor.py VA_ASCC_CTLG --stages halves
uv run python tools/ascc_page_processor.py VA_ASCC_CTLG --stages chunks
uv run python tools/ascc_page_extract.py VA_ASCC_CTLG
uv run python tools/ascc_image_extract.py VA_ASCC_CTLG \
  --ocr-rows tools/wip/cache/VA_ocr_rows.csv \
  --strict \
  --catalog-rows-out tools/wip/cache/VA_catalog_rows.csv
uv run python tools/ascc_data_munger.py \
  --input tools/wip/cache/VA_catalog_rows.csv \
  --input-dir tools/wip/in \
  --out-dir tools/wip/out/va \
  --reference-work-code ASCC5 \
  --region-abbrev VA
./woco ascc import tools/wip/out/va --dry-run
```

Lower-level OCR scripts do not provide the same orchestration as `ocr`. If you
run them manually, make sure marking images are available in
`backend/media/<state>/` before importing.

## Cleaning Generated Files

Clean one state's generated files:

```sh
./woco ascc clean VA
```

Clean generated cache and output files for all states:

```sh
./woco ascc clean
```

The clean commands preserve source files under `tools/wip/in/`.

## Import Notes

The ASCC importer loads bundle CSVs in dependency order:

```text
colors
letterings
shapes
regions
reference_works
post_offices
post_office_regions
markings
covers
cover_valuations
dates_seen
cover_markings
citations
images
```

`covers`, `cover_valuations`, and `cover_markings` may be absent from current
bundles. The importer skips those optional stems without `--allow-missing`.

Useful importer flags:

```sh
./woco ascc import tools/wip/out/v1_va --dry-run
./woco ascc import tools/wip/out/v1_va --truncate
./woco ascc import tools/wip/out/v1_va --only markings,images
./woco ascc import tools/wip/out/v1_va --allow-missing
```

`--dry-run` validates and rolls back. `--truncate` deletes catalog rows before
loading and should be used only against a database that should be replaced.

## Verification

After a committed import, start the dev stack:

```sh
./woco dev
```

Then verify:

- Search for a marking from the imported state.
- Confirm the listing renders.
- Confirm marking images render.
- Check `/api/v2/markings/?page=1` returns the expected count.

For direct media/API checks, use state-specific examples:

```sh
curl -s http://localhost:8000/api/v2/markings/1/ | python3 -m json.tool
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' \
  http://localhost:8000/media/va/va-419-2-1.png
```

## Known Caveats

- The ASCC CLI loads the repo-root `.env` (not `backend/.env`); see
  [BUILD.md](BUILD.md#environment-files) for the full env-file layout.
- Free-tier OpenRouter keys without credit fail on paid vision models.
- The OCR path requires `pdftoppm`; the v1 path does not.
- `reference_works.csv` must contain the selected `--reference-work` code.
- The v1 wrapper defaults to `ASCC6`; the OCR wrapper defaults to `ASCC5`.
- Non-numeric valuations still need a supported catalog representation before
  they can import as valuations.
