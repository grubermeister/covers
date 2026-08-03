# Getting Started — A Guide for State Editors

This guide takes you from "I was asked to be a state editor" to "I am signed in and reviewing my
state's records." Follow it in order. It assumes no prior familiarity with the site.

If a term here is unfamiliar -- Marking, Townmark, Cover, Submission -- open the **Glossary** in
Help. This guide links to it rather than repeating it.

---

## The two sites, and why there are two

| Site | What it is | What you do there |
|---|---|---|
| **hellowoco.app** | The future live site. Virginia and West Virginia are on it now. | Request your login here. |
| **woco.dev** | The development site. Each new state lands here first. | Review your state here. |

Your account is created on **hellowoco.app** and then synced to **woco.dev**. The sync runs one
way only.

> **Do not create an account directly on woco.dev.** It will not carry back to hellowoco.app, and
> you will end up with an account that stops working. Start at hellowoco.app every time.

---

## Step 1 — Request your login

This is the step that trips people up most often, because the sign-in page shows you a **Sign In**
form first, and that form is not for you yet. You do not have a password to type into it. Read this
step before you touch the page.

1. Go to **https://hellowoco.app/auth** (or click **Login** in the top navigation bar).
2. You will see a box titled **WorldCovers Account** with Email and Password fields and a **Sign
   In** button. **Ignore all of it.**
3. Underneath the Sign In button there is a small link: **Request a login**. Click that.

<!-- SCREENSHOT 01: the /auth page, full card, with the "Request a login" link at the bottom
     circled or arrowed. This is the single most important image in the guide. -->

4. A window opens titled **Request Login Access**. Fill in **First Name**, **Last Name**, and
   **Email**, then click **Submit Request**.

<!-- SCREENSHOT 02: the "Request Login Access" dialog, filled in with harmless placeholder
     values (e.g. Jane / Doe / jane@example.com). No real addresses. -->

5. You will see a confirmation reading **Request submitted!**. That is all you do for now — there
   is no password to choose, and nothing arrives instantly.
6. A project administrator reviews the request and emails you a **temporary password**. If a day
   passes and nothing has arrived, reply to the person who invited you rather than submitting the
   form again.

---

## Step 2 — Sign in and set your own password

1. Return to **https://hellowoco.app/auth**. This time use the **Sign In** form: your email address,
   and the temporary password you were sent.
2. Once you are signed in, your name appears at the top right of the page. Click it to open the
   menu.
3. Choose **Change password**.

<!-- SCREENSHOT 03: the avatar dropdown menu open, showing Dashboard / Change password / logout. -->

4. Enter the temporary password under **Current password**, then your own new password twice, and
   save.

<!-- SCREENSHOT 04: the "Change password" dialog, empty fields. Never capture a real password. -->

5. Now go to **https://woco.dev** and sign in there with **the same email and the same new
   password**. This is where your state's data lives and where you will do your reviewing.

If the woco.dev sign-in refuses your new password, the sync may not have caught up. Wait, then say
something — do not create a second account.

---

## Step 3 — Find your state

1. Click **Catalog** in the top navigation bar. (This is the search screen; it is labelled
   "Catalog".)
2. Use the search box — it reads *Search records, citations...* — and the filters beside it to
   narrow down to your state.

<!-- SCREENSHOT 05: the Catalog/search page with filters visible and a state's results loaded.
     Use a state that is genuinely loaded on woco.dev at capture time. -->

3. Results are Markings: **Townmarks** (the town's own postmark), **Ratemarks** (the postage rate),
   and **Auxmarks** (instructional markings such as PAID, FREE, MISSENT).
4. To start over, clear the filters and search again.

---

## Step 4 — Read a record

Click any result to open its record page.

<!-- SCREENSHOT 06: a record detail page, whole screen, on a record with a decent amount of data. -->

What you are looking at:

- **Inscription Text** — what is actually struck or written on the marking, abbreviations and all.
- **Color** — the ink color. Blank is legitimate when the source catalog never stated one.
- **Dates** — the earliest and latest dates recorded.
- **Shape, dimensions, lettering** — the physical description of the handstamp. A manuscript
  marking (written by hand) has no shape.
- **Associated Covers** — the actual covers recorded as bearing this marking.

---

## Step 5 — Your actual job: review and confirm

Every state's printed catalog used slightly different formats and notation, so an automated import
is never perfect. Your eye for your own state catches what the software cannot. That is the whole
reason you are here.

### Marking a record as reviewed

On a record page, editors see a checkbox labelled **Reviewed / confirmed**. Tick it once you are
satisfied that the record is right.

<!-- SCREENSHOT 07: a record page with the "Reviewed / confirmed" checkbox visible, unticked.
     Must be captured while signed in as an editor — the checkbox is hidden from everyone else. -->

### Seeing what is left

On the **Catalog** screen, editors get an extra filter called **Review Status**, with three
settings: **All (Default)**, **Reviewed**, and **Unreviewed**.

Set it to **Unreviewed** and you get exactly the records you have not yet worked through. This is
how you get through a whole state methodically instead of losing your place.

<!-- SCREENSHOT 08: the Review Status filter open, showing All / Reviewed / Unreviewed. -->

### The one convention that surprises everybody

When a catalog line lists several colors at once — say `PAID 5 — red, blue, green` — the import
creates **a separate listing for each color**. It has to, because it cannot know which ones are
real.

So if that marking only ever existed in red, you will find three listings where there should be
one. **Deleting the two that never existed is part of the job**, not a bug to report. The same
applies wherever one catalog line has been expanded into several records.

### What to do with errors you cannot fix yourself

Write them down and send them to the project team. If the errors are widespread, the state gets
re-run through the import with fixes rather than being corrected by hand. Once you judge the
remaining margin of error acceptable, you approve the state, and the data is moved to
hellowoco.app for final cleanup.

---

## Step 6 — Adding to the catalog

Beyond reviewing, you can add to the catalog. Use **Submit New Marking** for a marking the catalog
does not have, and **Submit Edit to Existing Marking** to correct one that it does.

<!-- SCREENSHOT 09: the record page header showing the Submit Edit to Existing Marking button. -->

To record an actual cover bearing a marking, use **Submit New Cover** from that marking's record.

<!-- SCREENSHOT 10: the Submit New Cover form, showing the image upload area and the three
     checkboxes below it. -->

On the cover form, three checkboxes matter:

- **Institutionally Owned** — tick if a museum, society, or archive holds the cover, rather than a
  private collector.
- **Backstamp** — tick if the marking is on the reverse of the cover.
- **Would you like your name to display as the submitter?** — tick this and your name is shown,
  publicly and permanently, alongside the cover you contributed. Leave it unticked to stay
  anonymous. It is entirely your choice.

**New submissions require an image.** Data already in the catalog and past submissions are taken
as given, but everything submitted from here on needs a picture as proof.

You can **Save Draft** and come back to it. When you submit, you will see **Submission received**,
and the entry goes into the review queue rather than straight into the catalog.

Everything you have submitted is listed under **Dashboard** in the menu under your name.

<!-- SCREENSHOT 11: the Dashboard, with at least one submission in a visible state. -->

---

## If something goes wrong

- **"Request a login" did nothing.** It opens a window on the same page. If nothing appeared, scroll
  up.
- **No temporary password arrived.** Check spam, then reply to whoever invited you. Do not submit
  the request form twice.
- **Signed in on hellowoco.app but not woco.dev.** Use the same password on both. If it still
  fails, say so — do not create a second account on woco.dev.
- **A search for your state returns nothing.** Not every state is loaded on every site yet. States
  land on woco.dev first.
- **You cannot see the Reviewed / confirmed checkbox.** It is shown only to editors, and only for
  the states you are responsible for.

The system is in beta. Bugs and rough edges are expected, and reports of them are welcome — that is
what this stage is for.
