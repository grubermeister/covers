# WorldCovers | Developer Vocabulary

A developer-facing reference for UI primitive selection and canonical action verbs. Pair with `docs/glossary.md`, which is the user-facing domain glossary surfaced in-app per story S24. The two documents are complementary and do not overlap: if a term is meaningful to end users (Guest, Contributor, Editor, Administrator), it belongs in the glossary; if it is only meaningful to people writing or reviewing code, it belongs here.

When writing a design spec, a bug report, or a stakeholder note about the UI, reference UI elements by the names in **UI Vocabulary** below and reference actions by the verbs in **Action Verbs**. Everything else uses the domain terms from the glossary.

---

## UI Vocabulary

Selection rules for the route-level and component-level surfaces currently in use under `frontend/src/`. When a designer, stakeholder, or product writer says "popup," "modal," "side thing," "panel," "screen," or "view," map back to one of these names before responding or implementing.

The shadcn library also ships Sheet, Drawer, and AlertDialog primitives. Sheet and Drawer are not used today. AlertDialog is structurally a Dialog with a confirmation idiom, so destructive confirmations use Dialog with the canonical Remove / Discard verbs on the action button. Add a separate entry for Sheet, Drawer, or AlertDialog only when a divergent use case lands.

**Page.** A top-level route surface, addressable by URL. Examples: Search at `/search`, Record Detail at `/record/:id`, Contributor Dashboard at `/contribute`, Editor Dashboard at `/dashboard`. Use Page, not Screen -- WorldCovers is a web SPA and the codebase organizes top-level routes under `frontend/src/pages/`.

**Dashboard.** A role-specific Page that shows the logged-in user their current work: a Contributor's submissions, an Editor's review queue, an Administrator's system view. Always qualify with the role in copy (Contributor Dashboard, Editor Dashboard) so the user can tell whose dashboard they are looking at.

**Dialog.** Centered, blocking modal. The default choice for any modal interaction that takes a single focused step or a short form, including destructive-confirmation prompts. Examples in code: `CoverDialog`, `EditSubmissionDialog`.

**Card.** Container for a self-contained unit of content inside a list, grid, or summary view: an Entry preview in search results, a Collection summary on the dashboard, a Submission in the review queue. A Card should not contain another Card -- if you find yourself nesting Cards, the inner content probably belongs in a Dialog opened from the outer Card instead.

**Badge.** Compact inline label conveying status or a small categorical fact (Submission status, marking type, Cover institutional-ownership flag). Use for one to three words. If the content needs more space, surface it inline in a Card or use a Toast.

**Toast.** Transient, non-blocking notification surfaced after an asynchronous action completes. Use for outcomes the user does not need to acknowledge ("Submission received", "Collection created"). Do not use Toast for errors the user must act on -- those belong in an inline error message next to the affected control.

---

## Action Verbs

One verb per operation kind. The verb describes the workflow effect, not the caller's role. When a button, menu item, or toast names an action, the label must use one of these verbs unless there is a documented exception in the relevant feature module.

A Submission moves through this lifecycle: **Create** a new Submission, **Edit** its fields, **Save** as a draft if you need to step away, **Submit** when ready for review, then the Editor decides **Approve** / **Reject** / **Return**.

| Verb | Operation | Examples |
|---|---|---|
| Create | Bring a new item into existence in the application state, before any persistence | Create Marking, Create Cover, Create Submission |
| Edit | Open an existing item for modification, prior to Save or Submit | Edit Marking, Edit Submission |
| Save | Persist a draft or non-reviewed change without sending for review | Save Draft, Save changes (account settings) |
| Submit | Contributor sends content into the review pipeline | Submit Marking, Submit Cover, Submit Edit, Submit Comment; Submit Image attaches an image to an in-flight Submission and is not a standalone Entry submission |
| Approve | Editor decision: accept a Submission | Approve Submission |
| Reject | Editor decision: refuse a Submission | Reject Submission |
| Return | Editor decision: send a Submission back to the Contributor for changes | Return Submission |
| Remove | Editor or Admin takes published data out of the catalog | Remove Marking, Remove Cover |
| Cancel | Close a modal without committing | Cancel |
| Discard | Throw away unsaved in-progress input | Discard Changes |

### Kill-list

Patterns that have appeared in user-facing code and must be replaced when encountered. If you see one, fix it in the same PR as your current work.

| Replace                                              | With                                            | Notes                                                                                                                                                                              |
|------------------------------------------------------|-------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Submit Townmark                                      | Submit Marking                                  | Use the broader Marking term for the unified TOWNMARK/RATEMARK/AUXMARK form; reserve "Submit Entry" for multi-kind screens.                                                       |
| Submit for Approval                                  | Submit                                          | Review is implicit in Submit.                                                                                                                                                      |
| Save changes (on a create flow)                      | Submit Marking / Submit Cover / Submit Image    | It is a creation, not a save -- pick the kind-specific verb.                                                                                                                       |
| Submission sent                                      | Submission received                             | From the user's perspective.                                                                                                                                                       |
| Begin Year, End Year                                 | Earliest seen, Latest seen                      |                                                                                                                                                                                    |
| First Seen Year, Last Seen Year                      | Earliest seen, Latest seen                      |                                                                                                                                                                                    |
| Town mark, Rate mark, Auxiliary/Instructional Mark   | Townmark, Ratemark, Auxmark                     | One word, leading capital.                                                                                                                                                         |
| Listing                                              | Entry (or kind-specific Marking / Cover)        | UI text only.                                                                                                                                                                      |
| record, records                                      | Entry, Entries                                  |                                                                                                                                                                                    |
| Delete, Deleting                                     | Remove, Removing                                |                                                                                                                                                                                    |
| Request Revision                                     | Return                                          |                                                                                                                                                                                    |
