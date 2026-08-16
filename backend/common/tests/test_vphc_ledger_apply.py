"""The VPHC ledger applier.

Pins the things that would fail silently: a contribution that approves cleanly,
uncertainty surviving into the record, images being additive, and duplicates
being archived rather than deleted.
"""
import csv
import json
import os
import tempfile

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
        # colour is single-valued on Marking, so the rest must not vanish
        self.assertIn("Colours recorded: BLACK, RED", data["desc"])
        self.assertIn("[VPHC: century inferred]", data["desc"])

        marking = apply_contribution_to_catalog(contribution)
        self.assertEqual(marking.post_office.name, "Abingdon")
        self.assertEqual(marking.color.name, "BLACK")
        self.assertEqual(float(marking.width), 30.5)
        # the doubt survives approval, not just the queue
        self.assertIn("[VPHC: century inferred]", marking.desc)

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
