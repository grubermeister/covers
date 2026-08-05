# v1 image import routes every cover scan onto the marking

**Issue:** Reese's `docs/issues.md` #75
**Status:** fix implemented on `reese/issue-75-v1-cover-routing`, tests green — for Michael's review
**Date:** 2026-08-05

## Summary

Every image the v1 → v2 pipeline imports is attached to a **marking**, including the ones v1
recorded as scans of a whole **cover**. v1's `txtView` column — the only field distinguishing the
two — is discarded as a routing signal and written into `image_description` as free text.

The result is visible on both live sites: whole envelopes render as marking thumbnails in the
catalog. It is also still accruing — each new state injection re-imports the same defect.

This is not contributor error. Of 374 cover-shaped images sitting in marking slots on prod,
**368 came from the v1 import and 6 from live user uploads.**

## Evidence

Read-only sweep of `/api/v2/images/` on both hosts, 2026-08-05:

| | prod `hellowoco.app` | staging `woco.dev` |
|---|---|---|
| Images in a MARKING slot | 713 | 2,170 |
| …cover-shaped (landscape >1.25, >0.6 MP) | 374 | 616 |
| …from the v1 import (`Marking-N-N.jpg`) | **368** | **592** |
| …from live user upload (uuid filename) | **6** | 24 |
| Images in a COVER slot | 21 | 0 |

692 of 713 prod marking images join by filename to `tblTownmarkImages`, all created in the June
import window. Staging gained 367 images in August.

Spot-checked by eye:

- image 2417, 2631×1290, `subject_type=MARKING` — a whole Fetterman VA envelope, address panel,
  manuscript dateline, "10" ratemark
- image 2582, 1898×1507, `subject_type=MARKING` — a folded letter, upside down
- image 2288, 458×465, `subject_type=MARKING` — a correct Berkeley Springs CDS closeup

The `RecordDetail.tsx:747` comment already names the behaviour: *"issue #48: v1 attached every
cover upload to the marking."* #48 shipped the manual repair; this is the source.

## Root cause

Two lines.

`tools/v1_to_v2_catalog_format.py:272-273` (before the fix)

```python
"image_view": IMAGE_VIEW_VALUE,                                # hardcoded "FULL" (:91)
"image_description": (row.get(IMG_VIEW_COL) or "").strip(),    # txtView -> free text
```

`tools/v1_bundle_overlay.py:1018`, `build_images` (before the fix)

```python
"subject_type": "MARKING",     # every v1 image, unconditionally
```

`Front` and `Back` are COVER views in v2's own vocabulary — `IMAGE_COVER_VIEW_CHOICES`,
`backend/common/models.py:472`.

**The ground truth survived the import.** Because `txtView` was written to `image_description`,
prod holds 41 `Front` + 3 `Back` and staging 17 `Back` + 2 `Front` + 1 `Details` on MARKING-subject
rows. Those are provably mis-slotted with no heuristic and no AI, which is what makes the backfill
(#78) tractable.

## The fix

`route_v1_view()` in `tools/v1_to_v2_catalog_format.py` maps `txtView` to a
`(subject_type, image_view)` pair:

| v1 `txtView` | `subject_type` | `image_view` |
|---|---|---|
| `Front` | COVER | FRONT |
| `Back` | COVER | BACK |
| `Details` | MARKING | DETAIL |
| blank | MARKING | FULL — **unchanged** |

Blank `txtView` is 9,736 of 10,072 live v1 image rows. It carries no signal, so it keeps the
current behaviour; guessing would silently reclassify most of the catalog. Those are triaged by
hand under #78.

`build_images` now returns `(images, covers, cover_markings)`. A COVER-routed image gets a `Cover`
record and a `CoverMarking` link back to the marking it was catalogued under — v1 had no cover
records, so both have to be created. Cover codes are allocated as `<RW>-<ST>-C<n>` above the
highest serial already present, so they cannot collide with the munger's institutional covers.
The importer already supports all of this: `covers` and `cover_markings` are in `ASCC_LOAD_ORDER`
ahead of `images`, and `PolymorphicSubjectResourceMixin` already resolves a COVER subject code.

### One deliberate judgment call

**One Cover per cover-view image — Front and Back are never paired.** In the v1 export only 11 raw
rows are a clean `(front, back)` pair, while 31 carry two Fronts and 4 carry four; those are
separate physical covers, so pairing by position would merge distinct ones. Splitting a wrongly
merged cover is harder than merging two with the existing move-image endpoint, so the error is
taken in the safe direction. Rows producing more than one cover emit a
`multiple_covers_from_one_row` warning for human review.

If you'd rather pair them, that's a one-function change and I'll make it — I just didn't want to
guess on your data.

### Backwards compatibility

`subject_type` is optional in the refs CSV. Refs written before this change have no such column and
`build_images` falls back to MARKING — exactly what it did unconditionally before. A regression
test covers it.

## Measured impact

Across the whole live v1 export (10,072 not-deleted rows):

| | rows |
|---|---|
| → COVER / FRONT | 187 |
| → COVER / BACK | 130 |
| → MARKING / DETAIL | 19 |
| → MARKING / FULL (unchanged) | 9,736 |

**317 images (3.1%) reroute**, creating 317 Cover records across 215 catalog rows. 96.9% of rows
are unaffected. The 19 DETAIL rows are a second small correctness gain — they were also being
flattened to FULL.

## Tests

`tools/tests/test_v1_pipeline.py::V1CoverImageRoutingTests` — 7 tests covering the routing table,
cover + link creation, no-cover-for-marking-images, the missing-`subject_type` fallback, cover code
serial allocation, the multi-cover warning, and `write_image_refs` end to end.

Full tools suite: **221 tests, OK** (was 214). `ruff` clean on all changed source files.

## What I'd ask of you

1. **Review and merge before the next state injection** — every state that lands first adds more
   rows for #78 to clean up.
2. **Confirm or overrule the one-cover-per-image call** above.
3. A re-run of one already-imported state to confirm the only bundle diff is image routing would be
   a good gate. `verify/compare_bundles.py` does an ID-agnostic semantic diff. I did not run it
   because building a post-#88 bundle needs the PDF/vision pipeline, which is your lane now — happy
   to run it if you'd rather I did.

## Not in this change

Repairing the images already imported is separate (#78) and needs a cropping tool first (#77):
moving a cover scan off a marking leaves it imageless, and **112 prod markings hold only
cover-shaped images**.
