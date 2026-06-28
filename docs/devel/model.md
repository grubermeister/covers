---

# WorldCovers **|** Model

---

# **Summary**

This document defines the structural vocabulary for data accessible through WorldCovers. Fifteen tables describe the philatelic domain's persistent state. markings is the central entity \- the catalog entry itself \- unifying town markings, rate markings, and auxiliary markings under a single type discriminator. Each row in markings carries the authoritative catalog text, the physical inscription of the device, and a reference to a row in post\_offices, whose jurisdictional history is recorded in post\_office\_regions against a time-bounded regions hierarchy. covers are conceptually observations of markings, linked through the cover\_markings junction, which also records per-observation positional context. Marking classification is represented through two primary editorial dimensions: shapes and letterings. Both remain provisional editorial vocabularies: their current records preserve catalog usage patterns and known inconsistencies, and therefore do not yet constitute fully orthogonal or exhaustively normalized taxonomies. Curatorial responsibility is expressed through collections, each of which wraps exactly one region and serves as the routing target for contributions submitted within that region. Two junction tables resolve the document's many-to-many associations: cover\_markings (covers to markings) and post\_office\_regions (post offices to regions). The latter exists because a post office is a fixed geographic place whose political jurisdiction can change over time; a marking's effective region context is derived by intersecting the post office's region associations with the marking's aggregated dates\_seen (both those attached directly to the marking and those attached to its associated covers). System-internal tables (submissions, comments, audit log, and role assignments) are intentionally not modeled in this document; they live alongside the domain tables in `backend/common/`.

---

# **Domain Tables**

### citations

Links a reference work to a cover or marking.

*Fields:*

* citation\_detail \- Specific location within the reference work (e.g., page number, section, url).  
* reference\_work\_id \- Related reference work.  
* subject\_id \- Identifier of the cited resource.  
* subject\_type \- Type of the cited resource.

*Invariants:*

* reference\_work\_id references exactly one row in reference\_works.  
* subject\_type is one of COVER, or MARKING.  
* subject\_id references exactly one resource of the type specified by subject\_type.

*Relationships:*

* References exactly one reference work.  
* Targets exactly one cover or marking.

### collections

An institutional curatorial unit associated with exactly one region. Contributions submitted within a collection's region are routed to that collection for editorial review. A collection is the unit of curatorial scope: it carries the human-facing identity (display name, description, active state) under which a region's holdings are presented and reviewed, independent of who is currently assigned to work it.

*Fields:*

* description \- Curatorial description of the collection.  
* is\_active \- Whether the collection is currently accepting submissions and editorial work.  
* name \- Display name for the collection (e.g., "Virginia").  
* region\_id \- Related region; one collection per region.

*Invariants:*

* name is non-empty.  
* region\_id references exactly one row in regions.  
* region\_id is unique across all collections (one-to-one with regions). v2 realizes the vision-doc multi-catalog goal via this region axis only: historical eras are expressed through the time-bounded regions hierarchy, and specialty-axis catalogs are out of v2 scope.  
* is\_active defaults to true.

*Relationships:*

* References exactly one region (one-to-one).

### colors

Value table of ink or cover material colors.

*Fields:*

* hex\_val (nullable) \- Hexadecimal color code for display rendering.  
* name \- Display name of the color.  
* pantone\_code (nullable) \- Pantone reference code for precise color matching.

*Seed values:*

* BLACK  
* BLUE  
* RED  
* GREEN  
* BROWN  
* ORANGE  
* PURPLE  
* MAGENTA  
* VIOLET

*Invariants:*

* name is unique across all rows in colors.  
* hex\_val defaults to "#FFFFFF".  
* BLACK is seeded as id 1 and is the default for markings.color\_id; the canonical color rows are supplied by the import pipeline, so a bare database has none.

*Relationships:*

* Referenced by zero or more rows in markings and covers.

### covers

A physical postal cover bearing one or more recorded markings. A cover is conceptually an observation of the markings it bears; each cover records a single-instance date and physical context for the markings it carries.

*Fields:*

* code \- An editor-assigned reference identifier.  
* color\_id \- Ink or material color of the cover itself.  
* type (nullable) \- Physical form of the postal cover.  
* has\_adhesive \- Whether the cover bears an adhesive postage stamp alongside stampless markings.  
* height (nullable) \- Vertical dimension of the cover.  
* is\_institutional \- Whether the cover is institutionally owned (museum, society, etc.).  
* width (nullable) \- Horizontal dimension of the cover.

*Invariants:*

* color\_id references zero or one row in colors; it is nullable with no default.  
* width and height are decimals in millimeters.  
* has\_adhesive defaults to false.  
* type, if set, is one of: "FC \- FOLDED COVER", or "FL \- FOLDED LETTER".

*Relationships:*

* Associated with one or more markings (via cover\_markings).  
* Has zero or more dates\_seen entries.  
* Has zero or more cover\_valuations entries.  
* References zero or one color.  
* Referenced by zero or more citations.

### cover\_markings

Junction linking a cover to a marking, with positional context describing how the marking appears on that particular cover.

*Fields:*

* cover\_id \- Related cover.  
* is\_backstamp \- Whether this marking appears on the reverse of the cover.  
* marking\_id \- Related marking.  
* placement (nullable) \- Positional qualifier for the marking's location on the cover.

*Invariants:*

* cover\_id references exactly one row in covers.  
* marking\_id references exactly one row in markings.  
* The combination of cover\_id and marking\_id is unique.  
* is\_backstamp defaults to false.  
* placement vocabulary is editorial and not yet enumerated; values should be drawn from an agreed controlled list once established.

*Relationships:*

* References exactly one cover.  
* References exactly one marking.

### cover\_valuations

An estimated collector market value for a cover, as published in a reference source.

*Fields:*

* amt (nullable) \- Estimated collector market value.  
* appraisal\_date \- Date of the valuation source.  
* cover\_id \- Related cover.

*Invariants:*

* cover\_id references exactly one row in covers.  
* amt, if set, is a non-negative decimal in USD; a null amt indicates an unpriced catalogue entry.  
* appraisal\_date is the date (or nominal date) of the valuation source.

*Relationships:*

* Belongs to exactly one cover.

### dates\_seen

A single date point observed for either a cover or a marking. When attached to a cover, the date is anchored to a specific physical artifact. When attached directly to a marking, the date records a use of the marking that is not tied to a cover row -- for example, a catalog-attested date for a marking whose cover has not been recorded, or a documentary date drawn from a reference work.

*Fields:*

* date \- Calendar date of the observed use.  
* granularity \- Granularity of the recorded date.  
* subject\_id \- Identifier of the dated resource.  
* subject\_type \- Type of the dated resource.

*Invariants:*

* subject\_type is one of COVER, or MARKING.  
* subject\_id references exactly one resource of the type specified by subject\_type.  
* granularity is one of DAY, MONTH, or YEAR.  
* If granularity is MONTH, the day component of date is synthetic (set to 01).  
* If granularity is YEAR, the month and day components of date are synthetic (set to 01).
* The combination of subject\_type, subject\_id, date, and granularity is unique.

*Relationships:*

* Targets exactly one cover or marking.

### images

Polymorphic image metadata for a file attached to either a cover or a marking.

*Fields:*

* display\_order \- Subject-local display position for gallery ordering.
* file\_checksum \- SHA-256 checksum of the stored file.
* file\_size\_bytes \- Stored file size in bytes.
* image\_description (nullable) \- Editorial or contributor description.
* image\_height \- Pixel height.
* image\_view \- View type appropriate to the subject.
* image\_width \- Pixel width.
* is\_tracing \- Whether the image is a tracing or diagram rather than a photograph.
* mime\_type \- Stored file MIME type.
* original\_filename \- Original uploaded or source filename.
* storage\_filename \- Path below the media root.
* subject\_id \- Identifier of the imaged resource.
* subject\_type \- Type of the imaged resource.

*Invariants:*

* subject\_type is one of COVER, or MARKING.
* subject\_id references exactly one resource of the type specified by subject\_type.
* image\_view is one of FULL or DETAIL when subject\_type is MARKING.
* image\_view is one of FRONT, BACK, INTERIOR, or DETAIL when subject\_type is COVER.
* storage\_filename alone is not unique; the same file may be reused across different subjects.
* The combination of storage\_filename, subject\_type, and subject\_id is unique.

*Relationships:*

* Targets exactly one cover or marking.

### letterings

Editorial value table for textual styling assigned to a postal marking. This vocabulary is intentionally provisional: current seed values preserve catalog usage and may mix type family, weight, stroke treatment, and stylistic descriptors.

*Fields:*

* code (nullable) \- An editor-assigned reference identifier.  
* name \- Display name of the typeface/style category.

*Seed values:*

* Italic  
* Serif  
* Sans-serif  
* Small  
* Large  
* Outline  
* Bold  
* Block  
* Gothic

*Invariants:*

* name is unique across all rows in letterings.  
* lettering values are editorial assignment categories and are not guaranteed to be mutually exclusive in a strict typographic sense.

*Relationships:*

* Referenced by zero or more rows in markings.

### markings

A postal marking \-- town marking, rate marking, or auxiliary marking \-- as observed on one or more covers. A marking may be a handstamped device or a manuscript inscription. All marking types share the same physical-device vocabulary (shape, lettering, impression, dimensions, colour); the type discriminator captures functional role.

*Fields:*

* catalog\_txt \- Authoritative catalog entry text for this listing.  
* code \- An editor-assigned reference identifier.  
* color\_id \- Ink color of this marking.  
* date\_fmt (nullable) \- Arrangement of date components inscribed on the device.  
* desc (nullable) \- Freetext field for contributor to provide annotations.  
* height (nullable) \- Vertical dimension of the marking impression.  
* impression (nullable) \- Printing technique of the handstamp device.  
* inscription\_txt \- Text as physically inscribed on the marking.  
* is\_irreg (nullable) \- Whether the handstamp outline is non-uniform.  
* is\_manuscript \- Whether this is a handwritten marking rather than a handstamped device.  
* lettering\_id (nullable) \- Typeface style observed on the handstamp.  
* post\_office\_id \- Post office that produced this marking.  
* rate\_val (nullable) \- Numeric postal rate amount, where applicable.  
* shape\_id (nullable) \- Base geometric outline of the handstamp device.  
* type \- Functional classification of this marking.  
* width (nullable) \- Horizontal dimension of the marking impression.

*Invariants:*

* type is one of TOWNMARK, RATEMARK, or AUXMARK.  
* If is\_manuscript is true, lettering\_id must be null.  
* If is\_manuscript is true, shape\_id must be null.  
* If is\_manuscript is false, shape\_id is required and references exactly one row in shapes.  
* lettering\_id, if set, references exactly one row in letterings.  
* color\_id references exactly one row in colors, defaults to 1 (guaranteed to be "BLACK").  
* If is\_manuscript is true, is\_irreg must be null.  
* If is\_manuscript is false, is\_irreg is required.  
* width and height are decimals in millimeters.  
* date\_fmt, if set, is one of: MD, MDD, YD, YMD, YMDD.  
* rate\_val, if set, is a non-negative decimal representing the rate amount;  
* rate\_val may be populated for any type but is most commonly associated with RATEMARK and with integrated-rate TOWNMARK devices.  
* catalog\_txt is the authoritative ASCC catalog entry text for this listing.  
* inscription\_txt is the text as it appears on the physical marking.  
* post\_office\_id references exactly one row in post\_offices.  
* impression, if set, is one of: Normal, Stencil, Negative.  
* A marking may exist without any cover\_markings rows; covers are only created when a valuation is recorded, so catalog entries without recorded valuations have no associated cover.  
* A marking's earliest and latest use dates are derived by aggregating two sources: dates\_seen rows attached directly to the marking, and dates\_seen rows attached to covers associated with the marking (via cover\_markings). The marking row itself does not store its date range. A marking with no directly attached dates\_seen and no associated covers therefore has no derivable date range.  
* A marking's region context is derived by joining its post office to post\_office\_regions and intersecting each linked region's date window with the marking's aggregated dates\_seen (both direct and cover-mediated, as defined above). A marking may resolve to multiple regions when its observed dates span a region transition (e.g., a marking observed before and after a territory becomes a state). A marking with no derivable date range has no derivable region context.

*Relationships:*

* Associated with zero or more covers (via cover\_markings).  
* Has zero or more dates\_seen entries.  
* References zero or one shape.  
* References zero or one lettering.  
* References zero or one color.  
* Referenced by zero or more citations.  
* Belongs to exactly one post office.

### post\_offices

A postal facility identified as a fixed geographic place. Its political jurisdiction over time is recorded as a set of associations to regions in post\_office\_regions; the post office row itself does not name a single region.

*Fields:*

* code \- Editor-assigned reference identifier. Munger-generated values use `{catalog_region_code}-{serial}`, e.g. `USA-MA1-3`.
* name \- Normalized town name used for filtering and grouping.

*Invariants:*

* code, if set, is unique across all rows in post\_offices.
* name is the normalized town name (e.g., Abingdon, Richmond).  
* name is not constrained to be unique. Same-name post offices that fall within a single editor's scope (e.g., two "Princeton"s within Mercer County, NJ) are distinguished by editorial naming convention (e.g., "Princeton City", "Princeton Township") rather than by a database constraint.

*Relationships:*

* Associated with one or more regions (via post\_office\_regions).  
* Referenced by zero or more rows in markings.

### post\_office\_regions

Junction linking a post office to a region under whose jurisdiction it operated. The active window of each association is inherited from the region's established\_date and defunct\_date; the junction itself carries no temporal columns.

*Fields:*

* post\_office\_id \- Related post office.  
* region\_id \- Related region.

*Invariants:*

* post\_office\_id references exactly one row in post\_offices.  
* region\_id references exactly one row in regions.  
* The combination of post\_office\_id and region\_id is unique.  
* Temporal bounds of an association are derived from regions.established\_date / regions.defunct\_date; no per-association dates are stored.  
* A post office's regions are not constrained to be non-overlapping in time, since two regions linked to the same post office may have intentionally overlapping windows during administrative succession.

*Relationships:*

* References exactly one post office.  
* References exactly one region.

### reference\_works

A citable publication or source.

*Fields:*

* code (nullable) \- An editor-assigned reference identifier.  
* authorship \- Author(s) or editor(s) of the publication.  
* isbn (nullable) \- International Standard Book Number.  
* publication\_year \- Year of publication.  
* edition (nullable) \- Released version of publication.  
* volume (nullable) \- Identifier for a multi-volume series.  
* publisher \- Publishing entity.  
* title \- Name of the publication.  
* url (nullable) \- Web address of the publication or digital resource.

*Invariants:*

* None beyond field presence.

*Relationships:*

* Referenced by zero or more citations.

### regions

A named geographic or administrative area used to organize post offices within a historical hierarchy.

*Fields:*

* established\_date \- First date on which this region definition is considered in force.  
* defunct\_date (nullable) \- Last date on which this region definition is considered in force.  
* code \- Editor-assigned reference identifier.
* name \- Canonical region name for the applicable historical period.  
* abbrev \- Canonical two or three character abbreviation.  
* parent\_region\_id (nullable) \- Immediate containing region in the hierarchy.  
* region\_tier \- Administrative level of this region.

*Invariants:*

* region\_tier is one of COUNTRY, TERRITORY, STATE, PROVINCE, COUNTY, CITY, DISTRICT, or OTHER.  
* parent\_region\_id, if set, references exactly one row in regions.  
* A region cannot parent itself.  
* If both established\_date and defunct\_date are set, established\_date must be less than or equal to defunct\_date.  
* code, if set, is unique across all rows in regions.
* A region with a non-null defunct\_date is considered inactive. A null defunct\_date indicates the region is still considered active within the modeled historical hierarchy.  
* Region identity is historical rather than purely modern; records with the same name may exist for different periods or different parents.

*Relationships:*

* May belong to zero or one parent region.  
* May contain zero or more child regions.  
* Associated with zero or more post offices (via post\_office\_regions).  
* Referenced by zero or one collection (one-to-one).

### shapes

Editorial value table for the primary form assigned to a postal marking. This vocabulary is intentionally provisional: while many values describe base geometry, some records reflect catalog terminology that may combine geometry, motif, framing treatment, or construction style. Compound ASCC codes (e.g., DC, DLC, DLDC, DO, DLO, DLDO, NOR) are carried verbatim as rows in shapes rather than decomposed into separate shape-and-framing axes.

*Fields:*

* code (nullable) \- An editor-assigned reference identifier.  
* name \- Display name of the assigned form category.

*Invariants:*

* name is unique across all rows in shapes.  
* shape values are editorial assignment categories and are not guaranteed to be mutually exclusive in a strict taxonomic sense.

*Relationships:*

* Referenced by zero or more rows in markings.

---

# **ER Diagram**

\`\`\`mermaid

erDiagram

citations {  
int id PK  
int reference\_work\_id FK  
string subject\_type  
int subject\_id  
string citation\_detail  
}

collections {  
int id PK  
string name  
string description  
int region\_id FK  
boolean is\_active  
}

colors {  
int id PK  
string name  
string hex\_val  
string pantone\_code  
}

covers {  
int id PK  
string code
int color\_id FK  
decimal width  
decimal height  
boolean has\_adhesive  
string cover\_type  
boolean is\_institutional  
}

cover\_markings {  
int id PK  
int cover\_id FK  
int marking\_id FK  
boolean is\_backstamp  
string placement  
}

cover\_valuations {  
int id PK  
int cover\_id FK  
decimal amt  
date appraisal\_date  
}

dates\_seen {  
int id PK  
int subject\_id  
string subject\_type  
date date  
string granularity  
}

images {
int id PK
int subject\_id
string subject\_type
string original\_filename
string storage\_filename
string file\_checksum
string mime\_type
int image\_width
int image\_height
int file\_size\_bytes
string image\_view
string image\_description
boolean is\_tracing
int display\_order
}

letterings {  
int id PK  
string code
string name  
}

markings {  
int id PK  
string code  
string type  
boolean is\_manuscript string desc  
int shape\_id FK  
int lettering\_id FK  
int color\_id FK  
boolean is\_irreg  
decimal width  
decimal height  
string date\_fmt  
string catalog\_txt  
string inscription\_txt  
int post\_office\_id FK  
string impression  
decimal rate\_val  
}

post\_offices {  
int id PK  
string code
string name  
}

post\_office\_regions {  
int id PK  
int post\_office\_id FK  
int region\_id FK  
}

reference\_works {  
int id PK  
string code  
string title  
string authorship  
string edition  
string volume  
string publisher  
int publication\_year  
string isbn  
string url  
}

regions {  
int id PK  
string code
string name  
string abbrev  
string region\_tier  
int parent\_region\_id FK  
date established\_date  
date defunct\_date  
}

shapes {  
int id PK  
string code  
string name  
}

covers ||--|{ cover\_markings : "has"  
markings ||--|{ cover\_markings : "observed on"  
covers ||--o{ cover\_valuations : "valued"  
covers ||--o{ dates\_seen : "dated"  
markings ||--o{ dates\_seen : "dated"  
covers ||--o{ images : "imaged"
markings ||--o{ images : "imaged"
shapes o|--o{ markings : "classifies"  
letterings o|--o{ markings : "classifies"  
colors o|--o{ markings : "colors"  
colors o|--o{ covers : "colors"  
reference\_works ||--o{ citations : "cited in"  
covers o|--o{ citations : "referenced by"  
markings o|--o{ citations : "referenced by"  
regions o|--o{ regions : "contains"  
post\_offices ||--|{ post\_office\_regions : "associated"  
regions ||--|{ post\_office\_regions : "associated"  
post\_offices ||--o{ markings : "operates"  
regions ||--o| collections : "curated as"

\`\`\`

![][image1]

[image1]: <data:image/png;base64,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>
