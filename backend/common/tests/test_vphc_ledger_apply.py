"""The VPHC ledger applier.

Pins the things that would fail silently: a contribution that approves cleanly,
uncertainty surviving into the record, images being additive, and duplicates
being archived rather than deleted.
"""
import csv
import json
import os
import tempfile

from PIL import Image as PILImage

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from common.contribution_apply import apply_contribution_to_catalog
from common.models import (
    Citation,
    Collection,
    Color,
    Contribution,
    Marking,
    MarkingRecycleBin,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
)

CW_FIELDS = [
    "vphc_key", "vphc_code", "src", "state", "county", "town", "town_key",
    "cancel_no", "po_status", "sheet_type", "sheet_insc", "sheet_shape",
    "sheet_width", "sheet_height", "sheet_colors", "sheet_earliest",
    "sheet_latest", "prod_code", "prod_type", "prod_insc", "prod_shape",
    "prod_width", "prod_height", "prod_color", "match_color", "identity_fields",
    "sheet_images", "sheet_image_files", "prod_images", "images_to_add",
    "landing", "verdict", "flags", "catalog_marker", "editor_comment", "note",
]


def cw_row(**kw):
    row = {f: "" for f in CW_FIELDS}
    row.update(kw)
    return row


class ApplyVphcLedgerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("importer", password="x")
        self.va = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=self.user, modified_by=self.user)
        Collection.objects.create(
            name="Virginia", region=self.va, is_active=True,
            created_by=self.user, modified_by=self.user)
        self.reference = ReferenceWork.objects.create(
            code="VPHC1", title="Virginia Postal History Catalog",
            authorship="Robert L. Lisbeth",
            publisher="Virginia Postal History Society", publication_year=1982,
            created_by=self.user, modified_by=self.user)
        Color.objects.create(name="BLACK", created_by=self.user,
                             modified_by=self.user)
        office = PostOffice.objects.create(
            code="USA-VA1-1", name="Abingdon",
            created_by=self.user, modified_by=self.user)
        PostOfficeRegion.objects.create(
            post_office=office, region=self.va,
            created_by=self.user, modified_by=self.user)
        self.doomed = Marking.objects.create(
            code="ASCC6-VA-M9999", type="TOWNMARK", inscription_txt="ABINGDON/Va.",
            is_manuscript=False, is_irreg=False, post_office=office,
            created_by=self.user, modified_by=self.user)

        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "crossexam", "ledger"))
        os.makedirs(os.path.join(self.dir, "extract", "media"))

        self.rows = [cw_row(
            vphc_key="ABINGDON#6", vphc_code="VPHC-VA-ABINGDON-6", src="T1:r9",
            state="VA", county="Washington", town="ABINGDON", town_key="ABINGDON",
            cancel_no="6", sheet_type="TOWNMARK", sheet_insc="ABINGDON/VA.",
            sheet_width="30.5", sheet_height="30.5", sheet_colors="BLACK;RED",
            sheet_earliest="1834", sheet_latest="1836", landing="create",
            verdict="create_no_inscription", flags="century_inferred",
            catalog_marker="[VPHC: century inferred]",
            editor_comment="Please check: The century was reconstructed.")]
        self.lines = [{
            "run": "test", "mode": "dryrun", "applied": False, "src": "T1:r9",
            "town": "ABINGDON", "county": "Washington", "cancel": "6",
            "action": "create", "target": "marking", "target_code": None,
            "marking_id": None, "rule": "I5", "rule_version": 2,
            "confidence": 1.0, "fields": [], "before": {}, "after": {},
            "rejected": {}, "why_unmatched": "create_no_inscription",
            "editor_comment": "Please check: The century was reconstructed.",
        }]
        self.write()

    def write(self):
        with open(os.path.join(self.dir, "crossexam", "crosswalk.csv"), "w",
                  newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CW_FIELDS, lineterminator="\n")
            w.writeheader()
            for r in self.rows:
                w.writerow(r)
        with open(os.path.join(self.dir, "crossexam", "ledger", "proposed.jsonl"),
                  "w", encoding="utf-8") as fh:
            for line in self.lines:
                fh.write(json.dumps(line) + "\n")

    def run_apply(self, **kw):
        call_command("apply_vphc_ledger", vphc_dir=self.dir, actor=self.user.pk,
                     verbosity=0, **kw)

    # ------------------------------------------------------------------ tests

    def test_creates_a_pending_contribution_that_approves_cleanly(self):
        self.run_apply()
        contribution = Contribution.objects.get()
        self.assertEqual(contribution.status, Contribution.STATUS_PENDING)

        data = contribution.submitted_data
        self.assertEqual(data["town"], "Abingdon")
        self.assertEqual(data["type"], "TOWNMARK")
        self.assertTrue(data["no_marking_image"])
        # RED is not in this fixture's Color vocabulary, so it cannot become a
        # marking of its own the way a recognised colour now does (issue #103).
        # It must still be named -- the color_unrecognised flag says a colour
        # was dropped but not which one.
        self.assertIn(
            "Colours recorded but not in the catalog vocabulary: RED", data["desc"],
        )
        self.assertIn("[VPHC: century inferred]", data["desc"])

        marking = apply_contribution_to_catalog(contribution)
        self.assertEqual(marking.post_office.name, "Abingdon")
        self.assertEqual(marking.color.name, "BLACK")
        self.assertEqual(float(marking.width), 30.5)
        # The doubt still survives approval -- but on the contribution, not in
        # the catalog record. `desc` is served by the AllowAny markings API, so
        # this assertion used to read "[VPHC: century inferred]" IS in
        # marking.desc, which is precisely what issue #110 changed: the marker
        # is stripped on approval and MarkingDetailSerializer.vphc_provenance
        # shows it to editors instead. The prose around it is untouched.
        self.assertNotIn("[VPHC:", marking.desc)
        self.assertIn(
            "Colours recorded but not in the catalog vocabulary: RED", marking.desc,
        )
        contribution.refresh_from_db()
        self.assertIn("[VPHC: century inferred]", contribution.submitted_data["desc"])

    def test_the_society_is_credited(self):
        self.run_apply()
        marking = apply_contribution_to_catalog(Contribution.objects.get())
        citation = Citation.objects.get(subject_type="MARKING",
                                        subject_id=marking.pk)
        self.assertEqual(citation.reference_work.code, "VPHC1")
        self.assertEqual(citation.reference_work.publisher,
                         "Virginia Postal History Society")
        # the cancel number is the place within the work
        self.assertIn("6", citation.citation_detail)

    def test_uncertainty_is_queryable_before_approval(self):
        self.run_apply()
        self.assertEqual(
            Contribution.objects.filter(
                submitted_data__vphc__flags__contains=["century_inferred"]
            ).count(), 1)
        self.assertTrue(Contribution.objects.get().submitted_data[
            "contributor_comment"])

    def test_an_unknown_device_defaults_loudly_rather_than_failing(self):
        self.rows[0]["sheet_type"] = ""
        self.rows[0]["sheet_insc"] = ""
        self.write()
        self.run_apply()
        data = Contribution.objects.get().submitted_data
        self.assertEqual(data["type"], "TOWNMARK")
        self.assertIn("type_defaulted", data["vphc"]["flags"])
        self.assertIn("please correct", data["desc"])
        # inscription is required and non-blank; the town is the honest fallback
        self.assertEqual(data["inscription_txt"], "ABINGDON")

    def test_duplicates_are_archived_never_deleted(self):
        self.lines.append({
            "run": "test", "mode": "dryrun", "applied": False, "src": "",
            "town": "Abingdon", "county": "", "cancel": "", "action": "archive",
            "target": "marking", "target_code": "ASCC6-VA-M9999",
            "marking_id": None, "rule": "D1", "rule_version": 2,
            "confidence": 0.99, "fields": ["recycle_bin"],
            "before": {"recycle_bin": None},
            "after": {"recycle_bin": "archived",
                      "duplicate_of": "ASCC6-VA-M1000", "reason": "identical"},
            "rejected": {"delete": "archive only"},
        })
        self.write()
        self.run_apply()

        self.assertTrue(MarkingRecycleBin.objects.filter(
            marking=self.doomed).exists())
        # the row itself is untouched and still reachable
        self.assertTrue(Marking.all_objects.filter(code="ASCC6-VA-M9999").exists())
        self.assertFalse(Marking.objects.filter(code="ASCC6-VA-M9999").exists())

    def test_dry_run_writes_nothing(self):
        self.run_apply(dry_run=True)
        self.assertEqual(Contribution.objects.count(), 0)

    def test_missing_reference_work_is_refused(self):
        self.reference.delete()
        with self.assertRaises(CommandError) as ctx:
            self.run_apply()
        self.assertIn("VPHC1", str(ctx.exception))

    def test_an_edit_carries_the_marking_s_existing_citations(self):
        """Naming a citation states the complete set, so an edit that named
        only VPHC1 deleted whatever the marking was already cited to -- in
        production, its ASCC citation on 118 of 120 sampled markings."""
        ascc = ReferenceWork.objects.create(
            code="ASCC6", title="American Stampless Cover Catalog",
            authorship="David G. Phillips", publisher="David G. Phillips",
            publication_year=1985,
            created_by=self.user, modified_by=self.user)
        Citation.objects.create(
            reference_work=ascc, subject_type="MARKING",
            subject_id=self.doomed.pk, citation_detail="p. 214",
            created_by=self.user, modified_by=self.user)

        self.rows.append(cw_row(
            vphc_key="ABINGDON#7", vphc_code="VPHC-VA-ABINGDON-7", src="T1:r10",
            state="VA", county="Washington", town="ABINGDON",
            town_key="ABINGDON", cancel_no="7", sheet_type="TOWNMARK",
            sheet_insc="ABINGDON/VA.", sheet_colors="BLACK",
            prod_code="ASCC6-VA-M9999", landing="update", verdict="resolved"))
        self.lines.append({
            "run": "test", "mode": "dryrun", "applied": False, "src": "T1:r10",
            "town": "ABINGDON", "county": "Washington", "cancel": "7",
            "action": "update", "target": "marking",
            "target_code": "ASCC6-VA-M9999", "marking_id": self.doomed.pk,
            "rule": "I3", "rule_version": 2, "confidence": 1.0,
            "fields": ["color"], "before": {}, "after": {}, "rejected": {},
            "why_unmatched": "", "editor_comment": "",
        })
        self.write()
        self.run_apply(only="update")

        contribution = Contribution.objects.get(
            submitted_data__edit_marking_id=self.doomed.pk)
        # VPHC1 first: catalog_codes derives the code prefix from the first id.
        self.assertEqual(contribution.submitted_data["reference_work_ids"],
                         [self.reference.pk, ascc.pk])

        apply_contribution_to_catalog(contribution)
        self.assertEqual(
            set(Citation.objects.filter(
                subject_type="MARKING", subject_id=self.doomed.pk
            ).values_list("reference_work__code", flat=True)),
            {"VPHC1", "ASCC6"})

    # ------------------------------------------- the colour grain (issue #103)

    def _recognise(self, *names):
        for name in names:
            Color.objects.get_or_create(
                name=name,
                defaults={"created_by": self.user, "modified_by": self.user},
            )

    def test_a_multi_colour_create_becomes_one_marking_per_colour(self):
        # Production's grain is one Marking row per type x colour (settled
        # 2026-08-15). The ingest used to keep colours[0] and write the rest
        # into the description as prose -- the book's grain, not the site's.
        self._recognise("RED", "BLUE")
        self.rows[0]["sheet_colors"] = "BLACK;RED;BLUE"
        self.write()
        self.run_apply()

        contributions = list(Contribution.objects.all())
        self.assertEqual(len(contributions), 3)
        self.assertEqual(
            sorted(c.submitted_data["color"] for c in contributions),
            ["BLACK", "BLUE", "RED"],
        )
        # The colours are rows now, so repeating them as prose would say the
        # same thing twice and re-assert the collapsed grain.
        for contribution in contributions:
            self.assertNotIn("Colours recorded", contribution.submitted_data["desc"])
        # Everything else about the listing is shared, not re-derived.
        for contribution in contributions:
            self.assertEqual(contribution.submitted_data["town"], "Abingdon")
            self.assertEqual(contribution.submitted_data["vphc"]["cancel_no"], "6")

    def test_the_scan_attaches_to_exactly_one_colour_variant(self):
        # E5: one scan is evidence for one physical device. Copying it onto
        # every colour variant would assert the same photograph proves three
        # separate records.
        self._recognise("RED", "BLUE")
        self.rows[0]["sheet_colors"] = "BLACK;RED;BLUE"
        self.rows[0]["sheet_image_files"] = "vphc-va-abingdon-6-r9.png"
        PILImage.new("RGB", (40, 40), color=(10, 10, 10)).save(
            os.path.join(self.dir, "extract", "media", "vphc-va-abingdon-6-r9.png")
        )
        self.write()
        self.run_apply()

        with_images = [c for c in Contribution.objects.all()
                       if c.submitted_data.get("marking_image_metas")]
        self.assertEqual(len(with_images), 1)
        self.assertEqual(Contribution.objects.count(), 3)
        without = [c for c in Contribution.objects.all()
                   if not c.submitted_data.get("marking_image_metas")]
        # _sync_images refuses a marking that neither has an image nor says it
        # has none deliberately.
        self.assertTrue(all(c.submitted_data["no_marking_image"] for c in without))

    def test_a_multi_colour_edit_stays_exactly_one_contribution(self):
        # Rule I3 already paired edits one-per-colour against live rows, so an
        # edit targets one existing marking and must not fan out. Its
        # description must also stay byte-identical.
        #
        # This test used to assert the payload carried "BLACK" -- sheet_colors[0]
        # -- which is exactly the defect issue #117 turned out to be. `self.doomed`
        # has no colour, so the edit-only backfill has nothing to write and the
        # key is now absent entirely. See test_an_edit_never_repaints_a_live_marking
        # for the invariant that replaced it.
        self._recognise("RED", "BLUE")
        self.rows.append(cw_row(
            vphc_key="ABINGDON#7", vphc_code="VPHC-VA-ABINGDON-7", src="T1:r10",
            state="VA", county="Washington", town="ABINGDON",
            town_key="ABINGDON", cancel_no="7", sheet_type="TOWNMARK",
            sheet_insc="ABINGDON/VA.", sheet_colors="BLACK;RED;BLUE",
            prod_code="ASCC6-VA-M9999", landing="update", verdict="resolved"))
        self.lines.append({
            "run": "test", "mode": "dryrun", "applied": False, "src": "T1:r10",
            "town": "ABINGDON", "county": "Washington", "cancel": "7",
            "action": "update", "target": "marking",
            "target_code": "ASCC6-VA-M9999", "marking_id": self.doomed.pk,
            "rule": "I3", "rule_version": 2, "confidence": 1.0,
            "fields": ["color"], "before": {}, "after": {}, "rejected": {},
            "why_unmatched": "", "editor_comment": "",
        })
        self.write()
        self.run_apply(only="update")

        contribution = Contribution.objects.get()
        self.assertNotIn("color", contribution.submitted_data)
        self.assertIn(
            "Colours recorded: BLACK, RED, BLUE",
            contribution.submitted_data["desc"],
        )

    def test_the_ledger_records_one_line_per_decision_not_per_row(self):
        # LEDGER.jsonl is the replayable record of what the ledger decided. A
        # colour fan-out is one decision that produced three rows.
        self._recognise("RED", "BLUE")
        self.rows[0]["sheet_colors"] = "BLACK;RED;BLUE"
        self.write()
        self.run_apply()

        with open(os.path.join(self.dir, "LEDGER.jsonl"), encoding="utf-8") as fh:
            written = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(written), 1)
        self.assertEqual(Contribution.objects.count(), 3)

    def test_a_single_colour_create_is_unchanged(self):
        self.rows[0]["sheet_colors"] = "BLACK"
        self.write()
        self.run_apply()

        contribution = Contribution.objects.get()
        self.assertEqual(contribution.submitted_data["color"], "BLACK")
        self.assertNotIn("Colours recorded", contribution.submitted_data["desc"])

    def test_a_create_with_no_colour_at_all_still_emits_one_row(self):
        self.rows[0]["sheet_colors"] = ""
        self.write()
        self.run_apply()

        contribution = Contribution.objects.get()
        self.assertNotIn("color", contribution.submitted_data)

    def test_an_edit_whose_first_colour_is_unknown_keeps_the_marking_s_own(self):
        """The fan-out must not repaint a live marking.

        `recognised[0]` reads like a harmless tightening of `colours[0]`. It is
        not: where the sheet's FIRST colour is one the vocabulary rejects, the
        payload must carry no colour so the edit branch backfills the marking's
        existing one. Picking the first *recognised* sheet colour instead sends
        a different colour, which merges on approval. Measured over the real
        crosswalk this changed 3 of the 310 queued edits, one RED -> BLACK.
        """
        self._recognise("BLACK")
        self.doomed.color = Color.objects.create(
            name="RED", created_by=self.user, modified_by=self.user)
        self.doomed.save(update_fields=["color"])

        self.rows.append(cw_row(
            vphc_key="ABINGDON#7", vphc_code="VPHC-VA-ABINGDON-7", src="T1:r10",
            state="VA", county="Washington", town="ABINGDON",
            town_key="ABINGDON", cancel_no="7", sheet_type="TOWNMARK",
            sheet_insc="ABINGDON/VA.",
            # PUCE is not in the vocabulary and it is first.
            sheet_colors="PUCE;BLACK",
            prod_code="ASCC6-VA-M9999", landing="update", verdict="resolved"))
        self.lines.append({
            "run": "test", "mode": "dryrun", "applied": False, "src": "T1:r10",
            "town": "ABINGDON", "county": "Washington", "cancel": "7",
            "action": "update", "target": "marking",
            "target_code": "ASCC6-VA-M9999", "marking_id": self.doomed.pk,
            "rule": "I3", "rule_version": 2, "confidence": 1.0,
            "fields": ["color"], "before": {}, "after": {}, "rejected": {},
            "why_unmatched": "", "editor_comment": "",
        })
        self.write()
        self.run_apply(only="update")

        data = Contribution.objects.get().submitted_data
        self.assertEqual(data["color"], "RED")
        self.assertIn("color_unrecognised", data["vphc"]["flags"])

    # ---------------------------------------------------------- issue #117
    #
    # None of these existed when the 58 repaints shipped. The gap was not a
    # missing assertion but a missing *question*: every check compared two
    # candidate implementations of the edit colour rule to each other, and none
    # compared the emitted payload to the marking it actually targets.

    def _queue_edit(self, sheet_colors, cancel_no="7", src="T1:r10"):
        """Queue one edit against self.doomed with the given sheet colours."""
        self.rows.append(cw_row(
            vphc_key="ABINGDON#%s" % cancel_no,
            vphc_code="VPHC-VA-ABINGDON-%s" % cancel_no, src=src,
            state="VA", county="Washington", town="ABINGDON",
            town_key="ABINGDON", cancel_no=cancel_no, sheet_type="TOWNMARK",
            sheet_insc="ABINGDON/VA.", sheet_colors=sheet_colors,
            prod_code="ASCC6-VA-M9999", landing="update", verdict="resolved"))
        self.lines.append({
            "run": "test", "mode": "dryrun", "applied": False, "src": src,
            "town": "ABINGDON", "county": "Washington", "cancel": cancel_no,
            "action": "update", "target": "marking",
            "target_code": "ASCC6-VA-M9999", "marking_id": self.doomed.pk,
            "rule": "I3", "rule_version": 2, "confidence": 1.0,
            "fields": ["color"], "before": {}, "after": {}, "rejected": {},
            "why_unmatched": "", "editor_comment": "",
        })
        self.write()
        self.run_apply(only="update")
        return Contribution.objects.get()

    def test_an_edit_carries_the_matched_colour_not_the_sheet_s_first(self):
        """Issue #117, stated as the case that actually occurred 58 times.

        I3 paired this edit to the RED production row. The sheet lists
        BLACK first. The payload must never carry BLACK -- that is a different
        member of the same listing, and merging it repaints a live marking.
        """
        self._recognise("BLACK", "RED")
        self.doomed.color = Color.objects.get(name="RED")
        self.doomed.save(update_fields=["color"])

        data = self._queue_edit("BLACK;RED").submitted_data

        self.assertEqual(data["color"], "RED")
        self.assertNotEqual(data["color"], "BLACK")

    def test_an_edit_never_repaints_a_live_marking(self):
        """The invariant, stated absolutely rather than as a delta.

        Approving any VPHC edit must leave `marking.color` exactly as it was.
        The 58 hid for four days behind a comment measuring one candidate
        implementation against another ("3 of the 310 edits changed"), which is
        not the same claim as this one.
        """
        self._recognise("BLACK", "RED", "BLUE")
        self.doomed.color = Color.objects.get(name="BLUE")
        self.doomed.save(update_fields=["color"])
        before = self.doomed.color_id

        contribution = self._queue_edit("BLACK;RED;BLUE")
        apply_contribution_to_catalog(contribution)

        self.doomed.refresh_from_db()
        self.assertEqual(self.doomed.color_id, before)
        self.assertEqual(self.doomed.color.name, "BLUE")

    def test_an_edit_against_a_colourless_marking_omits_the_key(self):
        """No colour to backfill means no key at all -- and approval must not
        invent one. `_payload_mentions_fk` returns False for an absent key, so
        `marking.color` is left alone rather than being set from the sheet."""
        self._recognise("BLACK", "RED")
        self.assertIsNone(self.doomed.color_id)

        contribution = self._queue_edit("BLACK;RED")
        self.assertNotIn("color", contribution.submitted_data)

        apply_contribution_to_catalog(contribution)
        self.doomed.refresh_from_db()
        self.assertIsNone(self.doomed.color_id)

    def test_the_fix_leaves_the_edit_description_byte_identical(self):
        """The colours are still *recorded*, just not written to the field.

        `_description` takes the whole `colours` list and never `colours[0]`, so
        removing the payload colour must not cost the observation. This is what
        the original comment was right to care about.
        """
        self._recognise("BLACK", "RED", "BLUE")
        self.doomed.color = Color.objects.get(name="BLUE")
        self.doomed.save(update_fields=["color"])

        data = self._queue_edit("BLACK;RED;BLUE").submitted_data

        self.assertIn("Colours recorded: BLACK, RED, BLUE", data["desc"])

    # ---------------------------------------------------------- issue #115
    def test_a_skipped_listing_leaves_an_artefact(self):
        """A refusal is fine. A refusal nobody can find is not.

        Four Bank's Division listings left the ledger and never reached the
        queue because the applier could not resolve a state for them. The only
        record was a stdout counter, so the loss was invisible until the ledger
        was counted against the queue a week later. Rule C1 says nothing from
        the sheet is ever dropped; when the applier declines, it must say which.
        """
        self.rows.append(cw_row(
            vphc_key="#1", vphc_code="VPHC-VA--1", src="T1:r345",
            state="", county="BANK'S DIVISION", town="", town_key="",
            cancel_no="1", sheet_type="TOWNMARK", sheet_colors="BLUE",
            landing="create", verdict="create_no_town",
            flags="county_uncertain;state_unknown"))
        self.lines.append({
            "run": "test", "mode": "dryrun", "applied": False, "src": "T1:r345",
            "town": "", "county": "BANK'S DIVISION", "cancel": "1",
            "action": "create", "target": "marking", "target_code": None,
            "marking_id": None, "rule": "I5", "rule_version": 2,
            "confidence": 1.0, "fields": [], "before": {}, "after": {},
            "rejected": {}, "why_unmatched": "create_no_town",
            "editor_comment": "",
        })
        self.write()
        self.run_apply(only="create")

        path = os.path.join(self.dir, "crossexam", "skipped.csv")
        self.assertTrue(os.path.exists(path), "no skipped.csv was written")
        with open(path, newline="", encoding="utf-8") as fh:
            skipped = list(csv.DictReader(fh))

        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["src"], "T1:r345")
        self.assertEqual(skipped[0]["reason"], "state cannot be determined")
        self.assertEqual(skipped[0]["county"], "BANK'S DIVISION")
        # the listing the applier DID emit is untouched by any of this
        self.assertEqual(Contribution.objects.count(), 1)
