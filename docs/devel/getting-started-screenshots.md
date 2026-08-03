# Screenshot capture list — `docs/getting-started.md`

Internal. Lives under `docs/devel/` so `HelpDocsView` does not publish it (`backend/common/api/help.py`).

The guide ships with each image site marked as an HTML comment (`<!-- SCREENSHOT nn: ... -->`) so
nothing renders as a broken image before the files exist. Capture the images, drop them in, then
replace each comment with its markdown tag.

## Where the files go

```
frontend/public/assets/guide/01-request-a-login.png
```

referenced from the guide as:

```markdown
![The "Request a login" link on the sign-in page](/assets/guide/01-request-a-login.png)
```

**This path is verified, not assumed.** Vite copies `frontend/public/` into `dist/` preserving
subdirectories, and Django serves `assets/<path:path>` from `FRONTEND_DIST / "assets"`
(`backend/woco/urls.py:57-62`). Everything *not* under `/assets/` falls through the SPA catch-all
(`urls.py:65-70`) and returns `index.html` instead of the image. Confirmed 2026-08-03 by building
and curling a test PNG: `200 image/png`.

## Capture conditions

- **Capture on woco.dev, signed in as a state editor.** Two of these images do not exist for
  anyone else: the `Reviewed / confirmed` checkbox and the `Review Status` filter are gated on
  editor rights (`RecordDetail.tsx:1196`, `Search.tsx:1048`). hellowoco.app runs May-24 code and
  does not have them at all.
- Use refreshed data. Do not screenshot stale records.
- No real credentials, no real email addresses, no personal data in frame. Placeholder values only.
- Crop tight to the relevant control. Full-page shots only where the guide says "whole screen".
- Consistent browser width so the images look like one set.

## The list

| # | File | What to capture | Notes |
|---|---|---|---|
| 01 | `01-request-a-login.png` | The `/auth` page, whole card, with the **Request a login** link highlighted | **The most important image in the guide.** This is the step Michael reports people keep failing. Circle or arrow the link. |
| 02 | `02-request-login-dialog.png` | The **Request Login Access** dialog, filled in | Use `Jane` / `Doe` / `jane@example.com` |
| 03 | `03-account-menu.png` | Avatar dropdown open: Dashboard / Change password / logout | Signed in as an editor, so it reads "Dashboard" not "My Submissions" |
| 04 | `04-change-password.png` | The **Change password** dialog, fields empty | Never capture a filled password field |
| 05 | `05-catalog-search.png` | Catalog screen with filters visible and a state's results loaded | Pick a state actually loaded on woco.dev that day |
| 06 | `06-record-detail.png` | A record detail page, whole screen | Choose a record with color, dates and dimensions populated |
| 07 | `07-reviewed-checkbox.png` | The **Reviewed / confirmed** checkbox on a record, unticked | Editor-only; crop to the checkbox and its surrounding card |
| 08 | `08-review-status-filter.png` | The **Review Status** filter open: All (Default) / Reviewed / Unreviewed | Editor-only |
| 09 | `09-submit-edit-button.png` | Record header showing **Submit Edit to Existing Marking** | |
| 10 | `10-submit-cover-form.png` | The Submit New Cover form: image upload area plus the three checkboxes | Must show "Would you like your name to display as the submitter?" |
| 11 | `11-dashboard.png` | The Dashboard with at least one submission visible | Use a throwaway submission, not a real contributor's |

## After capture

1. Replace each `<!-- SCREENSHOT nn: ... -->` comment in `docs/getting-started.md` with its
   markdown image tag and a real alt text.
2. `npm run build` in `frontend/`.
3. Load `/help/getting-started` and confirm every image renders.
4. `curl -I` one image and confirm `Content-Type: image/png`.
