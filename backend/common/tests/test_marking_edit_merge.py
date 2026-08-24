"""Marking-edit merge semantics (blocker B1).

An approved marking edit merges into the existing row: a column the submission
never mentions keeps its stored value, and only an explicitly empty value
clears it (RFC 7396).

The regression this pins is not hypothetical. `_apply_marking_edit` used to
rebuild the row from the payload, so every column the payload omitted was
written as NULL -- and because `full_clean()` passed, the approval returned 200
and wrote a clean audit trail. Measured on woco.dev 2026-08-13, approving the
VPHC queue would have nulled `impression` on all 288 matched markings and
deleted the existing ASCC citation on 118 of 120 sampled.

`fixtures/vphc_edit_payloads.json` holds one REAL payload per distinct key-set
found across the 310 queued VPHC edits (18 shapes, censused from the local
`Contributions` table). Replaying one of each covers all 310 by construction:
the defect is driven by which keys are present, not by their values.
"""
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from common.contribution_apply import apply_contribution_to_catalog
from common.models import (
    Citation,
    Collection,
    Color,
    Contribution,
    Marking,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
    Shape,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "vphc_edit_payloads.json"

# Production ids, kept so the fixture's reference_work_ids resolve unchanged.
VPHC_REFERENCE_ID = 13
ASCC_REFERENCE_ID = 4

# What the sheet never speaks to. Set on every marking before the replay so a
# silent null shows up as a failure rather than as a field that was empty
# anyway.
UNSPOKEN = {
    "impression": "Normal",
    "date_fmt": "MDD",
    "catalog_txt": "ASCC entry text",
}


class MarkingEditMergeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("editor", password="x")
        who = {"created_by": cls.user, "modified_by": cls.user}

        for code, name, abbrev in (
            ("USA-VA1", "Virginia", "VA"),
            ("USA-WV1", "West Virginia", "WV"),
        ):
            region = Region.objects.create(
                code=code, name=name, abbrev=abbrev, region_tier="STATE", **who
            )
            Collection.objects.create(
                name=name, region=region, is_active=True, **who
            )
        cls.va = Region.objects.get(abbrev="VA")

        for name in ("BLACK", "BLUE", "RED"):
            Color.objects.create(name=name, **who)
        for name in ("Box", "C - Circle"):
            Shape.objects.create(name=name, **who)

        ReferenceWork.objects.create(
            pk=VPHC_REFERENCE_ID, code="VPHC1",
            title="Virginia Postal History Catalog",
            authorship="Robert L. Lisbeth",
            publisher="Virginia Postal History Society",
            publication_year=1982, **who
        )
        ReferenceWork.objects.create(
            pk=ASCC_REFERENCE_ID, code="ASCC6",
            title="American Stampless Cover Catalog",
            authorship="David G. Phillips", publisher="David G. Phillips",
            publication_year=1985, **who
        )

        cls.office = PostOffice.objects.create(name="Seedtown", **who)
        PostOfficeRegion.objects.create(
            post_office=cls.office, region=cls.va, **who
        )

    def setUp(self):
        self.payloads = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _marking(self, index):
        """A marking carrying values in every column the sheet never mentions."""
        marking = Marking.objects.create(
            code="ASCC6-VA-M{:04d}".format(index),
            type="TOWNMARK",
            inscription_txt="SEEDTOWN/Va.",
            desc="Soldier's mail / Rate note: YD,DUE3",
            is_manuscript=False,
            is_irreg=False,
            rate_val="3.00",
            post_office=self.office,
            created_by=self.user,
            modified_by=self.user,
            **UNSPOKEN,
        )
        Citation.objects.create(
            reference_work_id=ASCC_REFERENCE_ID,
            subject_type="MARKING",
            subject_id=marking.pk,
            citation_detail="p. 214",
            created_by=self.user,
            modified_by=self.user,
        )
        return marking

    def _approve(self, marking, payload):
        payload = dict(payload, edit_marking_id=marking.pk)
        contrib = Contribution.objects.create(
            contributor=self.user,
            collection=Collection.objects.get(region=self.va),
            submitted_data=payload,
            status=Contribution.STATUS_PENDING,
            created_by=self.user,
            modified_by=self.user,
        )
        apply_contribution_to_catalog(contrib)
        marking.refresh_from_db()
        return payload

    def test_fixture_covers_the_real_queue(self):
        """The fixture is the census, not a sample -- and it is the census of a
        payload that genuinely never speaks to the fields at issue."""
        self.assertEqual(len(self.payloads), 18)
        mentioned = set().union(*(set(p) for p in self.payloads))
        self.assertEqual(mentioned & set(UNSPOKEN), set())
        self.assertNotIn("description", mentioned)

    def test_unmentioned_columns_survive_every_real_payload(self):
        for index, payload in enumerate(self.payloads):
            with self.subTest(payload=index):
                marking = self._marking(index)
                self._approve(marking, payload)

                self.assertEqual(marking.impression, UNSPOKEN["impression"])
                self.assertEqual(marking.date_fmt, UNSPOKEN["date_fmt"])
                self.assertEqual(marking.catalog_txt, UNSPOKEN["catalog_txt"])

    def test_citations_survive_a_payload_that_never_mentions_them(self):
        """Silence about citations is not an instruction to delete them.

        A payload that DOES list reference_work_ids is a different matter: it
        states the complete desired set, and replacing is correct. That is why
        the queued VPHC edits -- which list only VPHC1 -- have to be re-emitted
        carrying the union; see ApplyVphcLedgerTests.
        """
        payload = {
            k: v for k, v in self.payloads[0].items()
            if k not in ("reference_work_ids", "reference_work_details")
        }
        marking = self._marking(1000)
        self._approve(marking, payload)

        cited = set(
            Citation.objects.filter(
                subject_type="MARKING", subject_id=marking.pk
            ).values_list("reference_work_id", flat=True)
        )
        self.assertEqual(cited, {ASCC_REFERENCE_ID})

    def test_an_explicit_citation_list_still_replaces(self):
        marking = self._marking(1100)
        self._approve(marking, self.payloads[0])

        cited = set(
            Citation.objects.filter(
                subject_type="MARKING", subject_id=marking.pk
            ).values_list("reference_work_id", flat=True)
        )
        self.assertEqual(cited, {VPHC_REFERENCE_ID})

    def test_rate_val_survives_when_the_payload_omits_it(self):
        silent = [p for p in self.payloads if "rate_val" not in p]
        self.assertTrue(silent, "fixture should contain rate_val-free shapes")
        for index, payload in enumerate(silent):
            with self.subTest(payload=index):
                marking = self._marking(2000 + index)
                self._approve(marking, payload)
                self.assertEqual(str(marking.rate_val), "3.00")

    def test_the_sheet_still_wins_where_it_speaks(self):
        """Merge semantics must not turn into "ignore the submission"."""
        payload = next(p for p in self.payloads if p.get("color"))
        marking = self._marking(3000)
        self._approve(marking, payload)

        self.assertEqual(marking.color.name, payload["color"])
        self.assertEqual(marking.type, payload["type"])
        # The submitted description still lands -- that is what this test is
        # about -- but issue #110 removes the ingest's internal notation from it
        # on the way in, because `desc` is public. So the sheet still wins; it
        # just does not carry "(T1:r8)" or "[VPHC: ...]" into the catalog.
        self.assertIn("(T1:r", payload["desc"])
        self.assertEqual(marking.desc, "Virginia Postal History Catalog Abingdon #5.")

    def test_an_explicitly_empty_value_still_clears(self):
        """The submit form states emptiness as "", not as an omitted key, so a
        contributor clearing a box must still clear the column."""
        payload = dict(self.payloads[0], impression="", desc="", rate_val="")
        marking = self._marking(4000)
        self._approve(marking, payload)

        self.assertIsNone(marking.impression)
        self.assertIsNone(marking.desc)
        self.assertIsNone(marking.rate_val)
