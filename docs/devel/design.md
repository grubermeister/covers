# WorldCovers | Design

## Summary

*WorldCovers* is a software system, not a single utility.  This document defines four user roles spanning capability from unauthenticated browsing through to system maintenance and administration. Forty-nine stories describe the system's user-facing capabilities. Thirty-two are mapped across eleven features: authentication, collection discovery, submission workflow, comment workflow, image attachments, reference work management, collection administration, audit trail, documentation & help, system maintenance, and catalog data pipeline. Seventeen additional stories are documented as backlog items for future iterations. The role model is cumulative - each tier inherits the capabilities of those below it. Collection Entries require Editor review before publication. Comments and submissions follow the same basic workflow. All submissions are logged as system transactions.

## Roles

**R1** - *Guest:* Unauthenticated user. Can browse and search collections.

**R2** - *Contributor:* Authenticated user. Submits Entries, edits, and comments on Collections. Inherits R1 capabilities.

**R3** - *Editor:* Approves or rejects submissions and comments on assigned Collections. Provides feedback on decisions. Manages reference works. Inherits R2 capabilities.

**R4** - *Administrator:* Creates Collections, assigns Editors, performs system maintenance operations and data reporting. Inherits R3 capabilities.

## Stories

**S1** - As a Contributor, I want to authenticate with the system, so that *my identity is established for submissions.*

**S2** - As a Guest, I want to browse collections, so that I can *explore postal marking and cover Entries without an account.*

**S3** - As a Guest, I want to search across collections, so that I can *find specific postal markings or covers of interest.*

**S4** - As a Contributor, I want API responses in a structured, documented representation, so that *external systems can consume collection data reliably.*

**S5** - As a Contributor, I want to export an individual Entry as DOCX or PDF, and a list of Entries as PDF or XLSX, so that I can *share or archive collection data in formats used outside the system.*

**S6** - As a Contributor, I want to submit new Entries to a Collection, so that I can *contribute data for expert review.*

**S7** - As a Contributor, I want to submit suggested edits to existing Entries in a Collection, so that I can *propose corrections or additions for expert review.*

**S8** - As a Contributor, I want to save an in-progress submission as a draft, so that I can *set it aside and finish it later without losing work or submitting prematurely.*

**S9** - As a Contributor, I want to view the status of my submissions, so that I *know whether my contributions are pending, approved, or rejected.*

**S10** - As a Contributor, I want to view Editor feedback on my submissions, so that I can *address issues and resubmit a contribution.*

**S11** - As an Editor, I want to view pending submissions on my assigned Collections, so that I can *review what needs attention.*

**S12** - As an Editor, I want to filter and sort my submission queue by status, date, location, and contributor, so that I can *organize and prioritize my review work.*

**S13** - As an Editor, I want to approve or reject a submission, so that *Collection quality is maintained through expert review.*

**S14** - As an Editor, I want to provide feedback when approving or rejecting a submission, so that contributors *understand the reasoning and improve resubmissions.*

**S15** - As an Editor, I want to request revisions on a submission with specific feedback, so that *contributors can correct issues and resubmit without starting over.*

**S16** - As a Contributor, I want to submit comments on an Entry, so that I can *contribute observations or corrections for expert review.*

**S17** - As an Editor, I want to approve or reject submitted comments, so that *published commentary maintains the same quality standards as Entry data.*

**S18** - As a Contributor, I want to attach images to a submission, so that *visual evidence of each cover accompanies its cataloged data.*

**S19** - As an Editor, I want to add and edit reference works, so that contributors can *cite established catalogs and publications when documenting Entries.*

**S20** - As an Administrator, I want to create new Collections, so that *catalogs or regional datasets can be organized in the system.*

**S21** - As an Administrator, I want to assign Editors to Collections, so that *subject-matter experts can curate the collections they're qualified for.*

**S22** - As an Administrator, I want every submission to be logged as a system transaction, so that *there is an audit trail of all contributions.*

**S23** - As an Editor, I want to view version history for any Entry, so that *changes can be traced for scholarly accountability and recovery.*

**S24** - As a Guest, I want to read in-app documentation articles, including a system glossary and an FAQ, so that I can *understand system concepts, terminology, and common questions without an account.*

**S25** - As an Administrator, I want to back up the system database through the application, so that I can *protect data without requiring infrastructure access.*

**S26** - As an Administrator, I want to restore the system from a backup through the application, so that I can *recover from data issues without requiring infrastructure access.*

**S27** - As an Administrator, I want to apply system updates through the application, so that I can *keep the platform current without requiring infrastructure access.*

**S28** - As an Administrator, I want to invoke exploratory queries against catalog datasets, so that I can *assess data quality.*

**S29** - As an Administrator, I want to analyze catalog data with statistical summarization, so that I can *identify normalization requirements across inconsistent source formats.*

**S30** - As an Administrator, I want to convert catalog data from source formats into the system's target representation, so that *legacy and external catalog data can be prepared for import.*

**S31** - As an Administrator, I want to import converted data into a running system, so that *prepared catalog Entries become available to users.*

**S32** - As an Administrator, I want to bundle transformed data for export, so that *prepared datasets can be distributed or archived outside the system.*

### Backlog Stories

*The following stories are documented for future iterations. They are not mapped to Features and do not constrain the current design.*

**S33** - As a Guest, I want tooltip help on textual elements throughout the interface, so that I can *get concise, context-sensitive guidance while working without leaving my current task.*

**S34** - As an Administrator, I want to add and edit documentation articles, so that *the help library remains current and accurate as the system evolves.*

**S35** - As an Editor, I want to approve multiple submissions in a single operation, so that *high-volume review periods can be processed efficiently.*

**S36** - As a Contributor, I want to configure which notifications I receive and by what method (email, text, in-app), so that I am *informed of submission decisions, feedback, and system events without unwanted interruption.*

**S37** - As an Editor, I want to escalate a submission or contributor issue to an Administrator, so that *disputes, suspected fraud, or complex edge cases are resolved by the appropriate authority.*

**S38** - As a Contributor, I want to apply and search by flexible tags on Entries, so that *Entries can be classified and discovered beyond fixed catalog fields.*

**S39** - As a Contributor, I want to rate submission quality, contributor and editor profiles, and comment helpfulness, so that *the community can identify high-quality contributions and trusted community participants.*

**S40** - As a Contributor, I want result lists to use infinite scrolling, so that I can *browse continuously without interacting with pagination controls.*

**S41** - As a Guest, I want configurable pagination on result lists, so that I can *control page size and navigate results predictably.*

**S42** - As a Contributor, I want to submit comments on a collection, so that I can *contribute observations or corrections scoped to the collection as a whole for expert review.*

**S43** - As an Editor, I want to export a set of Entries as a typeset document ready for publication, so that *curated selections can be prepared for print or formal distribution without external layout work.*

**S44** - As a Guest, I want to search collections by submitting an image, so that I can *find Entries visually similar to a cover I am investigating without needing to describe it in text.*

**S45** - As a Guest, I want to submit a bug report from within the app, so that I can *flag defects or unexpected behavior without leaving the system or needing external credentials.*

**S46** - As a Contributor, I want to authenticate using a supported SSO provider, so that I can *access the system with an existing identity without managing separate credentials.*

**S47** - As a Contributor, I want to enable multi-factor authentication on my account, so that *my identity is protected against credential compromise.*

**S48** - As a Contributor, I want advanced search with boolean operations across selectable fields, so that I can *compose precise, multi-criterion queries against collection data beyond basic keyword search.*

**S49** - As a Contributor, I want to add and edit Entries in my Personal Collection, so that I can *catalog and curate my own covers in the system.* (Note: a Personal Collection is conceptually distinct from the institutional Collection defined in model.md, which is region-scoped and editor-curated; implementing this story would require a separate model concept.)

## Technical Constraints

* **API-First Architecture:** The REST API is the system's primary interface. The provided frontend application is an API client with no privileged access. All capabilities available through the frontend are equally available to any API consumer authenticated with the appropriate role.  
* **Backend Server:** Python, Django, MySQL.   
* **Frontend Application:** TypeScript, React.   
* **Data Science Tooling:** Jupyter notebooks, not exposed through the application interface.

## Features

**F1** - *Authentication* (S1): System identity establishment for interactive and API access.

**F2** - *Collection Discovery* (S2, S3, S4, S5): Browsing, searching, structured data access, and document/spreadsheet export across collections.

**F3** - *Submission Workflow* (S6, S7, S8, S9, S10, S11, S12, S13, S14, S15): Contributor submissions, draft saving, Editor review cycle with feedback and revision requests, and queue filtering and sorting.

**F4** - *Comment Workflow* (S16, S17): Contributor comments on Entries, with Editor review.

**F5** - *Image Attachments* (S18): Image attachment on Entries.

**F6** - *Reference Work Management* (S19): Registry of citable sources available for citation linking.

**F7** - *Collection Administration* (S20, S21): Collection lifecycle and Editor assignment.

**F8** - *Audit Trail* (S22, S23): Submission transaction logging and version history viewing.

**F9** - *Documentation & Help* (S24): In-app documentation library, including system glossary and an FAQ.

**F10** - *System Maintenance* (S25, S26, S27): Administrator-facing backup, restore, and update operations exposed through the application interface.

**F11** - *Catalog Data Pipeline* (S28, S29, S30, S31, S32): Offline tooling for exploratory analysis, normalization, format conversion, and import/export of catalog datasets.

This document is pure specification: it says what the system should do,
not what is built. For the current implementation status of each feature
(which surface implements it, what is partial or pending), see
[ISSUE.md](../../ISSUE.md) at the repo root.

## Story-to-Feature Reference Map

| Story | Story Name | Feature | Feature Name |
| ----- | ----- | ----- | ----- |
| S1 | Authenticate with the system | F1 | Authentication |
| S2 | Browse collections | F2 | Collection Discovery |
| S3 | Search across collections | F2 | Collection Discovery |
| S4 | Structured, documented API responses | F2 | Collection Discovery |
| S5 | Export Entries or lists in commonly supported formats | F2 | Collection Discovery |
| S6 | Submit new Entries to a Collection | F3 | Submission Workflow |
| S7 | Submit suggested edits to existing Entries | F3 | Submission Workflow |
| S8 | Save in-progress submissions as drafts | F3 | Submission Workflow |
| S9 | View submission status | F3 | Submission Workflow |
| S10 | View Editor feedback on submissions | F3 | Submission Workflow |
| S11 | View pending submissions | F3 | Submission Workflow |
| S12 | Filter and sort submission queue | F3 | Submission Workflow |
| S13 | Approve or reject a submission | F3 | Submission Workflow |
| S14 | Provide feedback on submissions | F3 | Submission Workflow |
| S15 | Request revisions on a submission | F3 | Submission Workflow |
| S16 | Submit comments on an Entry | F4 | Comment Workflow |
| S17 | Approve or reject submitted comments | F4 | Comment Workflow |
| S18 | Attach images to a submission | F5 | Image Attachments |
| S19 | Add and edit reference works | F6 | Reference Work Management |
| S20 | Create Collections | F7 | Collection Administration |
| S21 | Assign Editors to Collections | F7 | Collection Administration |
| S22 | Log submissions as system transactions | F8 | Audit Trail |
| S23 | View version history for any Entry | F8 | Audit Trail |
| S24 | Read documentation articles (glossary, FAQ) | F9 | Documentation & Help |
| S25 | Back up system database | F10 | System Maintenance |
| S26 | Restore system from backup | F10 | System Maintenance |
| S27 | Apply system updates | F10 | System Maintenance |
| S28 | Run exploratory queries against catalog datasets | F11 | Catalog Data Pipeline |
| S29 | Analyze catalog data with statistical summarization | F11 | Catalog Data Pipeline |
| S30 | Transform catalog data from source formats | F11 | Catalog Data Pipeline |
| S31 | Load transformed data into a running system | F11 | Catalog Data Pipeline |
| S32 | Bundle transformed data for export | F11 | Catalog Data Pipeline |
| S33 | Tooltip help on textual elements | - | Backlog |
| S34 | Add and edit documentation articles | - | Backlog |
| S35 | Bulk approve submissions | - | Backlog |
| S36 | Configure notification methods and triggers | - | Backlog |
| S37 | Escalate submission or contributor issue | - | Backlog |
| S38 | Apply and search by flexible tags | - | Backlog |
| S39 | Rate submissions, profiles, and comments | - | Backlog |
| S40 | Infinite scrolling on result lists | - | Backlog |
| S41 | Configurable pagination on result lists | - | Backlog |
| S42 | Submit comments on a collection | - | Backlog |
| S43 | Export Entry sets as typeset documents ready for publication | - | Backlog |
| S44 | Search collections by image | - | Backlog |
| S45 | Submit a bug report from within the app | - | Backlog |
| S46 | Authenticate via SSO | - | Backlog |
| S47 | Enable multi-factor authentication | - | Backlog |
| S48 | Advanced search with boolean operations on selectable fields | - | Backlog |
| S49 | Add and edit Entries in a Personal Collection | - | Backlog |
