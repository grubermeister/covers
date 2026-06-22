# ASCC Catalog Pipeline -- Runbook

Turn a state sub-catalog PDF into rows in the WorldCovers database. This is the
canonical, reproducible path; it supersedes the notebook chain described in
`docs/devel/TOOLS.md` (those notebooks no longer exist -- the pipeline is now the
`tools/ascc_*.py` scripts below).

**Validated end-to-end against Virginia (`VA_ASCC_CTLG`), 2026-06-09.**

For normal use and live demos, use the state-centered wrapper:

```bash
./woco ascc doctor VA
./woco ascc run VA --pdf ~/Downloads/va-catalog.pdf
```

For state `VA`, the wrapper writes `tools/wip/in/VA.pdf`,
`tools/wip/cache/VA_ocr_rows.csv`, `tools/wip/cache/VA_catalog_rows.csv`,
`tools/wip/out/va/`, `tools/wip/cache/compare/VA/review_ledger_VA.csv`, and
`tools/wip/cache/VA_run.json`.

---

## 0. Prerequisites

| Need | How |
|------|-----|
| Python env | `uv sync` in repo root (set `CC=gcc CXX=g++` if `mysqlclient` fails to build) |
| MySQL DB | `worldcovers` DB migrated; see `tools/rebuild_staging_db.sh`. Work on `staging` branch; see `DECISIONS.md` for the `main` migration bug. |
| `pdftoppm` | poppler-utils. `sudo apt-get install -y poppler-utils`, or no-sudo `brew install poppler` |
| `OPENROUTER_API_KEY` | in **`worldcovers/.env`** (the scripts load `TOOLS_DIR.parent/.env`, i.e. the repo-root `.env`, NOT the workspace-root one). Account must have credit -- the vision stages call the paid model `anthropic/claude-sonnet-4.6` |

### The `BASE` convention

Every script keys off one basename, e.g. `VA_ASCC_CTLG`. It must:
- be the input PDF's filename stem: `tools/wip/in/<BASE>.pdf`
- start with the 2-letter region abbrev (`VA`) -- the munger derives `REGION_ABBREV`
  from `os.path.basename(input)[:2]`, and the processor/extract derive the state
  header (`VA` -> `VIRGINIA`) from the first `_`-delimited token via `regions.csv`.

> All of `tools/wip/{in,out,cache}/` is gitignored (incl. all pdf/png/jpg) -- safe scratch.

### Seed files (place in `tools/wip/in/` before starting)

- `<BASE>.pdf` -- the state sub-catalog
- `reference_works.csv` -- **exactly 1 data row** (the catalog being cited); munger raises otherwise
- `regions.csv` -- must contain exactly 1 row matching `REGION_ABBREV`

---

## Stage map

| # | Script | API? | Reads | Writes |
|---|--------|------|-------|--------|
| A | `ascc_page_processor.py ... --stages render` | no | `wip/in/<BASE>.pdf` | `wip/cache/<BASE>_full/page-*.png` |
| B | `ascc_page_processor.py ... --stages halves` | yes | full pages | `wip/cache/<BASE>_halves/` |
| C | `ascc_page_processor.py ... --stages chunks` | yes | halves | `wip/cache/<BASE>_chunks/page-NNNN-MMMM.png` |
| D | `ascc_page_extract.py <BASE>` | yes | `wip/cache/<BASE>_chunks/` chunks | raw OCR catalog rows |
| E | `ascc_image_extract.py <BASE>` | no | raw OCR rows + chunks | marking images + verified catalog rows |
| F | `ascc_data_munger.py --input ... --input-dir ...` | no | verified catalog rows + seeds + images | Django-shape CSVs in `wip/out/` |
| G | `manage.py import_ascc_bundle <out-dir>` | no | the bundle | rows in MySQL |

All commands run from the repo root (`worldcovers/`).

---

## A. Render  (deterministic)

```bash
uv run python tools/ascc_page_processor.py VA_ASCC_CTLG --stages render
```

Result (VA): **17 pages** rendered at 300 DPI -> `wip/cache/VA_ASCC_CTLG_full/`.

## B. Halves  (vision)

```bash
uv run python tools/ascc_page_processor.py VA_ASCC_CTLG --stages halves
```

Per page: deterministic vertical-rule detection, with a vision page-number read
and a single-column-confirm fallback. Crops to L/R halves named by **catalog**
page number.

Result (VA): catalog pages **419-435**, **34 half-images**, 20 vision calls
(4 `vision_confirmed_weak_rule`). **Inspect a sampling of halves for seam-clipped
text/markings before proceeding** (the script reminds you).

## C. Chunks  (vision)

```bash
uv run python tools/ascc_page_processor.py VA_ASCC_CTLG --stages chunks
```

Per half: deterministic dark/blank row-block detection + vision per-block
classify (illustration vs text); cut at the top of every illustration block.

Result (VA before the page 419 Norfolk split fix): 795 blocks detected,
173 illustrations, **201 chunk PNGs** (~34 min; 795 classify + 197 review
vision calls). The current chunk handoff directory is
`wip/cache/VA_ASCC_CTLG_chunks/page-NNNN-MMMM.png`; no manual chunk move is
needed.

## D. Extract  (vision)

```bash
uv run python tools/ascc_page_extract.py VA_ASCC_CTLG
```

Sends each chunk to the vision model; writes raw OCR catalog rows with columns
`listing_text`, `catalog_page`, `chunk_number`, `image_count`, and `row_type`.

Result (VA): **1,596 rows** (1,539 LISTING + 57 META), 167 chunks with
`image_count >= 1` (~22 min). This CSV is raw vision output. Downstream import
uses the verified catalog rows CSV written by image-extract.

> **Known limitation (MD run, 2026-06-12):** image-extract separates
> illustrations from text at the chunk's single largest vertical gap. On dense
> pages (MD's Baltimore section) real tracings can sit below that cut and are
> unreachable; the review can only reduce the count and forfeit those images.
> Listings still import. MD saw about 14 forfeits vs. 3 on FL. Candidate fix:
> reuse the chunk stage's illustration-block classification instead of
> re-deriving the boundary. Forfeits are cataloged per state in
> `docs/<st>-edge-cases.md`.

## E. Image extract  (deterministic, no API)

```bash
uv run python tools/ascc_image_extract.py VA_ASCC_CTLG \
  --ocr-rows tools/wip/cache/VA_ocr_rows.csv \
  --strict \
  --catalog-rows-out tools/wip/cache/VA_catalog_rows.csv
```

For each chunk with `image_count >= 1`, cuts the marking illustration(s) out by
gap/dark-region geometry (no vision). Writes
`wip/cache/VA_ASCC_CTLG_images/va-<page>-<chunk>-<counter>.png`. If the raw
vision count safely over-counts one substantial marking group, image-extract
emits one image and writes the corrected `image_count` to
`tools/wip/cache/VA_catalog_rows.csv`. Unresolved mismatches fail
under `--strict` before the munger can reference missing files.

Result (VA): 167 subchunks, **205 marking images**, with all import-blocking
statuses resolved.

### hop 2 -- copy marking images into Django's media root

The munger (next step) reads marking bytes from `MEDIA_ROOT/<region>/`, not from
`wip/out/`. Copy them:

```bash
mkdir -p backend/media/va
cp tools/wip/cache/VA_ASCC_CTLG_images/*.png backend/media/va/
```

## F. Munge  (deterministic, no API)

Point `--input` at the verified catalog rows CSV from image-extract,
`--input-dir` at the seeds in `in/`:

```bash
uv run python tools/ascc_data_munger.py \
  --input tools/wip/cache/VA_catalog_rows.csv \
  --input-dir tools/wip/in
```

Writes the full Django-shape CSV set to `wip/out/`: colors (16), letterings (9),
shapes (16), regions (58, passthrough), reference_works (1, passthrough),
post_offices (1057), post_office_regions (1057), markings (2813), dates_seen
(3443), citations (2813), images (276), plus `marking_lineage.csv` sidecar.

> **Town-heading date parsing:** see `DECISIONS.md`. The current munger path
> in `tools/munger/head.py` (`parse_head`) must strip bare trailing
> years off town-table headings (`Accomack C.H 1835` -> `Accomack C.H`).
> Without it the munger aborts with "PostOffice normalization produced N
> name(s) with characters outside [A-Z, space, period, single dash]."

**Verify the "Section-region assignment" report** printed at the top of the
munge output. Listings are assigned the region of the catalog section they
sit under (META banners matching a TERRITORY-tier name in `regions.csv`,
optionally prefixed `AS `; a `STATEHOOD` banner resets to the catalog's
default region; a `TERRITORIAL PERIOD` banner, FL style, maps to the seed's
`<catalog state> Territory` row when exactly one exists, and otherwise falls
into the unmatched report). Check that:

- the per-region listing counts match the catalog's section sizes
  (single-region catalogs like VA show one line: all listings on the default);
- nothing under "Unmatched banner-like META rows" is a real territory
  section the vision step misread (e.g. `MICHIGAN TERRlTORY`) -- if it is,
  fix the row in the extract CSV and re-munge.

A post office listed under several sections (e.g. Detroit) gets one
`post_office_regions` row per section region, so junction-row count can
exceed post-office count on territory-bearing catalogs.

## G. Import  (deterministic, no API)

```bash
uv run python backend/manage.py import_ascc_bundle ./tools/wip/out/
```

Loads in FK-safe order (colors -> letterings -> shapes -> regions ->
reference_works -> post_offices -> post_office_regions -> markings -> ... ->
citations -> images). `covers / cover_valuations / cover_markings` are absent for
this catalog and are skipped. The whole bundle is one transaction -- any error
rolls everything back (no partial state).

**Pre-reqs the bundle needs in the DB (one-time):**

1. **A user with `id=1`.** Every row's `created_by/modified_by` references user 1.
   ```bash
   uv run python backend/manage.py shell -c "from django.contrib.auth import get_user_model as G; U=G(); U.objects.filter(id=1).exists() or U(id=1,username='admin',is_superuser=True,is_staff=True).save()"
   ```
2. **Schema fixes -- check for symptoms first, usually NOT needed.** A truly
   fresh DB + `migrate` on a staging-based branch produces a complete schema
   (verified on the MI run, 2026-06-11: import succeeded with zero fixes).
   The four drifts below were observed once (VA run, 2026-06-09) and were
   almost certainly residue of an earlier broken-`main` migrate attempt
   against the same DB. Apply a fix only if its specific import error
   appears (1364 region_id / 1146 post_office_region / 1054 is_tracing /
   1062 duplicate storage_filename):
   ```sql
   ALTER TABLE post_office MODIFY region_id BIGINT NULL;   -- orphaned NOT NULL col
   ALTER TABLE images DROP INDEX storage_filename;          -- model says NOT unique
   ```
   ```python
   # create 4 model-declared tables 0001_initial never built, + images.is_tracing:
   from django.db import connection; from django.apps import apps
   from common.models import Image
   with connection.schema_editor() as se:
       for n in ['PostOfficeRegion','CoverVersion','MarkingRecycleBin','CoverRecycleBin']:
           se.create_model(apps.get_model('common', n))
       se.add_field(Image, Image._meta.get_field('is_tracing'))
   ```

Result (VA): **`Done. new=11559  update=0  skip=0  invalid=0  error=0`**
(post_offices 1057, post_office_regions 1057, markings 2813, dates_seen 3443,
citations 2813, images 276, + 58 auto-created Collections).

---

## Verify

1. `./woco dev`, open `http://localhost:8080`.
2. Search for a Virginia marking; confirm the listing AND its marking image render.

Verified for VA at the API/media layer (what the search page consumes):

```bash
curl -s http://localhost:8000/api/v2/markings/1/ | python3 -m json.tool   # state VA, region Virginia, 1 image w/ image_url
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' \
  http://localhost:8000/media/va/va-419-2-1.png                            # 200 image/png
```

`/api/v2/markings/?page=1` returns `count: 2813`; post-office names are clean
(0 digit-bearing names; `ACCOMACK`, `ACCOMACK C.H` present).

---

## Gotchas found while validating VA

- **`.env` location:** scripts read `worldcovers/.env`, not the workspace-root `.env`.
- **Free-tier OpenRouter** keys (no credit) 402 on the paid vision model. Use a funded key.
- **`pdftoppm`** must be on PATH (poppler). No-sudo option: `brew install poppler`.
- **Chunk handoff location:** chunks live in `wip/cache/<BASE>_chunks/`.
  `ascc_page_extract.py` and `ascc_image_extract.py` read that directory
  directly; do not move chunk PNGs into `wip/in/`.
- **Media hop:** marking images still need to be copied from
  `wip/cache/<BASE>_images/` to `backend/media/<region>/` before munger/import.
- **`reference_works.csv` ships with 2 rows** (ASCC1 + ASCC2); the munger requires
  exactly 1. For the VA baseline we keep ASCC1 (the published 5th edition the scan
  is from). See `DECISIONS.md`.
- **Non-numeric valuations abort the munger.** MD's Baltimore Postmaster's
  Provisional row is priced "Rare"; there is no valuation representation for it
  yet, so such rows are provisionally re-typed `LISTING` to `META` in the
  scratch CSV until one exists. Expect "Rare" in other states too.
- **`main` branch** can't migrate a fresh DB (`InvalidBasesError`); use `staging`.
- **`staging` migrates but the schema is incomplete** (4 drifts) -- see the Stage G
  pre-reqs and `DECISIONS.md`. This is the single biggest blocker to a clean repro and
  is Michael's to fix at the migration level.

## Summary of changes this runbook depends on

| Change | Where | Status |
|--------|-------|--------|
| `parse_head` town-heading date peel | `tools/munger/head.py` (code) | local patch, branch `reese/issue-1-va-e2e`, **proposed to Michael** |
| 4 schema fixes + user id=1 | local MySQL only | local workaround; **Michael fixes migrations** |
| safe `image_count` overcount correction | `ascc_image_extract.py` | automatic catalog-row correction |
| keep ASCC1 in `reference_works.csv` | scratch seed | baseline choice |
