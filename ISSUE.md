# WorldCovers / APMC -- Consolidated Issues

**Single source of truth for engineering work on WorldCovers (WoCo) / APMC.**
This file merges every WoCo issue we currently track, from all sources, into one
list so there is exactly one place to look.

| Source | What it was | Folded in here as |
|---|---|---|
| `06-09-emails.md` (Ian Gibson-Smith, Greg Stone) | Raw beta feedback + change requests | Issues **1-27** |
| Reese's working queue (`docs/issues.md`) | Pipeline milestones M1-M3 | Tagged **[R1]-[R8]** inline |
| `docs/DECISIONS.md`, `docs/mi-edge-cases.md`, `docs/michigan-report-for-michael.md` | Michigan E2E status + open calls | Status + Issues **28-34** |

Issues are numbered **1-34**, no prefixes. The earlier `ISSUES.md` /
`trello_cards_based_on_issues.md` pass and the Trello board are being retired in
favor of this file.

**Scope:** WorldCovers / APMC only. IanThom.org and ChinaOverprints (and the
Bluehost/Porkbun account admin) are a separate, lower-precedence track and are
**not** in this file. Credentials and donor lists from the email archive are
deliberately omitted here.

**Precedence:** *"Worldcovers takes total precedence"* -- Ian, 9 Jun 2026.

**Glossary:** WoCo = the software. APMC = the dataset (proposed go-live brand).
ASCC = the first catalog being digitized into APMC. USPCS = U.S. Philatelic
Classics Society (sponsor). VPHC = Virginia Postal History Society catalog.
v1 = the legacy ColdFusion/MSSQL system behind worldcovers.org (`worldcovers-v1/`).

---

## Status snapshot (as of 2026-06-15)

**State rollout order (Ian):** VA -> WV -> **Michigan** -> Maryland -> Florida -> Tennessee -> Alabama.

| Milestone | What | Status |
|---|---|---|
| **M1** | Reproduce VA E2E locally, then Michigan E2E | **Substantially done** |
| [R1] | VA pipeline reproduced E2E locally | [DONE] **DONE** -- 11,559 rows, 0 errors. Required local patches (not unmodified); see `docs/DECISIONS.md`. |
| [R2] | Michigan E2E via per-state extensibility | [DONE] **DONE** -- 10,224 rows, 0 errors; verified API/media. **PR #50** (this branch). Verdict: **config-only, no MI adapter class.** Open tails -> Issues 28-32. |
| M2 | Frontend gaps: user upload+verify, territory UI | Started -- Issues 11, 17, 25, [R3]-[R5]; Issue 18 done |
| M3 | QoL: citation search, branding, opt-out | Started -- Issues 23, 24, [R6]-[R8]; Issue 22 done |

**Highest-value finding (resolved):** the four "fresh-install schema drift" alarms
from the VA write-up **did not reproduce** on a clean DB -- they were residue of an
earlier broken-`main` migrate against the same DB. Migration-integrity alarm
**downgraded**. The local test-DB privilege gap is fixed (Issue 33).

---

# Issues

Status values: `open` - `in-progress` - `blocked` - `done`.

## Data & ingestion

### Issue 1 -- Fix WV data ingestion ("WV disconnect")
**Status:** open - **Depends on:** none
West Virginia markings don't appear in listings -- Martinsburg and Shepherdstown
are entirely absent, and Ian's own markings don't show. Ian: add WV **asap**.
- [ ] Martinsburg, WV markings appear in listings
- [ ] Shepherdstown, WV markings appear in listings
- [ ] Ian's submitted markings are findable
- [ ] Root cause documented: import gap vs. query/filter bug

### Issue 2 -- Fix Richmond town-name normalization
**Status:** open - **Depends on:** none
Records render the town as `Richmd, VA` instead of "Richmond" -- unsearchable and
looks broken to beta testers. (Same *family* of head-parsing normalization as the
Michigan territory-suffix residue, Issue 28.)
- [ ] Records showing `Richmd` display as "Richmond"
- [ ] Searching "Richmond, VA" returns those markings

### Issue 3 -- Stampless parser: unknown year misread as rate (Amelia, VA)
**Status:** open - **Depends on:** none - **Source:** Greg Stone
When the catalog year is unknown (`--`), the parser uses the CDS/circle **size** as
a rate marking. Systematic corruption of imported VA records.
- [ ] Amelia, VA no longer shows the CDS size as a rate
- [ ] Unknown-year (`--`) records import with no rate fabricated from size
- [ ] Spot-check of other unknown-year VA entries confirms the fix

### Issue 4 -- Marking-shape parsing: circle imported as straight-line (New Glasgow)
**Status:** open - **Depends on:** none - **Source:** Greg Stone
Markings shown as circles in the Stampless catalog import as straight-line.
- [ ] New Glasgow imports with the correct circular shape
- [ ] Spot-check confirms catalog circles aren't imported as straight-line

### Issue 5 -- Import updated VA data from worldcovers.org
**Status:** in-progress (Michael) - **Depends on:** none
Bring the updated Virginia records from worldcovers.org into WoCo. VA is first in
the rollout and the basis for beta testing and the VPHC comparison.
- [ ] VA records from worldcovers.org present in the new system
- [ ] Record counts reconcile between source and target

### Issue 6 -- Compare and import VPHC Catalog (VA)
**Status:** open - **Depends on:** 5
Diff the VPHC Catalog against WoCo's VA data, then import VPHC VA entries.
- [ ] Diff of VPHC vs. WoCo VA produced
- [ ] VPHC entries imported with `VPHC Catalog 1st Edition` as reference work (Issue 13)
- [ ] Duplicates reconciled, not double-listed

### Issue 7 -- Enter Michigan data -- [DONE] DONE
**Status:** done (pipeline) - **Depends on:** none - **Assigned:** Reese [R2] - **PR #50**
Michigan imported E2E: **10,224 rows, 0 errors** (2,617 markings, 837 post
offices, 93 images), verified at API/media. Section-driven region assignment
validated live (Detroit -> 4 regions; Michigan Territory carries 292 POs incl.
WI/IA/MN precursors). Architecture verdict: **config-only, no Michigan adapter** --
everything state-specific was *data*, not *code*. Entry-process notes captured in
`docs/PIPELINE.md` for repeatability on later states.
**Remaining tails -> Issues 28-32** (territory-suffix stripping, `#N` offices,
territory abbrevs, blessing region rows, territory search/UI).

### Issue 8 -- State rollout tracking
**Status:** open - **Depends on:** none
Track ingestion order VA -> WV -> MI -> MD -> FL -> TN -> AL with current status each.
(Live status lives in the snapshot above; this issue = the standing tracker.)
- [ ] Checklist exists with all 7 states in order + current status

## Bugs & cleanup

### Issue 9 -- Editor dashboard: delete bad records
**Status:** done (staging data cleanup) - **Depends on:** none
Delete the folded-cover record dated `1864-01-01` (submitted by Ian, missing
image) and the other fake submission Ian rejected.
- [x] `1864-01-01` folded-cover record removed
- [x] Rejected fake submission removed
Resolution note, 2026-06-14: closed by the staging orphan-submission cleanup
recorded under "Completed staging fixes" below.

### Issue 10 -- Submitter edit/revise permission bug
**Status:** done (account permissions) - **Depends on:** none
Authenticated editor (repro: Wayne Farley) could not revise/reject their own
pending listing -- *"not authorized to reject or revise anything."* Michael's manual
user-ID fix suggests a **systemic permissions gap after account migration** from
worldcovers.org.
- [x] Editor can edit and approve their own pending/approved listing
- [x] Migrated accounts get correct permissions without manual intervention
- [x] Regression: no editor sees a spurious "not authorized" on their own records
Resolution note, 2026-06-15: current backend auth payload and contribution
queries use editor role plus assigned collections. The original migrated-account
permission failure is no longer tracked as open. Review-pass caveat: the Wayne
Farley reproduction is not covered by a narrow regression test; see
"Review pass, 2026-06-15" below.

### Issue 11 -- Submit New Cover: set the main image marking (Prospect Hill bug)
**Status:** open - **Depends on:** none - related **[R3]**
A newly added cover can't be designated the main image for its marking
(reproduced by Ian on Prospect Hill). Editors can't control which image
represents a marking. Backend (`ImageViewSet`/`ImageResource`) exists -- overlaps
the [R3] `SubmitImageDialog.tsx` -> `/api/v2/images/` wiring.
- [ ] After adding a cover, submitter can mark it the main image
- [ ] Chosen main image displays on the marking detail screen

## UI copy & forms

### Issue 12 -- Canonical submission-guidelines content block
**Status:** open - **Depends on:** none
One reusable guidelines block (used on three submission pages -- define once):
1. Image quality -- "300 dpi preferred"
2. Rate vs. Auxiliary -- *Rate: "Paid", "Free", "3", "Due 3"; Auxiliary: "Advertised", "Missent"* (final wording gated on Issue 21)
3. Reference works -- "To add a new reference, please add a note to editor for approval and addition"
4. Date-verification -- "please include image verifying date if not on exterior of cover"
- [ ] Reusable component/string exists with all four items
- [ ] Content approved against Ian's wording

### Issue 13 -- Submit New Marking page updates
**Status:** open - **Depends on:** 12
- [x] Rename "Create Marking" -> **"Submit New Marking"**
- [ ] Expose **ERD** (Earliest Recorded Date) and **LRD** (Latest Recorded Date) fields
- [ ] Reference Works lists **"ASCC Edition 5", "ASCC Edition 6", "VPHC Catalog 1st Edition"** (currently shows ASCC twice -- remove dup)
- [ ] Apply guidelines block (Issue 12)
- [ ] Townmark field: hover/bracket note that it means the **EXACT** text on the marking
Progress note, 2026-06-15: page H1 now uses `Submit New Marking`.

### Issue 14 -- Submit Edit to Marking page updates
**Status:** open - **Depends on:** 12
- [x] Rename "Edit Marking" -> **"Submit Edit to Existing Marking"**
- [x] Date-format selector: "Select one or more date formats" -> **"Select Date format"** (single-select)
- [ ] Document date-format codes: **MD** (month/day), **MDD** (month and day), **YMD** (year and month), **YMDD** (year, month and day) -- WARNING *source listed "YMDD" twice; confirm the year-and-month code with Ian*
- [ ] Add ERD/LRD fields
- [ ] Apply guidelines block (Issue 12)
Progress note, 2026-06-15: edit-marking H1 now uses the requested wording; date
format now behaves as a single-select radio menu and uses the requested empty
state text.

### Issue 15 -- Submit New Cover page updates
**Status:** open - **Depends on:** 12
- [ ] Apply guidelines block (Issue 12), including the date-verification-image note

### Issue 16 -- Rename "Record Details" to entity-specific details
**Status:** done (frontend copy) - **Depends on:** none
- [x] Cover detail and cover contribution pages read **"Cover Details"**
- [x] Marking detail page reads **"Marking Details"**
Resolution note, 2026-06-15: replaced the generic `Record Details` label with
entity-specific details labels: `Cover Details` for covers and `Marking Details`
for markings.

### Issue 17 -- Submitter acknowledgement on covers
**Status:** open - **Depends on:** 16 - related **[R8]**
On **Submit New Cover** (covers only, **not** markings): "Would you like your
name to display as the submitter?" If yes -> show "Submitted by [name]" on the
Cover Details screen. Incentivizes uploads.
- [ ] Yes/no acknowledgement checkbox on Submit New Cover
- [ ] When yes: "Submitted by [name]" appears on Cover Details
- [ ] When no: no submitter name shown
- [ ] Option does **not** appear on marking submission

### Issue 18 -- Catalog Search: search by size (diameter)
**Status:** done (frontend/API filter) - **Depends on:** none
- [x] Catalog Search exposes a size/diameter filter
- [x] Searching by diameter returns matching markings
Resolution note, 2026-06-15: `frontend/src/pages/Search.tsx` shows a Diameter
input for circle-family shapes and mirrors diameter to both `height` and `width`.
`frontend/src/services/markings.ts` sends those params to `/api/v2/markings/`;
`backend/common/filters.py` applies exact `height` and `width` filters.

## Markings model & display (Greg Stone parsing feedback)

### Issue 19 -- Ratemark display: plain vs. in-circle rate
**Status:** open - **Depends on:** none - **Source:** Greg Stone
Show the difference between a plain rate ("5") and a rate enclosed in a circle --
as the Stampless catalog distinguishes Cumberland vs. Curdsville.
- [ ] A "5" and a circled "5" render distinguishably
- [ ] Cumberland and Curdsville display their correct rate styling

### Issue 20 -- "Years Seen" model for marking dates
**Status:** open - **Depends on:** none - **Source:** Greg Stone
**WARNING Model change -- scope first; record decision in `docs/DECISIONS.md`.**
Replace the strict Earliest/Latest pair with discrete known years/ranges to handle
hiatuses (Greg's Aquila: **1811, 1849-1855**), for both handstamp and manuscript.
Ian's counter-view: keep Earliest+Latest and note ranges in the detail notes -- to
be resolved with the board. **Interacts with the `parse_head` year-peel (Issue 32),
which currently *drops* trailing years this model would want to preserve.**
- [ ] Design note: data model + migration from Earliest/Latest
- [ ] Aquila shows "1811, 1849-1855" rather than a single 1811-1855 range
- [ ] Handstamp listings extract years as shown in source
- [ ] **Decision recorded in DECISIONS.md**

### Issue 21 -- Rate vs. Auxiliary classification decision
**Status:** open - **Depends on:** 12 - **Source:** Greg Stone
**WARNING Board decision; record in `docs/DECISIONS.md`.**
Keep Ratemarks as a distinct class, or treat all non-townmarks as Auxiliary
(Greg's proposal: "Paid" and "Paid 5" both Auxiliary)? Ian: *"a marking is a
marking at the end of the day"* -- wants a board ruling before conventions harden.
- [ ] Classification decision documented with rationale
- [ ] Guidelines (Issue 12) reflect the final convention
- [ ] **Decision recorded in DECISIONS.md**

## Content, branding & citations

### Issue 22 -- Rewrite the Acknowledgements page
**Status:** done (content + route) - **Depends on:** none
Replace with Ian's supplied copy (sections: U.S. Philatelic Classics Society,
Project Team, Beta Testers, Past Editors of the Catalogue, Donors). Full verbatim
text in `06-09-emails.md` -> "Acknowledgements" thread, and drafted at
`worldcovers/docs/acknowledgements.md`.
- [x] Page matches the supplied sections
- [x] All named people/orgs match the email exactly (spelling verified)
Resolution note, 2026-06-15: `docs/acknowledgements.md` contains the required
sections, `frontend/src/App.tsx` routes `/acknowledgements`, and
`frontend/src/components/Footer.tsx` links to the page.

### Issue 23 -- Branding: APMC + Classics Society
**Status:** open - **Depends on:** none - related **[R7]**
**WARNING Board approval required before merge.**
Replace "Worldcovers" branding with **APMC**, add the U.S. Philatelic Classics
Society logo, and add Society website / "Become a member" links.
- [ ] "APMC" branding + Classics Society logo replace "Worldcovers"
- [ ] Society website + membership links present and correct
- [ ] Final branding/logo/links confirmed with the board before merge

### Issue 24 -- Per-state reference citations
**Status:** open - **Depends on:** 13
As each state is added, attach its source reference/citation (e.g. VPHC Catalog) --
listings from another catalog are accepted as genuine on that catalog's authority.
- [ ] Imported listings carry their source reference work
- [ ] VA imports cite the VPHC Catalog where applicable

## Lower priority / later

### Issue 25 -- Marking with multiple states/territories
**Status:** open - **Depends on:** none - related **[R5]**
Support and display a single marking belonging to more than one state/territory.
**Spike already answered by the Michigan run:** the `post_office_regions` junction
is **already many-to-many** (`unique_together [post_office, region]`); Detroit
links to four regions and `PostOffice.region` resolves the display region. So this
is largely **display + filter UI**, not a schema change. See Issue 31.
- [ ] A marking can be associated with multiple states/territories
- [ ] Detail screen displays all associated states

### Issue 26 -- Submit a cover for additional markings
**Status:** open - **Depends on:** none
After a cover is submitted for one marking, allow associating it with another
marking without re-uploading (many covers carry multiple markings).
- [ ] From a submitted cover, user can associate it with another marking without re-uploading

### Issue 27 -- Submitter self-delete option (open question)
**Status:** open - **Depends on:** none
**WARNING Decision required.** Should submitters be able to delete their own submissions?
- [ ] Decision recorded (yes/no) with rationale
- [ ] If yes: submitter can delete own submission. If no: close in tracker.

---

# Michigan E2E open tails (from PR #50)

These are concrete follow-ups surfaced by the Michigan import. They were
**cataloged, not fixed** (Reese/Michael agreement: log each, keep separate from the
VA patches, get a handling call before changing code). Full evidence:
`docs/mi-edge-cases.md`, `docs/michigan-report-for-michael.md`, `docs/DECISIONS.md`.

### Issue 28 -- Territory-suffix residue fragments post offices  * biggest MI data-quality item
**Status:** open (needs Michael's call) - **Depends on:** 7
Head parsing leaves `M.T` / `Mic.T` / `Mich.Ty or M.T` in town names, so **173 of
837 PO names** carry residue and the same town splits across periods (`ADRIAN M.T`
!= `ADRIAN`; Green Bay = 3 variants; only 37 merged cleanly). With section-driven
regions, the suffix is now **redundant** -- stripping it in `parse_head` (same family
as the VA trailing-year peel, `8be62ca`) fixes fragmentation and loses nothing.
User-visible today ("Town: GREEN BAY M.T"). **Not touched pending decision.**

### Issue 29 -- `#N` office numbers: Port Lawrence #1 / #2 (2 real markings excluded)
**Status:** open (needs Michael's call) - **Depends on:** 7
`#` is outside the munger's PO-name charset -> import aborts. Two real Toledo-Strip
offices (values 1000.00 / 1250.00) were provisionally re-typed LISTING->META in the
scratch CSV to complete the run. **They must come back** once Michael picks a
handling: allow `#` in the charset, or normalize to `NO. 1`.

### Issue 30 -- Bless `regions.csv` territory rows into canonical data + DB
**Status:** open (needs Michael's go) - **Depends on:** 7
Two proposed rows live only in the gitignored scratch `tools/wip/in/regions.csv`:

| id | name | tier | parent | established | defunct |
|---|---|---|---|---|---|
| 59 | Michigan Territory | TERRITORY | 1 (USA) | 1805-06-30 | 1837-01-26 |
| 60 | Indiana Territory | TERRITORY | 1 (USA) | 1800-07-04 | 1816-12-11 |

Dates cross-checked (Wikipedia / Indiana Historical Bureau); follow the seed
convention that a territory's `defunct_date` = its successor's `established_date`.
Canonical `ASCC Data/regions.csv` and the DB are untouched pending Michael's OK.
**Sub-question:** abbrevs for both territories -- left empty (catalog only offers
the ambiguous `M.T.`). Hard constraint for any future code: must **not** collide
with a 2-char state abbrev (`MI`/`IN`/`MT` taken), since the munger keys catalog
files to regions by exact filename-prefix match.

### Issue 31 -- Territory search/UI surfacing
**Status:** open (design) - **Depends on:** 7 - related **25, [R5]**, M2
Territory regions hang off USA (parent id 1), not off their successor state -- so a
`region=MI` search won't include Michigan Territory markings as modeled. Decide
whether territories should also parent under (or alias to) their successor state
for search. Also: region filtering is per **post office**, not per **marking** -- a
town in two regions shows all its markings under either filter (correct per the
junction, but a territory-UI design input). Feeds the M2 territory-filter work.

### Issue 32 -- `parse_head` town-heading date patch (VA + MI)
**Status:** proposed (commit `8be62ca` on PR #50) - **Depends on:** none - related **20**
Strips bare trailing dates off town-table headings; required for both VA and MI to
avoid digit-bearing PO-name aborts. Still a **proposal** for Michael. It currently
**drops** the peeled year -- which conflicts with Issue 20 ("Years Seen") wanting
years preserved. Long-term fix may capture the year into a date field instead.

### Issue 33 -- Local backend test-DB privilege
**Status:** done (infra) - **Depends on:** none
Resolved locally on 2026-06-12: `wocod@localhost` has `GRANT ALL PRIVILEGES ON
test_worldcovers.*`, and Django can create/drop the test DB. Verified from repo
root with `uv run python backend/manage.py test common.tests.test_api_permissions
-v 2 --noinput` (exit code 0). The setup SQL now grants `test_worldcovers.*` for
fresh installs.

Staging checkouts without that test module can verify the DB grant directly:

```sh
sudo -u wocod -H bash -lc 'cd /srv/woco && mysql --defaults-file=mysql.cnf -e "DROP DATABASE IF EXISTS test_worldcovers; CREATE DATABASE test_worldcovers CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; DROP DATABASE test_worldcovers;"'
```

Expected exit code: 0.
*(The four "migration drift" alarms from the VA write-up are
**withdrawn** -- fresh installs are healthy; see DECISIONS.md.)*

### Issue 34 -- VA + MI coexistence in one DB (ID offsetting)
**Status:** open (tracked elsewhere) - **Depends on:** 5, 7
The importer keys on raw PKs and the munger renumbers each state from 1, so MI went
into a **fresh** DB for the E2E proof. Multi-state coexistence needs the
ID-offsetting work (tracked as Ian's Issue #8 in the v2 backlog). Noted here only
to confirm MI E2E does **not** attempt it.

---

# Completed staging fixes

These were found while debugging staging after the original 1-34 issue merge.
They are recorded here so the next cleanup pass does not rediscover them.

## Staging orphan submissions pointing at missing records
**Status:** done (staging data cleanup) - **Date:** 2026-06-14
Approved submission rows were visible from the user-submissions dashboard even
when their target marking or cover route no longer resolved. Example symptoms:
`/record/3005` returned not found, and `/record/3005/cover/1673` linked through
the same missing parent marking. Root cause was staging data, not stale code:
approved contribution/version rows referenced records that no longer existed or
could no longer be reached.

Cleanup rule used on staging:
- Find approved `Contributions` rows whose `marking_id` is missing, or whose
  cover/version data points through a missing parent marking.
- Delete dependent version rows first, or set their `transaction_id` to `NULL`
  before deleting parent `SubmissionTransactions`.
- Delete the bad `SubmissionTransactions`.
- Delete the bad `Contributions`.

No code change was required for the data purge itself.

## Dashboard Back from approved submissions
**Status:** done (frontend) - **Date:** 2026-06-14
Opening an approved marking or cover from the user-submissions dashboard and then
clicking Back could navigate to a parent record instead of returning to the
dashboard. The dashboard now passes `fromDashboard` plus the originating
`dashboardTab`, and the detail pages preserve that state through approved-record
redirects.

Files changed:
- `frontend/src/pages/Dashboard.tsx`
- `frontend/src/pages/ContributionDetail.tsx`
- `frontend/src/pages/CoverContributionDetail.tsx`
- `frontend/src/pages/CoverDetail.tsx`
- `frontend/src/pages/RecordDetail.tsx`

Verified from `frontend/` with `npm run build`; expected exit code 0.

---

# Review pass, 2026-06-15

Scope: `ISSUE.md` checked against the current repo state after marking Issues
9, 10, 18, 22, and 33 resolved.

Findings:
- Issues 9, 10, 18, 22, and 33 are now marked `done`.
- Issue 18 is supported by current source: `frontend/src/pages/Search.tsx`
  exposes Diameter for circle-family shapes, `frontend/src/services/markings.ts`
  sends `height` and `width`, and `backend/common/filters.py` filters exact
  dimensions. Test gap: no backend test currently exercises dimension filtering.
- Issue 22 is supported by current source: `docs/acknowledgements.md` has the
  required sections, `/acknowledgements` is routed in `frontend/src/App.tsx`, and
  the footer links to it.
- Issue 33 was already correctly marked `done`; no change needed beyond this
  review note.
- Issue 16 is now closed with entity-specific labels: `Cover Details` for cover
  pages and `Marking Details` for marking pages.
- Issue 9 is a staging data cleanup, not a source-code behavior; source review
  cannot re-prove the deleted staging rows. The completed cleanup procedure is
  recorded below and now linked from Issue 9.
- Issue 10's original migrated-account permission failure is closed. Current
  code uses editor role plus collection assignments for contribution visibility
  and review access. Test gap: there is no narrow regression test named for the
  Wayne Farley reproduction.
- No additional stale `done`/`open` mismatch was found in the checked areas.

---

# Open decisions awaiting Ian / Michael / the board

| # | Decision | Owner | Blocks |
|---|---|---|---|
| 14 | Confirm the "year-and-month" date-format code (source listed YMDD twice) | Ian | 14 |
| 20 | "Years Seen" vs. Earliest/Latest date model | Board + Greg | 20, 32 |
| 21 | Rate vs. Auxiliary classification | Board | 12, 21 |
| 23 | APMC branding / logo / Society links sign-off | Board | 23 |
| 27 | Allow submitter self-delete? | Ian | 27 |
| 28 | Strip territory suffixes in `parse_head`? | Michael | 28 |
| 29 | `#N` office-name handling (allow `#` or `NO. N`) | Michael | 29 |
| 30 | Bless territory region rows into canonical `regions.csv` + DB; territory abbrevs | Michael | 30 |
| 31 | Territory search behavior (parent under successor state?) | Michael | 25, 31 |

---

# Out of scope / non-engineering follow-ups

- **Hosting cost** -- Bob / Eric Stone meeting on APMC hosting (Website ~$975/qtr, Chronicle ~$465/qtr); Eric asked for a load/cost recommendation. Business decision.
- **Billing/naming** -- confirm "Covercensus" == "Worldcovers" for tax/billing (pending Michael).
- **ASCC data-model doc** -- reference only (Google Doc linked in `06-09-emails.md` -> "ASCC database" thread).
- **IanThom.org / ChinaOverprints / account admin / Jay Logan transition** -- separate, lower-precedence track. Not tracked here.
