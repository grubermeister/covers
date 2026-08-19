# Decisions

## 2026-08-19 — VPHC ingest notation is stripped from `desc` on approval, and that includes the sheet cell (issue #110)

**What was decided.** Approving a VPHC contribution no longer copies the ingest's internal notation
into `Marking.desc`. Two things are removed, not one: the bracketed flag markers
(`[VPHC: ambiguous]`) **and the sheet-cell reference** (`(T1:r6495)`). The original text is
untouched on the contribution, and `MarkingDetailSerializer.vphc_provenance` returns the provenance
blob to editors and the contributor only.

**Why.** `desc` is served by the `AllowAny` markings API and rendered on the public record page, so
approving published the internal flag vocabulary and the spreadsheet cell as public catalog text on
~1,500 entries. **Ian's call, 2026-08-19: keep the doubt, make it editor-only.**

**Why the sheet cell too, which is a widening of how #110 was written.** #110's acceptance criteria
say "the marker is stripped", but its problem statement names three things that become public: *"the
marker, the internal flag vocabulary **and the sheet-cell reference (`T1:r6495`)**."* The first
implementation removed only the brackets, and an over-HTTP check of the anonymous response showed
`(T1:r6495)` still sitting in the public description — it lives in the lead sentence
`apply_vphc_ledger._description` builds, outside the brackets, so no marker pattern touches it.
It is spreadsheet notation with no meaning to a philatelist, the same objection #111 raises about
rule codes in editor-facing prose. Editors lose nothing: the provenance blob carries it as `src`.

`Virginia Postal History Catalog Wytheville #2 (T1:r6495). [VPHC: ambiguous]`
→ `Virginia Postal History Catalog Wytheville #2.`

**Why at approval rather than by re-emitting the queue.** The text is baked into `submitted_data` on
~2,084 contributions and `apply_vphc_ledger` is not idempotent, so a re-emit is a delete-and-rebuild
with eight human submissions to protect. #110 asks for this explicitly: *"stripped or hidden at the
point of approval, not by a later sweep."*

**The two patterns, and why neither is loose.** The marker match is non-greedy and global — 118 of
2,062 rows carry **two** markers, the crossexam one plus the `type_defaulted` one
`apply_vphc_ledger` appends separately (`:452` and `:454`), so a trailing-only strip leaves one
behind. The sheet-cell match spells the format out (`T\d+:r\d+`, `;`-joined) rather than using
`\(.*?\)`, **which would eat any parenthetical an editor later writes into a VPHC record.**

**The edit path needed more than the same one-liner.** An edit merges (B1/RFC 7396), so an
explicitly empty value clears the column — and a marker-only description strips to `""`, which would
silently null a good description the submission never spoke to. The guard keys on *why* the value is
empty: an empty submission still clears; one emptied by stripping leaves the stored value alone.
**Keying on "is this a VPHC payload" instead breaks `test_an_explicitly_empty_value_still_clears`**,
since VPHC payloads can carry a legitimate explicit `""`. That was the first cut, and B1's tests
caught it.

**Gated on the `vphc` key, never on the text or the status** — the standing rule for this queue. A
contributor who types `[VPHC: ...]` or cites a sheet cell keeps their words.

**Evidence.** Dry-run across all 2,062 ingested contributions: 2,062 changed, **0 leftover markers,
0 leftover cells, 0 descriptions emptied**. Verified over real HTTP against a running server, not
just `APIClient` — anonymous and unrelated logged-in users get `vphc_provenance: null` and a body
with no `[VPHC:` and no `T1:r`; an editor gets the blob.

⚠️ **Not swept: markings already approved with notation in `desc`.** Four exist in the local
snapshot. This change only prevents new ones; the existing rows need a separate decision.

---

## 2026-08-17 — Marking list ordering is an annotation contract, not a relation path (issue #103)

**What was decided.** `MarkingViewSet` no longer exposes or defaults to
`post_office__post_office_regions__region__name` as an ordering key. The list queryset carries
`primary_region_name` / `primary_region_abbrev` — correlated `Subquery` annotations resolving the
town's most-recent active non-`SUBREGION_TIERS` region — and those are the ordering keys.

**Why a rewrite and not an allowlist entry.** DRF matches an explicit `ordering_fields` list
**verbatim** (`rest_framework/filters.py:264-291`), so leaving the junction spelling in the list
would hand it straight to `order_by()` and restore the fan-out. Bookmarked search URLs still carry
it, and rejecting it outright would silently change the sort under the user. So
`AliasedOrderingFilter` (`common/filters.py`) rewrites retired keys from a `ordering_aliases` map on
the view. **If you add an ordering key that crosses a to-many relation, it belongs in that map, not
in `ordering_fields`.**

**The invariant worth keeping.** `_primary_region_subquery` mirrors `PostOffice.region`'s tie-break
exactly — `defunct_date DESC NULLS FIRST, established_date DESC NULLS LAST`, minus subregion tiers —
so the sort key and the displayed `state` cannot disagree. Change one and change both.
`PostOffice.region` gained the same tier guard: it resolved to the state before only because VA/WV
carry an `established_date` and counties do not, so one dated county row would have flipped it.

**`PostOffice.regions` (plural) deliberately still returns county links** — the marking detail page
needs them for its County field. Consumers meaning "state/territory" filter on `region_tier`, which
the serializer ships on every entry.

**A trap, measured.** `PostOffice.objects.exclude(post_office_regions__region__region_tier__in=...)`
looks like the obvious way to drop county links and is wrong: excluding across a to-many relation
compiles to `NOT EXISTS`, so it drops every town that has *any* county link — **593 of 2,162 post
offices survived** it locally. `town_options` iterates the junction instead, where the same
predicate is a forward FK.

**Sources.** [Django `order_by`](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#order-by)
· [`distinct`](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#distinct)
· [Subquery](https://docs.djangoproject.com/en/5.2/ref/models/expressions/#subquery-expressions)
· [DRF #6886](https://github.com/encode/django-rest-framework/issues/6886)
· [django-filter usage](https://django-filter.readthedocs.io/en/stable/guide/usage.html) (the
`filterset_fields` dict form behind `region_tier__in`).

## 2026-08-07 — Image validation combines #76's wrong-kind warning with #94's no-image opt-out

**What was decided.** On `Contribute.tsx` and `CoverEdit.tsx`, submission validation now
chains both image rules rather than choosing one:

```ts
if (gallery.length === 0 && !noMarkingImage) {
  errors.images = "Add at least one image or confirm no image is available";
} else if (coverLikeImageCount > 0 && !wrongImageKindAcknowledged) {
  errors.images = "Confirm the highlighted image is correct, or remove it, before submitting.";
}
```

Both controls render — the "No image is available to upload" `Checkbox` and
`<WrongImageKindWarning>`.

**What changed.** Issue #76 as originally implemented made at least one image a hard
requirement (`"At least one image is required"`). That is now relaxed: a contributor who
ticks the no-image opt-out may submit an empty gallery. The wrong-kind acknowledgement is
unreachable when the gallery is empty, so the opt-out always takes precedence.

**Why.** PR #94 (merged to staging 2026-08-06) added the `noMarkingImage` / `noCoverImage`
opt-out to the same validation block that #76 had rewritten. Rebasing
`reese/issue-75-v1-cover-routing` onto staging surfaced this as a direct textual conflict in
both files. Keeping #76's hard requirement would have reverted a feature already merged and
live on woco.dev; dropping #76's check would have descoped the issue. Neither rule
invalidates the other — they guard different failure modes (no image at all vs. an image of
the wrong kind) — so both were kept.

**Source / evidence.**
- Conflict surfaced by `git rebase origin/staging reese/issue-75-v1-cover-routing`,
  commit `8a923a9` (#76) replayed onto `e7b4612` (#95).
- Confirmed with Reese 2026-08-07 before resolution.
- Verified: `npm run lint`, `npm run typecheck`, `npm test` (19 suites / 90 tests), and
  `npm run build` all pass; `frontend/src/lib/contributionToFields.test.ts` (staging's
  opt-out) and `frontend/src/components/WrongImageKindWarning.test.tsx` (#76) both green.

## 2026-08-07 — `0013_image_cropped_from` renumbered to `0014`

**What was decided.** The #77 migration was renamed
`0013_image_cropped_from.py` → `0014_image_cropped_from.py`, with its dependency repointed
from `0012_backfill_marking_date_ranges` to `0013_merge_staging_dateselect`.

**Why.** Staging's PR #94 introduced `0013_merge_staging_dateselect.py`, so after the rebase
two migrations claimed `0013` and the `common` app had two leaf nodes. Git does not flag
this — both files merge cleanly — but Django raises *"Conflicting migrations detected;
multiple leaf nodes in the migration graph"* at runtime. Renumbering to sit after staging's
merge node keeps the graph linear and avoids generating a second merge migration.

**Source / evidence.** `manage.py makemigrations --check --dry-run` → "No changes detected";
`manage.py check` → no issues; `manage.py showmigrations common` shows a single linear tail
ending at `0014_image_cropped_from`.

## 2026-08-12 — VPHC `vphc` provenance blob is deliberately ignored by the field adapter; `lettering` is consumed

**What was decided.** `KNOWN_SUBMITTED_DATA_KEYS` in
`frontend/src/lib/contributionToFields.ts` gained two keys, on opposite sides of the
allowlist's consumed/ignored split:

- `vphc` — **ignored.** The nested provenance blob `apply_vphc_ledger` attaches to every
  ingested contribution (`src`, `cancel_no`, `vphc_code`, `rules_version`,
  `why_unmatched`, `flags`, `county`, `state`). None of it is a catalog field and
  `contribution_apply` never reads it, so it has no row in the field list.
- `lettering` — **consumed**, resolved in `resolveLettering` after
  `lettering_style_name` / `letteringStyleName`. This is *not* a meta key:
  `contribution_apply._resolve_fk(Lettering, payload, "lettering_style_id", "lettering",
  "lettering_id")` uses it as the name key, so a payload carrying it does set the
  marking's lettering on approval. Ignoring it would have hidden a value the reviewer is
  approving.

**Why.** Both keys were absent from the allowlist, so `submittedDataToFieldInput` threw and
`ContributionDetail` rendered its error banner instead of the field list — on all 2,062
pending contributions, i.e. the entire review queue. The fail-loud policy worked exactly as
designed; the adapter had simply not been updated when the VPHC ingest landed (#106).

**The blob is ignored by the adapter but surfaced by the page.** Ignoring `vphc` is the
correct *adapter* decision — it is not a catalog field — but on its own it would have left
the ingest's uncertainty invisible. 1,284 of the 2,062 contributions carry at least one
`vphc.flags` entry (`date_low_confidence` alone accounts for 759), and only
`type_defaulted` and the multi-colour case reached the reviewer, as prose that
`apply_vphc_ledger._description` writes into `desc`. A marking whose century was inferred
and whose county could not be resolved was therefore indistinguishable from one read
cleanly off the sheet.

So `frontend/src/lib/vphcProvenance.ts` + `components/VphcProvenanceCard.tsx` render the
blob as a read-only strip under the field list on `ContributionDetail`: the source
coordinates (VPHC code, cancel no., county, sheet cell), why the row was catalogued as new
rather than matched, and each flag with its explanation. Flags are split into *uncertain*
("this may still be wrong") and *repaired* ("the ingest changed this and is telling you"),
uncertain sorted first. Nothing in the strip is applied to the marking.

**Flag wording is copied, not invented.** `FLAG_REASONS` in the frontend mirrors
`FLAG_REASONS` in `tools/vphc_crossexam.py:228`, which is the source of truth — a reviewer
should read the same sentence in the UI as in the crossexam report. The two flags
`apply_vphc_ledger` adds itself (`type_defaulted`, `color_unrecognised`) have no entry
there and are worded here. An unrecognised flag is displayed rather than dropped: the
crossexam vocabulary can grow, and a flag we don't know is the one a reviewer most needs.

**Source / evidence.**
- Ground truth from the local `worldcovers` DB, not inferred from the ingest script:
  the distinct top-level key set across all 2,062 `Contributions.submitted_data` rows
  diffed against the allowlist yields exactly `["vphc", "lettering"]`.
- `lettering` is present on the 310 edit submissions and is `null` on 305 of them
  (`"Serif"` ×3, `"Outline"` ×2) — `toStr(null)` → `""`, so the renderer prints "-".
- All 2,062 real payloads replayed through `submittedDataToFieldInput` *and*
  `readVphcProvenance` (temporary harness, not committed): 0 failures, 2,062 with
  provenance, 1,284 with flags, 5 lettering values displayed, 0 flags missing from the
  glossary, and all 7 `why_unmatched` verdicts present in the data (`ambiguous`,
  `town_damaged`, `no_colour_match`, `unclassified_device`, `create_no_town`,
  `create_no_prod_markings`, `create_no_inscription`) covered by `UNMATCHED_REASONS`.
- Node 22 (CI's version, not local Node 26): `npm run lint`, `npm run typecheck`,
  `npm test` (25 suites / 130 tests), `npm run build` all pass.

## 2026-08-15 — Backups: mysqldump over MySQL Shell, `/var/backups` over `/srv/woco/backups`, DB before media

**What was decided.** The automated backup system (`deploy/worldcovers-backup.sh` and
friends) makes three choices that a reasonable reader might expect to go the other way.

**1. `mysqldump`, not the MySQL Shell dump utilities.** The MySQL 8.0 manual now
carries a Tip recommending Shell's utilities over `mysqldump`
([using-mysqldump](https://dev.mysql.com/doc/refman/8.0/en/using-mysqldump.html)),
and `mysqlpump` is deprecated as of 8.0.34
([mysqlpump](https://dev.mysql.com/doc/refman/8.0/en/mysqlpump.html)). Shell's
advantage is parallel dumping and integrated compression, which matters on large
datasets; ours is a **1.7 MB compressed / 22.2 MB raw** dump. Against it: Shell has its
own dump format, and there is no equivalent on prod's MariaDB — so adopting it means
carrying two tools and two restore paths for two environments whose comparability is the
whole point. `mysqldump` is also what the 2026-08-07 manual backup used and what the
2026-08-10 restore rehearsal passed with. The flag set is frozen to that proven set plus
`--no-tablespaces` (`PROCESS` is required without it as of 8.0.21, and `wocod`
deliberately lacks it). Deliberately excluded: `--databases` (keeps the dump loadable
into a scratch DB for rehearsal), `--events` (none exist), and `--set-gtid-purged`
(MySQL-only; MariaDB's dumper rejects it — the same portability trap as
`ISSUE-2026-08-10-01`, one layer down).

**2. `/var/backups/woco`, not `/srv/woco/backups`.** The latter already exists and
already holds 1.9 GB. It is also **inside the git checkout** that `deploy/deploy.sh`
runs `git reset --hard` against, and that a recovery would re-clone. Backups must not
live in a directory the deploy process manages. `/var/backups` is the FHS location and
is on the same filesystem as the media tree, which is what makes `rsync --link-dest`
hardlinks work at all. The 1.9 GB of pre-existing content was left alone: deleting data
during a backup rollout is exactly backwards.

**3. The database is dumped before media, and the order is a correctness property.**
`mysqldump --single-transaction` snapshots at time T; the media rsync finishes at T+n. A
file created in that window lands on disk with no DB row — a harmless orphan. The
reverse order yields DB rows pointing at files that were never copied — a broken image
link, which is precisely the failure `backups/2026-08-07/README.md` warns about.

**Accepted limitation, recorded rather than hidden.** `--single-transaction` is not
isolated from concurrent DDL: the manual states that while such a dump is in process, no
other connection should use `ALTER TABLE`, `CREATE TABLE`, `DROP TABLE`, `RENAME TABLE`
or `TRUNCATE TABLE`
([mysqldump](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html)). A `deploy.sh`
migration racing the 02:30 UTC window would produce an inconsistent dump that passes
every check we make. Low probability, not cheaply fixable, documented in `BACKUP.md`.

**Related:** the manifest's row-count census is taken from the live database a few
seconds before `START TRANSACTION`, so `worldcovers-restore` treats a row-count
difference as a **report** and only a missing table as a hard failure. Claiming exactness
we do not have would be worse than saying so.

**Source / evidence.** Verified end to end on woco.dev 2026-08-15: first snapshot 43
tables / 7,693 media files / 1.33 GB, with `common_region` (199) and `post_office`
(5,754) matching the live API exactly from an independent path. Restore rehearsal from
the pulled copy passed with the census exact and media 0 missing / 0 corrupt / 0 size
drift. Hardlinking measured: two snapshots that would be 2.52 GB as independent copies
occupy 1.26 GB. Failure injection (unreachable database) left `LAST_SUCCESS` untouched,
pruned nothing, and wrote `ALERT` with `consecutive_failures=1`.

## 2026-08-15 — Marking edits merge (RFC 7396) instead of rebuilding the row

**What was decided.** `_apply_marking_edit` no longer rebuilds a Marking from the
contribution payload. It merges: a key the payload never mentions leaves the stored value
alone, and only an explicitly empty value clears it. `Contribute.tsx` was changed to match
— the marking submit form now always sends its optional scalars, using `""` rather than an
omitted key to mean "the contributor emptied this box". Both halves are required; either
alone is wrong.

**What changed.** Previously every column was assigned unconditionally from
`payload.get(...)`, so an absent key was written as `NULL`. Only `color` and
`display_submitter_name` were presence-checked. `_sync_citations` likewise deleted every
existing Citation before reading the payload, so silence about citations read as "delete
them all".

**Why.** Approving the 310 queued VPHC edit contributions would have destroyed catalog data
without erroring — `full_clean()` passed, the approval returned 200, and a clean audit trail
was written. Measured read-only against woco.dev 2026-08-13: `impression` nulled on 288 of
288 matched markings, the existing ASCC citation deleted on 118 of 120 sampled, `desc`
overwritten on 9, `date_fmt` nulled on 4. The crosswalk carries
`prod_code/type/insc/shape/width/height/color` and has no `impression`, `date_fmt`,
`rate_val` or `desc` column at all, which is why the cross-examination never compared the
fields that were being nulled.

This is *not* a change to the overwrite policy ("where the sheet and production disagree,
the sheet wins"). These are fields the sheet never spoke to.

**Why the frontend had to change too.** The obvious fix — copy `_apply_cover_edit`'s
absent⇒keep semantics — is unsafe on its own, because the submit form collapsed "empty" to
an omitted key (`impression.trim() || undefined` in the JSON branch; a skipped
`form.append` in the multipart one). Backend-only would have fixed the ingest while making
it impossible to ever clear a field from the UI. `_apply_cover_edit` gets away with
absent⇒keep because `CoverEdit` is a genuinely partial form.

**Alternatives ruled out.** Back-filling the missing fields in `apply_vphc_ledger` only —
smallest change, but leaves the approval path armed for the next partial producer, and
issue #101 (bulk approve) would amplify it. A marker key declaring a payload "partial" —
no frontend change, but makes correctness depend on a flag every producer must remember to
set.

**Two things in scope that were not in the original framing.**
1. `_sync_citations` did not know the `reference_work_ids[]` spelling that multipart
   submissions actually produce (`catalog_codes._first_reference_work_id` already read all
   three spellings). Any image-carrying marking submission therefore had its citations
   deleted and not recreated. Fixed by teaching `_sync_citations` the third spelling.
2. Naming any citation id states the complete desired set, so merge semantics alone does
   not save the ASCC citations — the *producer* was wrong. `apply_vphc_ledger` now sends
   the union of the marking's existing reference works plus VPHC1, VPHC1 first
   (`catalog_codes` derives the code prefix from the first id).

**Known consequence, accepted.** Clearing *all* citations from a multipart (image-carrying)
submission is no longer possible — the empty case sends no key, which now reads as silence.
The path it replaces deleted every citation on every multipart submission, so this is a net
improvement; a proper fix belongs with the separate defect that the submit view flattens
`reference_work_ids[]` to its last value only (`api/v2/views.py`, "Strip multi-value form
keys to plain values").

**Source / evidence.**
- IETF RFC 7396, JSON Merge Patch — absent members are left untouched, null removes:
  https://www.rfc-editor.org/rfc/rfc7396
- DRF partial updates — omitted fields come from `self.instance`, the same reading:
  https://www.django-rest-framework.org/api-guide/serializers/#partial-updates
- Django field options — `blank=True` makes an empty value valid and `choices` are not
  enforced against empty values, so `""` passes `full_clean()` on `impression` / `date_fmt`
  and the existing `.strip() or None` normalizes it to NULL:
  https://docs.djangoproject.com/en/5.2/ref/models/fields/#blank
- Regression fixture: `backend/common/tests/fixtures/vphc_edit_payloads.json` — one real
  payload per distinct key-set across the 310 queued edits (18 shapes, censused from the
  local `Contributions` table). None of the 18 mentions `impression`, `date_fmt` or
  `description`.
- Verified: `manage.py test common` (222 tests) OK; `manage.py check` clean; `tools`
  suite (283 tests) OK; Node 22 `npm run lint` / `typecheck` / `test` (25 suites, 130
  tests) / `build` all pass.

**Operational follow-up (not code).** The 310 already-queued edits still carry
`reference_work_ids: [VPHC1]`. Merge semantics fixes their scalars with no regeneration,
but not their citations — they must be deleted and re-emitted with
`manage.py apply_vphc_ledger --only update` (note: `--only`, not `--steps`).
`apply_vphc_ledger` is not idempotent, so the delete is mandatory.
