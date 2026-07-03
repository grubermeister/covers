# WorldCovers | Glossary

A reference for the terminology used in WorldCovers data, interface, and supporting documentation.

---

**Administrator.** Creates Collections, assigns Editors, performs system maintenance and reporting. Inherits Editor capabilities.

**APMC.** *American Postal Markings Catalog.* The aggregate dataset WorldCovers manages. The ASCC was the first Reference Work incorporated; the VPHC and others will follow.

**ASCC.** *American Stampless Cover Catalog.* The first Reference Work being digitized.

**Auxmark** (`AUXMARK`). An auxiliary or instructional Marking: PAID, FREE, ADVERTISED, MISSENT, FORWARDED, etc.

**Backstamp.** A Marking applied to the reverse of a Cover.

**Citation.** A link between a Reference Work and a Cover or Marking, with a detail field carrying page number, section, or URL.

**Collection.** The curatorial grouping for a Region. In the APMC, Collections are organized as Regions: every Region has its Collection, and submitting a Marking or Cover for a Region submits it into that Collection.

**Color.** Ink color of a Marking or material color of a Cover, identified by name. May also include a hexadecimal value (used for screen display) and a Pantone code (used for precise color matching). The field may be blank when the source does not state a color.

**Contributor.** Authenticated user. Can submit entries, edits, and comments. Inherits Guest capabilities.

**Cover.** A folded letter or folded cover that carries one or more recorded Markings. The Cover record holds the cover type, physical details, and date observations.

**Dates.** The earliest and latest dates recorded for a Marking, derived from two sources: dates carried directly on the Marking from the source catalog text, and the observation dates of Covers bearing the Marking. New dates are contributed only as part of Cover data.

**Editor.** Approves or rejects submissions and comments on assigned Collections, provides feedback, manages Reference Works. Inherits Contributor capabilities.

**Entry.** A unit of cataloged data: either a Marking or a Cover. Contributors propose new Entries (and edits to existing ones) via Submissions; Editors approve them for being entered into the catalog.

**Guest.** Unauthenticated user. Can browse and search Collections.

**Impression.** Optional printing technique of the handstamp: Normal, Stencil, or Negative. Not set on Manuscript Markings.

**Inscription Text.** The text as it appears on the Marking itself: what was struck or written. Often includes abbreviations and roman numerals (e.g., `RICHM'D` for Richmond, or `V` for 5). Labeled in the interface according to the Marking's type (Townmark, Ratemark, or Auxmark).

**Institutional Ownership.** A flag on a Cover indicating that it is held by an institution (museum, society, archive, etc.) rather than a private collector.

**Irregularity.** Optional flag indicating that the handstamp outline is non-uniform. Not set on Manuscript Markings.

**Lettering.** Optional typeface style observed on the handstamp: Italic, Serif, Sans-serif, Outline, Bold, Block, Gothic, Small, or Large. Not set on Manuscript Markings.

**Manuscript Marking.** Any Marking applied by hand rather than by handstamp. Set independently of Marking type: manuscript Townmarks, manuscript Ratemarks, and manuscript Auxmarks are all valid. A Manuscript Marking has no Shape.

**Marking.** Either a Townmark, Ratemark, or Auxmark, applied to a Cover (struck by handstamp or written by hand) to identify origin, state the postage rate, or convey instruction. Each Marking is recorded with its Inscription Text, dimensions, optional Color, optional Shape, and the Post Office that produced it.

**Post Office.** A postal facility identified by its town name within a Region. Every Marking belongs to exactly one Post Office.

**Ratemark** (`RATEMARK`). A Marking that states a postal rate. The Rate Value is recorded numerically in addition to the Inscription Text.

**Rate Value.** The numeric amount of postage stated by a Ratemark, recorded as a decimal value in cents. For example, a 3-cent rate is stored as 3 and shown as 3 cents.

**Reference Work.** A citable publication, such as a printed catalog, monograph, or digital resource. The ASCC and the forthcoming VPHC are among the Reference Works incorporated into the APMC.

**Region.** A structured hierarchy of named geographic or administrative jurisdictions such as "country, state, territory, county, city, or district."

**Shape.** The base geometric form of the Marking, such as Circle, Oval, Straight Line, Box, Fancy, or Pictorial. Compound ASCC codes (DC, DLC, DLDC, etc.) are carried verbatim. Not set on Manuscript Markings.

**Submission.** A proposed contribution (a new Entry or an edit) pending Editor review. Lifecycle states are draft, pending, approved, needs revision, or rejected. Editors use the Return action to move a Submission into needs revision.

**Townmark** (`TOWNMARK`). A Marking whose function is to identify the origin Post Office. Bears the town name, and frequently a date.

**VPHC.** *Virginia Postal History Catalog.* The next Reference Work scheduled for incorporation into the APMC.

---

## Source Codes

Codes carried verbatim from the ASCC.

### Shape Codes

| Code | Meaning |
|---|---|
| `C` | Circle |
| `DC` | Double Circle |
| `DLC` | Double [outer] Line Circle |
| `DLDC` | Double Line Double Circle |
| `NOR` | No Outer Rim (rimless circle) |
| `O` | Oval |
| `DO` | Double Oval |
| `DLO` | Double [outer] Line Oval |
| `DLDO` | Double Line Double Oval |
| `SL` | Straight Line (one, two, or three lines) |
| `Box` | Single Line Box or rectangle |
| `DL Box` | Double Line Box |
| `Arc` | Arc or semi-circle |
| `Pmk` | post mark/town mark (generic) |
| `CD` | Circle Date (Bishop or Franklin Mark) |

### Date Format Codes

Codes describing how the date is arranged on the Marking.

| Code | Meaning |
|---|---|
| `MD` | Month date only |
| `MDD` | Month and day date |
| `YD` | Year date only |
| `YMD` | Year, month, date |
| `YMDD` | Year, month and day date |
