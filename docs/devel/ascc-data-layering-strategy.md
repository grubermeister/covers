# ASCC Data Layering Strategy

This note records the ASCC provenance model and the narrow tooling rule used
when comparing Fifth Edition catalog text to the v1 worldcovers.org dump.

## Provenance Model

WorldCovers should load the Fifth Edition first. The Fifth Edition is the
printed, reproducible baseline that state editors can verify against physical
or scanned catalog pages.

The legacy v1 data is useful, but it is not one clean source. It combines:

- Fifth Edition text.
- Unpublished Sixth Edition draft text.
- Programmatic parser output from an older munger.
- Later manual edits to split-out v1 columns.
- Independent `tblTownmarkImages` associations.

The safest path is therefore:

1. Build and import the Fifth Edition baseline through the v2 OCR and munger
   pipeline.
2. Convert v1 `tblRawStateData.txtRawStateData` into the same seven-column v2
   intermediate CSV shape.
3. Use `tools/catalog_edition_diff.py` to compare Fifth Edition text against
   v1 raw text.
4. Treat split-out v1 columns as later editorial evidence, not as part of the
   edition-text diff.

## Family-Level Comparison

ASCC catalog rows are context-dependent. Rows that begin with relationship
markers such as `Same`, `(L)`, or `(E)` inherit context from the nearest prior
independent parent row.

`tools/catalog_edition_diff.py` should therefore compare and export complete
parent families. It should not export a relationship row without the parent row
that gives it meaning.

## Duplicate Source Rows

Duplicate catalog text is not a safe deletion rule for v1. Some v1 records were
already split into multiple source rows even though the printed catalog text is
identical. Those rows can carry distinct image associations through
`tblTownmarkImages`.

The edition-diff matcher may group duplicate text to choose one representative
for alignment. It must still preserve the duplicate COMPARE source rows in the
normal review artifact when those rows carry images.

The simple rule is:

- Export a COMPARE family when any representative row is `added`.
- Export a COMPARE family when any representative row has
  `content_change == "material"`.
- Export a COMPARE family when any COMPARE row has `dup_index > 0` and
  `Images Above > 0`.

The output remains `tools/wip/out/v2_VA_new_modified.csv`. It is a review and
handoff artifact, not proof that v1 image files can be imported automatically.
It keeps image-bearing duplicate source rows visible so a later import or review
step can make an explicit decision.

## Operating Rules

- Do not import v1 wholesale over the Fifth Edition baseline.
- Do not treat all v1 columns as equal evidence.
- Treat `txtRawStateData` as the v1 witness for Sixth Edition draft text.
- Keep edition-text deltas separate from per-column user/editor edits.
- Keep ASCC parent/child families together during comparison and export.
- Preserve image-bearing duplicate v1 source rows in the normal diff output.
