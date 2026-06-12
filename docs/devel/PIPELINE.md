# ASCC Catalog Pipeline

How to turn a scanned ASCC catalog PDF into the Django-shape CSV bundle
that `import_ascc_bundle` loads into the catalog tables.

The four `tools/ascc_*` utilities run in a fixed order, with a human
review gate between each. Output of one stage is reviewed and then fed to
the next.

```
  tools/wip/in/<BASE>.pdf
        |  (1) ascc_page_processor.py        -> tools/wip/out/<BASE>/  (chunk PNGs)
        |  [review chunks, move out -> in]
        v
  tools/wip/in/<BASE>/page-*.png
        |  (3) ascc_page_extract.py          -> tools/wip/out/<BASE>.csv
        |  [review/correct CSV]
        v
  tools/wip/out/<BASE>.csv
        |  (5) ascc_image_extract.py  OPT    -> tools/wip/out/<BASE>_images/
        |  [move reviewed CSV out -> in]
        v
  tools/wip/in/<BASE>.csv (+ reference_works.csv, regions.csv)
        |  (7) ascc_data_munger.py           -> tools/wip/out/*.csv  (11-file bundle)
        v
  tools/wip/out/
        |  (8) ./woco import_ascc_bundle     -> catalog DB tables
        v
  ASCC1 database baseline
```

## Conventions

- `<BASE>` is the PDF stem, e.g. `VA_ASCC_CTLG`. Its first two letters are
  the region abbrev the munger keys on (`VA` is matched against
  `regions.csv`).
- Run the four `ascc_*` tools from repo root. Their default paths resolve
  under `tools/wip/` based on each script's location, not on the shell cwd.
  Invoke with `uv run python tools/<script> <BASE>`.
- The `tools/wip/` working dir splits into `in/` (curated inputs) and `out/`
  (generated artifacts). The namespace deliberately flips between stages:
  a tool writes to `tools/wip/out/...`, you review it, then you move it back to
  `tools/wip/in/...` to feed the next tool. Those moves are the review gates.
- `tools/wip/cache/` holds regenerable intermediates (rendered pages, vision
  responses). Safe to delete; pass `--force` to invalidate selectively.
- API-dependent steps default to OpenRouter. To use Anthropic directly, set
  `ANTHROPIC_API_KEY` and pass `--provider anthropic`, or set
  `PIPELINE_LLM_PROVIDER=anthropic`. `PIPELINE_LLM_MODEL` can override the
  provider-specific default model. OpenRouter uses `OPENROUTER_API_KEY`.

## Layering strategy

Load the Fifth Edition ASCC catalog first. It is the printed baseline that
state editors can verify against scanned catalog pages. The later v1
worldcovers.org export remains reference evidence only: it mixes Fifth Edition
text, unpublished Sixth Edition draft text, old parser output, later manual
edits, and independent `tblTownmarkImages` associations.

No automated v1 overlay is performed. Use v1-derived artifacts for manual
comparison and review only. The detailed provenance rules live in
[ascc-data-layering-strategy.md](ascc-data-layering-strategy.md).

## Steps

### 1. Render and chunk the PDF -- ascc_page_processor.py

Renders pages, splits two-column pages into halves, and slices each
column into per-listing chunk PNGs. Three stages: render, halves, chunks.

- in:  `tools/wip/in/<BASE>.pdf`
- out: `tools/wip/out/<BASE>/page-NNNN-MMMM.png` (chunk PNGs)
- cache: `tools/wip/cache/<BASE>_full/`, `_halves/`, `_blocks.json`, `_review.json`

```
uv run python tools/ascc_page_processor.py <BASE>
uv run python tools/ascc_page_processor.py <BASE> --stages render,halves,chunks
uv run python tools/ascc_page_processor.py <BASE> --pages 419-425
uv run python tools/ascc_page_processor.py <BASE> --force halves,chunks
uv run python tools/ascc_page_processor.py <BASE> --provider anthropic
```

Provider selection examples:

```sh
# Default: OpenRouter with OPENROUTER_API_KEY.
uv run python tools/ascc_page_processor.py <BASE>

# Direct Anthropic Claude API with ANTHROPIC_API_KEY.
uv run python tools/ascc_page_processor.py <BASE> --provider anthropic

# Env-driven provider and optional model override.
PIPELINE_LLM_PROVIDER=anthropic PIPELINE_LLM_MODEL=claude-sonnet-4-6 \
  uv run python tools/ascc_page_processor.py <BASE>
```

### 2. Review gate: chunks

Eyeball the chunk PNGs and fix any mis-slices, then move the directory
from `tools/wip/out/<BASE>/` to `tools/wip/in/<BASE>/`. The next two tools
read chunks from `tools/wip/in/<BASE>/`.

### 3. Extract listing text -- ascc_page_extract.py

Sends each chunk to a Claude vision model and writes one CSV row per
detected entry.

- in:  `tools/wip/in/<BASE>/page-*.png` + `tools/wip/in/regions.csv`
- out: `tools/wip/out/<BASE>.csv` (columns: Listing, Page, Images Above, Type)
- cache: `tools/wip/cache/<BASE>_extract.json`

```
uv run python tools/ascc_page_extract.py <BASE>
uv run python tools/ascc_page_extract.py <BASE> --pages 419-420
uv run python tools/ascc_page_extract.py <BASE> --force
uv run python tools/ascc_page_extract.py <BASE> -v
uv run python tools/ascc_page_extract.py <BASE> --provider anthropic
```

Provider selection works the same as the page processor:

```sh
# Default: OpenRouter with OPENROUTER_API_KEY.
uv run python tools/ascc_page_extract.py <BASE>

# Direct Anthropic Claude API with ANTHROPIC_API_KEY.
uv run python tools/ascc_page_extract.py <BASE> --provider anthropic

# Env-driven provider and optional model override.
PIPELINE_LLM_PROVIDER=anthropic PIPELINE_LLM_MODEL=claude-sonnet-4-6 \
  uv run python tools/ascc_page_extract.py <BASE>
```

### 4. Review gate: CSV

Proofread `tools/wip/out/<BASE>.csv` against the catalog. The "Images Above"
counts drive step 5; the listing text drives step 7. Leave the corrected
file in `tools/wip/out/<BASE>.csv` for now (step 5 reads it there).

### 5. Extract marking images -- ascc_image_extract.py (OPTIONAL)

Only when you want the marking illustrations pulled out as PNGs.
Deterministic and offline (no API calls). Uses the "Images Above" counts
from the reviewed CSV.

- in:  `tools/wip/in/<BASE>/page-*.png` + `tools/wip/out/<BASE>.csv`
- out: `tools/wip/out/<BASE>_images/<state>-<page>-<chunk>-<n>.png`,
       `tools/wip/out/<BASE>_subchunks/`,
       `tools/wip/out/<BASE>_subchunks_report.csv` (per-chunk status)

```
uv run python tools/ascc_image_extract.py <BASE>
uv run python tools/ascc_image_extract.py <BASE> --pages 419-425
uv run python tools/ascc_image_extract.py <BASE> -v
```

Check `<BASE>_subchunks_report.csv` for mismatches (a chunk whose
detected image count does not match the CSV).

### 6. Review gate: move CSV to in/

Move the reviewed CSV from `tools/wip/out/<BASE>.csv` to
`tools/wip/in/<BASE>.csv`, so it sits beside
`tools/wip/in/reference_works.csv` and `tools/wip/in/regions.csv`. The
munger derives its input dir from the CSV path and reads both reference
files from there.

### 7. Build the import bundle -- ascc_data_munger.py

Parses the reviewed listings and emits the Django-shape CSV bundle.

- in:  `tools/wip/in/<BASE>.csv` + `tools/wip/in/reference_works.csv`
       + `tools/wip/in/regions.csv` + marking images from step 5
- out: 11 CSVs to `tools/wip/out/` (see "Bundle contents" below)

```
uv run python tools/ascc_data_munger.py --input tools/wip/in/<BASE>.csv --out-dir tools/wip/out/ --reference-work-code ASCC1
```

`--input` defaults to `tools/wip/in/VA_ASCC_CTLG.csv`. `--input-dir`
overrides where the reference CSVs are read from (defaults to the input
CSV's dir). `--reference-work-code` selects the source row from
`reference_works.csv`; the default is `ASCC1`.
On success the munger prints the exact load command to run next.

### 8. Load the bundle -- import_ascc_bundle

Django management command at
`backend/common/management/commands/import_ascc_bundle.py`. Loads every
CSV in the directory in dependency order via the import-export Resource
classes. Side effect: auto-creates a Collection for any Region that lacks
one.

Run from repo root with `./woco`:

```
./woco import_ascc_bundle tools/wip/out/ --dry-run
./woco import_ascc_bundle tools/wip/out/
```

Always do a `--dry-run` first (parses and validates every CSV, then rolls
back). Useful flags:

- `--dry-run`     validate only; commit nothing
- `--truncate`    wipe all 14 catalog tables first (incompatible with --only)
- `--only a,b`    load just these stems (order still forced)
- `--allow-missing`  skip stems whose CSV is absent

### 9. Convert v1 raw data to v2 CSV shape (reference utility)

Convert the v1 `tblRawStateData` export into the same seven-column CSV shape
that step 3 produces. These outputs currently have no downstream consumer in
the automated pipeline. They are kept for manual comparison of v1 evidence
against the ASCC1 baseline.

- in: `tools/wip/in/v1_<REGION>_data.csv`
- in: optional `tools/wip/in/tblTownmarkImages.csv`
- out: `tools/wip/in/v1_<REGION>_ocr.csv`
- out: `tools/wip/in/v1_<REGION>_image_refs.csv`

Run from repo root. Expected exit code: `0`.

```sh
uv run python tools/v1_to_v2_catalog_format.py \
  tools/wip/in/v1_VA_data.csv \
  --images tools/wip/in/tblTownmarkImages.csv \
  --out tools/wip/in/v1_VA_ocr.csv \
  --image-refs-out tools/wip/in/v1_VA_image_refs.csv \
  --region-abbrev VA
```

The v2-shaped output has this exact header:

```csv
Listing,Page,Chunk,Images Above,Type,Manuscript,Default Shape
```

`Chunk` is the v1 `nRawStateDataID`. `Images Above` counts non-deleted
`tblTownmarkImages` rows for that raw row. `Default Shape` is blank because v1
does not provide a clean equivalent.

## Bundle contents

The munger writes 11 import CSVs. Import load order (parents first):

```
colors            generated   leaf lookup
letterings        generated   leaf lookup
shapes            generated   leaf lookup
regions           passthrough  copied from tools/wip/in/regions.csv
reference_works   passthrough  copied from tools/wip/in/reference_works.csv
post_offices      generated
post_office_regions generated junction (post_office + region)
markings          generated   main table (shape, lettering, color, post_office)
dates_seen        generated   polymorphic (anchored to markings)
citations         generated   reference_work + marking
images            generated   marking tracings (from step 5)
```

The three legacy cover stems (`covers`, `cover_markings`,
`cover_valuations`) are optional and no longer emitted by the munger;
`import_ascc_bundle` loads cleanly without them. Covers are authored by
hand after the bundle is imported.
