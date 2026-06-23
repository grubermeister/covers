# ASCC Compare Pipeline -- Stage Reference

The compare pipeline (`tools/ascc_compare.py`, implemented in `tools/compare/stages.py`)
compares the existing live database against a newly OCR-extracted catalog scan and
produces a per-entry review ledger. It runs automatically at the end of `./woco ascc run`.

## Concepts

**v1** -- the existing live catalog data for a state, exported from three DB tables:
`tblRawStateData` (entries), `tblStates` (state IDs), `tblTownmarkImages` (image links).
These files live in `tools/wip/in/` and are the authoritative current state of the DB.

**v2** -- the newly OCR-extracted catalog rows produced by the ASCC pipeline from a
PDF scan. This is `tools/wip/cache/{state}_catalog_rows.csv`. It represents what the
physical printed catalog says.

**Layering rule** -- v1 raw listing text and v1 split columns are different evidence.
`txtRawStateData` is the edition-text witness. The split fields such as `txtDatesSeen`,
`txtColors`, and `txtTownmarkShape` are later stored/editorial evidence. The stored
description layer starts with the description interpreted from `txtRawStateData`, then
appends `txtOther` and `memNotes` as newline-separated lines. The compare pipeline keeps
those layers separate so a row can show both "v1 text differs from v2 text" and "stored
v1 field differs from v1 text".

The pipeline answers: "where does the live DB disagree with the freshly scanned catalog,
and is any of our existing structure (families, images) at risk?"

All output lands in `tools/wip/cache/compare/{STATE}/`. Each stage also writes a
`stage{N}_*_summary.txt` with row counts and samples -- read these first when debugging.
`manifest.json` records input checksums and timestamps for every stage run.

---

## Stage 0 -- Slice

**Question:** What does the live DB currently have for this state?

Pulls all active rows for the target state out of the raw DB export, joins image counts
from `tblTownmarkImages`, and writes a single flat file.

**Output:** `v1_{state}_slice.csv`

This is the foundation for every other v1 file. It contains all original DB columns
(txt*, n*, yn*, dt*) plus an `images_count` column added by this stage.

---

## Stage 1 -- Project

**Question:** How do we reshape the DB data so text, stored fields, families, and images
can be compared without collapsing provenance layers?

Takes the slice and writes six separate "layer" files, each exposing a different view of
the same rows. Different comparison stages need different views, so they are separated here.

**Outputs:**

- `v1_{state}_L0_edition.csv` -- listing text only, reformatted to match the v2 catalog
  column shape (`listing_text`, `chunk_number`, `image_count`, etc.). This is the side
  that gets text-matched against the OCR output in Stage 3.

- `v1_{state}_L1_text_interpreted.csv` -- normalized field values interpreted from
  `txtRawStateData` with the same munger parsing and relationship inheritance rules used
  by import. This includes `shape` when the catalog text supplies an explicit shape code
  or default shape, plus interpreted lettering, date format, manuscript status, and
  description text. Parser fallback shape is recorded in `shape_source` but not treated
  as catalog-text evidence.

- `v1_{state}_L1_parsed.csv` -- the pre-parsed DB fields: town, dates_seen, colors, sizes,
  width, height, value, rates, stored townmark shape, lettering, date format, manuscript
  flags, `txtOther`, and `memNotes`. Used for field-level comparison in Stage 4 as the
  stored/editorial v1 layer.

- `v1_{state}_L2_classified.csv` -- townmark classification fields only (shape, lettering,
  framing, rate location, rate text, rate value, color). Not directly used in the ledger
  but available for deeper debugging.

- `v1_{state}_family_claimed.csv` -- the parent/child relationships as stored in the DB
  (`nRawStateDataID_parent`, `nGroupOrder`). Stage 2 compares these claimed relationships
  against what the munger re-derives from the listing text.

- `v1_{state}_images.csv` -- one row per image, linking each image to its source entry.
  Used in Stage 5 to detect orphaned images.

---

## Stage 2 -- Family

**Question:** Does the DB's parent/child grouping structure agree with what the listing
text actually implies?

Re-runs the same relationship-detection logic the munger uses on import, but applied to
the existing v1 listing text. Compares the result against `family_claimed` from Stage 1.

A family is a group of entries that share a parent (e.g., a town's primary marking and
its variants listed under "Same/..."). The DB stores this explicitly via
`nRawStateDataID_parent`. This stage checks whether that stored structure is consistent
with what the listing text encodes.

**Output:** `family_{state}.csv`

Key columns:
- `detected_family_id` -- the root entry ID of the detected family (all members of the
  same family share this value)
- `detected_parent_key` -- the detected immediate parent (empty for root entries)
- `group_order` -- position within the family (1 = parent/root, 2+ = children)
- `claimed_family_matches_detected` -- `true` or `false`. A `false` means the DB's stored
  parent pointer disagrees with what the munger detects from the text.

---

## Stage 3 -- Align

**Question:** Which v1 entries have a counterpart in the new scan, and which are only
on one side?

Matches v1 entries (from `L0_edition`) against v2 entries (from `catalog_rows.csv`) by
listing text. First tries exact key match, then falls back to fuzzy text similarity
(threshold 0.55). Each entry gets a `disposition`:

- `matched` -- one v1 entry paired with one v2 entry (the normal case)
- `moved` -- paired, but not at the same position in the catalog (may indicate a catalog
  reorganization or a "Same/..." entry resolved differently)
- `duplicate_pair` -- both sides have more than one entry with this listing text; the
  second and subsequent pairs within the group get this disposition
- `v1_duplicate` -- v1 has more copies of this listing than v2 (extra v1 duplicate)
- `v2_duplicate` -- v2 has more copies of this listing than v1 (extra v2 duplicate)
- `added` -- entry exists in v1 but has no match in v2 (may have been removed from the
  new catalog edition, or OCR missed it)
- `removed` -- entry exists in v2 but has no match in v1 (may be new in the new edition,
  or a DB gap)

**Output:** `align_{state}.csv`

The `score` column is the text-similarity ratio (0.0-1.0). Low scores on `matched` or
`moved` entries are worth inspecting -- they may indicate a bad alignment.

---

## Stage 4 -- Fields

**Question:** For entries that exist in both versions, which text-derived and stored field
layers differ?

Only runs for pairs with disposition `matched`, `moved`, or `duplicate_pair`. Compares
ten normalized fields:

- `post_office/town`
- `dates_seen`
- `colors`
- `width/height`
- `rate_val`
- `shape`
- `lettering`
- `date_fmt`
- `is_manuscript`
- `description`

Each (entry, field) row carries the three layer values reviewers need:

- `v1_catalog_value` -- value interpreted from v1 `txtRawStateData` (what the printed
  catalog text says, after parsing).
- `v1_user_value` -- value the user entered into the dedicated v1 split columns
  (e.g. `txtDatesSeen`, `txtColors`). May differ from the catalog text if an editor
  corrected or augmented it.
- `v2_text_value` -- value interpreted from `tools/wip/cache/{state}_catalog_rows.csv`.

It also carries verdicts for the two layer-aware comparisons:

- `catalog_vs_v2_verdict` -- v1 catalog text compared to v2 catalog text. This is the
  edition-text layer check: did the printed catalog change between versions?
- `user_vs_catalog_verdict` -- user-entered v1 split fields compared to the v1 catalog
  text. This is the user-entry layer check: does what the user typed in the DB match
  what the catalog actually says?

Verdict values for `user_vs_catalog_verdict`: `agree`, `differ`, `user_only` (user
entered a value but the catalog text has none), `catalog_only` (catalog text has a value
but the user did not enter it).

Verdict values for `catalog_vs_v2_verdict`: `agree`, `differ`, `v1_catalog_only` (in
v1 catalog but not v2), `v2_catalog_only` (in v2 catalog but not v1).

The file also keeps `user_vs_v2_verdict`, `v1_value`, `v2_value`, and `verdict` as a
compatibility view for older debugging habits. Stage 6 review reasons are based on the
layer-aware verdicts, not on the compatibility verdict.

Example shape case: if v1 catalog text parses as `C` but the user entered `O` in
`txtTownmarkShape`, the row gets `user_vs_catalog_verdict=differ` and Stage 6 records
`S4:user_catalog_shape_differ`. This is intentionally separate from any v1-vs-v2 text
change.

Description stored layer rule: the `description` interpreted from `txtRawStateData`
comes first. `txtOther` and `memNotes` are each split into lines, blank lines are
dropped, and the remaining lines are appended in that order. The result is the
`v1_stored_value` for the `description` field.

**Output:** `fields_{state}.csv`

One row per (entry, field) combination. A single entry with ten differing fields
produces ten rows all with the same `v1_key`.

---

## Stage 5 -- Preservation

**Question:** Did alignment break any family groups apart, and are any images now orphaned?

Two independent checks per entry:

**Family integrity** -- looks at all members of each detected family (from Stage 2)
and checks whether they all landed in the same v2 family after alignment. If some
members matched to one v2 family and others matched to a different one, the family
was split across versions. This can indicate a catalog reorganization or a bad alignment.

**Image coverage** -- for each v1 entry with images (`images_count > 0`), checks whether
its matched v2 entry appears in the munger's output as an entry with images. If a v1 entry
has images but its v2 counterpart does not (or has no match at all), those images have no
v2 home and are flagged as orphaned.

**Output:** `preservation_{state}.csv`

Key columns:
- `family_ok` -- `true` or `false`
- `family_note` -- `S5:family_split` when `family_ok` is false, else empty
- `image_status` -- `no_v1_images`, `represented`, or `S5:orphaned_images`

---

## Stage 6 -- Ledger

**Question:** What needs human review, and why?

Joins all previous stages into one file. One row per v1 entry, sorted by family then
group position. Collects every reason-to-review from all prior stages and sets
`needs_review = true` when any reason applies.

**Output:** `review_ledger_{state}.csv`

This is the file you actually read. See the review workflow in `docs/devel/TOOLS.md` for
how to use it. For a breakdown of all reason codes and column meanings, start there.

**Coverage gap -- `removed` entries do not appear in the ledger.** The ledger is v1-centric:
it only produces a row when a v1 DB entry exists to anchor it. Entries with disposition
`removed` in `align_{state}.csv` have no v1 key (they exist only in the new scan), so
the ledger silently skips them. If `stage3_align_summary.txt` shows a non-zero `removed`
count, those are catalog entries the OCR found that the DB does not have at all -- potential
net-new additions. To inspect them, filter `align_{state}.csv` for `disposition == removed`
and read the `v2_key` column.

---

## Debugging quick reference

If a row in the ledger looks wrong, check stages in this order:

1. `align_{state}.csv` -- is the `disposition` and `score` what you expect?
   A score below 0.7 on a `matched` pair often means a bad alignment.
2. `fields_{state}.csv` -- filter by `v1_key` to see the raw per-field verdicts.
3. `v1_{state}_L1_text_interpreted.csv` -- confirm what Stage 4 parsed from v1 text.
4. `family_{state}.csv` -- check `claimed_family_matches_detected` for structural issues.
5. `v1_{state}_slice.csv` -- the full original DB row for any v1 entry.
6. `stage{N}_*_summary.txt` files -- row counts and sample rows written after each stage.
