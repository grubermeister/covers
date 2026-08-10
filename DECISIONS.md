# Decisions

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
