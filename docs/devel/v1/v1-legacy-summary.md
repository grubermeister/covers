# WorldCovers | v1 Legacy System Summary

This document describes the v1 data model as it actually exists -- the ColdFusion/worldcovers.org schema that preceded the current WorldCovers platform. It is a factual reference for developers and agents who need to understand what they are looking at when they encounter v1 artifacts (CSV exports, column names, parent/child semantics). The authoritative source is `data_model-v1.erd`; this document annotates it.

---

## 1. Platform

Adobe ColdFusion web application, backed by a relational database (exported as CSVs). Retired due to shrinking ColdFusion talent pool, hosting constraints, and inability to extend beyond single-catalog ASCC data. The USPCS sponsors the replacement.

---

## 2. Table Inventory

The v1 schema comprises 10 tables in four functional groups.

### 2.1 Core Reference Tables

**`tblStates`** -- One row per US state/territory. Scopes everything else.

| Column | Type | Notes |
|---|---|---|
| `nStateID` | int PK | |
| `txtState` | string | Full name |
| `txtStateAbv` | string | Two-letter abbreviation |
| `txtFilename` | string | Associated file path |
| `txtPDF` | string | PDF source reference |
| `nMarkings` | int | Denormalized count of markings in this state |
| `ynCompleted` | bool | Whether state processing is finished |

**`tblAbbreviations`** -- Glossary of ASCC abbreviations (~50 entries).

| Column | Type |
|---|---|
| `ID` | int PK |
| `txtAbbreviation` | string |
| `txtMeaning` | string |
| `nOrder` | int |
| `ynActive` | bool |

### 2.2 Classification Lookup Tables

Five small tables with identical structure: integer PK, text value, memo/description, display order, active flag. Connected to `tblRawStateData` by **text-value match** (not FK integer).

| Table | PK Column | Value Column | Cardinality | What It Classifies |
|---|---|---|---|---|
| `tblTownmarkLettering` | `nTownmarkLetteringID` | `txtTownmarkLettering` | ~10 | Typeface style of the device inscription |
| `tblTownmarkFraming` | `nTownmarkFramingID` | `txtTownmarkFraming` | ~10 | Border/frame treatment |
| `tblTownmarkDateFormat` | `nTownmarkDateFormatID` | `txtTownmarkDateFormat` | 5 | Date component arrangement (MD, MDD, YMD, YMDD, YD) |
| `tblTownmarkRateLocation` | `nTownmarkRateLocationID` | `txtTownmarkRateLocation` | 3-4 | Where the rate appears relative to the townmark |
| `tblTownmarkRateValue` | `nTownmarkRateValueID` | `txtTownmarkRateValue` (float) | ~40 | Numeric postal rate amounts |

Each has a `mem*` description column, `nOrder`, and `ynActive`. Explorer analysis confirmed >95% coverage of actual field values by these lookup tables.

### 2.3 The Core Record: `tblRawStateData`

This is the v1 system's central and dominant table. **~51,600 rows, 62 columns.** Each row is one ASCC catalog listing -- conceptually one townmark device at a post office. The ERD shows a trimmed 24-column view; the full CSV export has 62 columns. Every column is listed below, grouped by function.

#### Identity & Hierarchy

| Column | Type | Notes |
|---|---|---|
| `nRawStateDataID` | int PK | Unique record identifier |
| `nRawStateDataID_parent` | int FK (self) | Points to parent record for relationship-indicator grouping |
| `nGroupOrder` | int | Ordering position within a parent/child family |
| `nStateID` | int FK -> `tblStates` | State scope |
| `txtPublishedID` | string | ASCC listing key (e.g. "VA-0042"). Links to `tblCovers` by text match |
| `nOrder` | int | Display ordering |

#### Raw Source Text

| Column | Type | Notes |
|---|---|---|
| `txtRawStateData` | string | **The verbatim ASCC catalog entry text.** The single most important column -- all parsed fields derive from this. Example: `ABINGDON/VA.("A"high)(1843-48;29;Blue,Red) 20` |
| `txtRawStateDataTemp` | string | ~83% populated. A **semicolon-delimited reformatting** of the same record's `txtRawStateData`: town/inscription portion is separated by semicolon from the parenthesized data block, extra semicolons are inserted as field delimiters within that block, and the valuation amount is stripped. It is _not_ rolled-up family text -- each row's Temp corresponds only to its own Raw. Example: Raw = `Nfk(Norfolk)(Nov. 20, 1772;Ms;Red) 1,000` -> Temp = `Nfk(Norfolk);(Nov. 20, 1772;Ms;;Red)` |
| `txtWorkingData` | string | ~55% populated. Scratch column with mixed content: date fragments, relationship indicator text, partial catalog entries. No consistent structure |

#### Layer 1 -- Parsed Fields (high population, extracted from `txtRawStateData`)

| Column | Type | Population | Notes |
|---|---|---|---|
| `txtPostmark` | string | ~83% | The townmark inscription text, parsed from the raw entry |
| `txtDatesSeen` | string | ~82% | Free-text date range as printed in ASCC (e.g. "1843-48", "May 21, 1772", "1827-34;1845-53") |
| `txtSizes` | string | ~53% | Dimension string in ASCC notation (e.g. "C-30", "SL-29x4", "box-30x8") |
| `txtColors` | string | ~45% | Comma-separated ink colors (e.g. "Blue,Red", "Black") |
| `txtRates` | string | ~4% | Layer 2 analyst interpretation of rate text (almost always empty) |
| `txtRatesText` | string | ~40% | ASCC rate notation parsed from raw text (e.g. "PAID/3[C]", "PAID,5,10") |
| `txtValue` | string | ~75% | Slash-separated catalog valuations (e.g. "20", "75/25.00", "--") |
| `txtTerritory` | string | ~95% | State/territory abbreviation as printed in the catalog |
| `txtTown` | string | ~95% | Normalised town name |
| `txtTownPostmark` | string | ~83% | Town name as it appears in the postmark inscription |
| `txtOther` | string | low | Miscellaneous parsed text |

#### Layer 2 -- Classified Fields (low population, manually entered by analysts)

| Column | Type | Population | Notes |
|---|---|---|---|
| `txtTownmarkShape` | string | ~30% | Shape classification, text-matched to no lookup table (free text) |
| `txtTownmarkLettering` | string | ~30% | Text-matched to `tblTownmarkLettering` |
| `txtTownmarkDateFormat` | string | ~30% | Text-matched to `tblTownmarkDateFormat` |
| `txtTownmarkFraming` | string | ~30% | Text-matched to `tblTownmarkFraming` |
| `txtTownmarkRateLocation` | string | ~9% | Text-matched to `tblTownmarkRateLocation` |
| `txtTownmarkRateText` | string | ~9% | Analyst description of the rate marking |
| `txtTownmarkRateValue` | string | ~9% | Text-matched to `tblTownmarkRateValue` |
| `txtTownmarkColor` | string | ~9% | Analyst-assigned color for a specific variant |

The Layer 1 vs Layer 2 distinction is a critical finding from the explorer notebook: Layer 1 fields were populated during bulk ASCC transcription and are 5-10x more populated than their Layer 2 counterparts, which required manual analyst classification that was never completed for most records.

#### Date Components (manually parsed from `txtDatesSeen`)

| Column | Type | Notes |
|---|---|---|
| `nEarliestUseDay` | float | Day component; 0 = unknown |
| `txtEarliestUseMonth` | string | Month name/abbreviation |
| `txtEarliestUseYear` | string | Year as text |
| `nEarliestUseYear` | float | Year as number |
| `nLatestUseDay` | float | |
| `txtLatestUseMonth` | string | |
| `nLatestUseYear` | float | |
| `txtLatestUseYear` | string | |
| `ynEarliestKnownDate` | int | Flag: is this the earliest known use? |
| `ynLatestKnownDate` | int | Flag: is this the latest known use? |

These six+ columns are a manual decomposition of `txtDatesSeen` into structured components. Explorer analysis found ~95% agreement between the manually parsed values and what can be algorithmically extracted from `txtDatesSeen`.

#### Physical Dimensions

| Column | Type | Notes |
|---|---|---|
| `nWidth` | float | Horizontal dimension in mm |
| `nHeight` | float | Vertical dimension in mm |

#### Flags

| Column | Type | Notes |
|---|---|---|
| `ynManuscript` | int | 1 = manuscript marking (not handstamped) |
| `ynManuscriptTownmarks` | int | Duplicate/variant manuscript flag |
| `ynBackstamp` | int | 1 = marking appears on cover reverse |
| `ynTownNameHasExtra` | int | Town name contains parenthetical/qualifier |
| `ynProcessed` | int | Processing completion flag |
| `ynDeleted` | int | Soft delete |
| `ynForReview` | int | Flagged for editorial review |

#### Images

| Column | Type | Notes |
|---|---|---|
| `nImageCount` | int | Denormalized count of images in `tblTownmarkImages` (477 records have mismatched counts) |
| `txtDefaultImage` | string | Filename of the designated display image |
| `txtPDFPage` | float | Page number reference to the source PDF |

#### Workflow & Audit

| Column | Type | Notes |
|---|---|---|
| `approve_status` | string | Approval state |
| `request_status` | string | Submission state |
| `submitterId` | int | External user reference |
| `approverId` | int | External user reference |
| `txtUserEmail` | string | |
| `ynEmailCheck` | float | |
| `txtReasonForReview` | string | |
| `txtMarkedBy` | string | |
| `dtMarkedForReview` | date | |
| `memNotes` | string | Freetext editorial notes |
| `dtEntered` | date | Record creation timestamp |
| `dtUpdated` | date | Last modification timestamp |

### 2.4 Pending Updates: `tblRawStateData_PendingUpdate`

Shadow copy of `tblRawStateData` holding proposed edits awaiting approval. Identical column set as the ERD's trimmed view of `tblRawStateData`, plus `ynForReview` and `txtUserEmail`. Each row references the `nRawStateDataID` it proposes to modify.

### 2.5 Images: `tblTownmarkImages`

| Column | Type | Notes |
|---|---|---|
| `nTownmarkImageID` | int PK | |
| `nRawStateDataID` | int FK | Links to the catalog record |
| `txtFilename` | string | Image file path |
| `txtView` | string | View classification |
| `nX`, `nY` | int | Crop origin coordinates |
| `nWidth`, `nHeight` | int | Crop dimensions |
| `imageStatus` | string | |
| `submitterName` | string | |
| `submitterEmail` | string | |
| `ynEmailCheck` | bool | |

### 2.6 Process & Permissions

**`tblParseSteps`** -- Per-state processing checklist.

| Column | Type |
|---|---|
| `nParseStepID` | int PK |
| `txtParseStep` | string |
| `nStateID` | int FK -> `tblStates` |
| `ynCompleted` | bool |
| `nOrder` | int |
| `ynActive` | bool |

**`ctUserStates`** -- Maps external user IDs to states with role assignments.

| Column | Type |
|---|---|
| `ID` | int PK |
| `nUserID` | int (external) |
| `nStateID` | int FK -> `tblStates` |
| `memRoles` | string |

### 2.7 Covers: `tblCovers`

User-entered cover observations, submitted through the worldcovers.org interface. **Not** populated from the ASCC bulk import. Linked to `tblRawStateData` loosely via `txtPublishedID` text match (dotted relationship in the ERD -- no FK constraint).

| Column | Type | Notes |
|---|---|---|
| `nCoverID` | int PK | |
| `nUserID` | int | External user reference |
| `txtCoverKeyID` | string | |
| `txtStateAbv` | string | State abbreviation |
| `txtTerritory` | string | |
| `txtTown` | string | |
| `txtTownmarkShape` | string | Duplicates classification fields from `tblRawStateData` |
| `txtLettering` | string | |
| `txtTownmarkFraming` | string | |
| `txtDateFormat` | string | |
| `txtRate` | string | |
| `txtRateText` | string | |
| `txtSecondRate` | string | A second rate marking -- no equivalent in `tblRawStateData` |
| `nWidth`, `nHeight` | float | |
| `txtColor` | string | Singular (not comma-separated like `txtColors`) |
| `nEarliestUseDay/Month/Year` | int | Structured date components (ints, not the mixed types in `tblRawStateData`) |
| `nLatestUseDay/Month/Year` | int | |
| `memASCCText` | string | Copy of the ASCC catalog text |
| `memNotes` | string | |
| `memOtherChar` | string | |
| `nEstimatedValue` | float | Dollar valuation |
| `txtPublishedID` | string | Text link to `tblRawStateData` |
| `txtImage1`, `txtImage2` | string | Direct filename references (not via `tblTownmarkImages`) |

---

## 3. Relationships

From the ERD's relationship section, verbatim:

| Relationship | Type | Mechanism |
|---|---|---|
| `tblStates` -> `tblRawStateData` | one-to-many | `nStateID` FK |
| `tblStates` -> `tblRawStateData_PendingUpdate` | one-to-many | `nStateID` FK |
| `tblStates` -> `tblParseSteps` | one-to-many | `nStateID` FK |
| `tblStates` -> `ctUserStates` | one-to-many | `nStateID` FK |
| `tblStates` -> `tblCovers` | one-to-many | implicit (via `txtStateAbv` text) |
| `tblRawStateData` -> `tblRawStateData` | self-referencing one-to-many | `nRawStateDataID_parent` FK |
| `tblRawStateData` -> `tblTownmarkImages` | one-to-many | `nRawStateDataID` FK |
| `tblRawStateData` -> `tblRawStateData_PendingUpdate` | one-to-many | `nRawStateDataID` FK |
| `tblRawStateData` <-> `tblCovers` | **dotted** (logical, not enforced) | `txtPublishedID` text match |
| Lookup tables <-> `tblRawStateData` | **dotted** (logical, not enforced) | Text-value match on corresponding `txtTownmark*` column |

Solid lines (`||--o{`) are real FKs. Dotted lines (`||..o{`) are text-match joins with no referential integrity.

---

## 4. Parent/Child Grouping Semantics

The ASCC catalog uses **relationship indicators** to compress repeated information:

- **`Same`** -- this listing inherits town, shape, and other physical properties from the immediately preceding entry; only the differing attributes (color, date, rate, valuation) are printed.
- **`(E)`** -- earliest known use of the device described by the preceding entry.
- **`(L)`** -- latest known use.

In v1, these are represented by the self-referencing `nRawStateDataID_parent` FK. A parent record is a full ASCC listing; its children are the `Same`/`(E)`/`(L)` variants. `nGroupOrder` controls display sequence within the family.

The child's `txtRawStateData` typically contains only the variant-specific data (e.g. `Same(July 25, 1775;Black) 1,200`). Town name, shape, and other inherited properties were **manually copied** from parent to child during data entry -- the child's raw text does not contain this information. Explorer analysis validated this with ~95% match rates for `txtTown` and `txtTownmarkShape` between parent and child records.

Note: `txtRawStateDataTemp` is sometimes mistaken for rolled-up family text. It is not -- it is a semicolon-delimited reformatting of the **same record's** `txtRawStateData` (see Section 2.3 above). Family-level text aggregation is a v2 pipeline operation, not a v1 schema feature.

---

## 5. What v1 Structurally Cannot Represent

These are not missing features but **structural limitations** of the flat-record design:

1. **Independent ratemarks and auxmarks.** Everything hangs off the townmark record. A ratemark or auxmark has no row of its own -- it exists only as sub-fields (`txtRatesText`, `txtTownmarkRateValue`, `txtTownmarkRateLocation`) on a townmark row. You cannot query "all PAID markings" without parsing text.

2. **Multiple markings on one cover.** `tblCovers` has one set of townmark fields. A cover bearing markings from two different towns, or a townmark plus an unrelated auxmark, cannot be structurally represented.

3. **Normalised dates.** Dates are either free text (`txtDatesSeen`) or six manually-parsed component columns. There is no separate date-observation entity, no granularity metadata, and no way to record multiple discrete date spans for one record without encoding them in the free text (which ~10% of records do -- the "MULTI_SPAN" pattern class from explorer analysis).

4. **Post office as entity.** Town names are free-text strings. Two records for "Richmond" in different states have no structural link and no way to track jurisdictional changes.

5. **Color as entity.** Multi-color entries (e.g. `txtColors` = "Black,Blue,Red") represent distinct color variants of the same device. They are not decomposed into separate records -- the single row covers all variants.

6. **Valuations as structured data.** `txtValue` holds slash-separated strings where position encodes date-period tier (earliest/middle/latest). `--` means unpriced. None of this is structurally encoded.

---

## 6. Key Statistics from Explorer Analysis

The explorer notebook (`tools/apmc_data_explorer.ipynb`) was run against a 52,046-row export; the current project CSV (`tblRawStateData.csv`) contains 51,632 rows (414 fewer -- likely deleted/filtered records between exports). The statistics below are from the notebook's executed outputs and should be understood as approximate:

- **51,632 rows** in the current project CSV, 62 columns
- **Layer 1 fields** (parsed from ASCC text): 45-95% populated
- **Layer 2 fields** (`txtTownmark*` classifications): 9-30% populated; Layer 1 is 5-10x more populated
- **Lookup table coverage**: >95% of actual classified values are present in the lookup tables
- **Parent/child records**: 13,935 child records (where `nRawStateDataID` != `nRawStateDataID_parent`)
- **Parent/child match rates**: ~95% for `txtTown`, ~95% for `txtTownmarkShape` between child and parent
- **Date field agreement**: ~95% between manually-parsed numeric date columns and algorithmically-extracted values from `txtDatesSeen`
- **Image count mismatches**: 477 records where `nImageCount` disagrees with the actual count in `tblTownmarkImages` (all cases: actual > declared)
- **Multi-span dates**: ~10% of `txtDatesSeen` values contain multiple discrete date ranges
- **`txtDatesSeen` pattern distribution**: 35% year-range, 35% year-only, 11% exact date, 10% multi-span, 9% other, <1% month-year
- **`txtRawStateDataTemp`**: 83% populated (42,870 / 51,632); semicolon-reformatted per-record text, not family aggregation
- **`txtWorkingData`**: 55% populated (28,147 / 51,632); mixed scratch content

---

*Source: `data_model-v1.erd`, `tools/apmc_data_explorer.ipynb`, `tblRawStateData.csv` (62-column export, 51,632 rows). Statistics from the notebook were run against a slightly larger export (52,046 rows); figures are directionally accurate.*
