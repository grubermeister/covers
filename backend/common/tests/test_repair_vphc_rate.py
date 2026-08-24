"""The rate repair (issue #120).

Pins the three things that would be expensive to get wrong: a dry run that
writes, a human submission that gets swept up, and a stale --expect that is
trusted anyway.
"""
import io
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from common.models import (
    Citation,
    Collection,
    Contribution,
    Marking,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
)


def payload(**kw):
    data = {
        "submission_kind": "marking", "type": "RATEMARK", "state": "VA",
        "town": "Abingdon", "inscription_txt": "3/DUE", "rate_val": "4",
        "vphc": {"src": "T1:r348", "cancel_no": "4",
                 "vphc_code": "VPHC-VA-ABINGDON-4", "flags": []},
    }
    data.update(kw)
    return data


class RepairVphcRateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("importer", password="x")
        self.va = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=self.user, modified_by=self.user)
        self.collection = Collection.objects.create(
            name="Virginia", region=self.va, is_active=True,
            created_by=self.user, modified_by=self.user)
        self.reference = ReferenceWork.objects.create(
            code="VPHC1", title="Virginia Postal History Catalog",
            authorship="Robert L. Lisbeth",
            publisher="Virginia Postal History Society", publication_year=1982,
            created_by=self.user, modified_by=self.user)

        self.wrong = self._contribution(payload())
        # A real person's submission. It has no `vphc` key, and nothing this
        # command does may reach it -- the census that found only two of these
        # missed six, and "delete all pending" would have destroyed eight.
        #
        # Its rate is deliberately one the repair WOULD rewrite if the `vphc`
        # filter were dropped: the device says 3 and the contributor typed 9.
        # A contributor is allowed to be wrong; correcting them is not this
        # command's job, and a row that survives only because its value happens
        # to agree proves nothing.
        self.human = self._contribution({
            "submission_kind": "marking", "type": "RATEMARK",
            "town": "Boston", "inscription_txt": "3/PAID", "rate_val": "9",
        })

    def _contribution(self, submitted_data, status=Contribution.STATUS_PENDING):
        return Contribution.objects.create(
            contributor=self.user, collection=self.collection,
            submitted_data=submitted_data, status=status,
            created_by=self.user, modified_by=self.user)

    def _marking(self, inscription_txt, rate_val, code="ASCC6-VA-M1"):
        """A published VPHC-cited RATEMARK -- what --audit-live looks for."""
        office, _ = PostOffice.objects.get_or_create(
            code="USA-VA1-1", defaults={
                "name": "Abingdon", "created_by": self.user,
                "modified_by": self.user})
        PostOfficeRegion.objects.get_or_create(
            post_office=office, region=self.va,
            defaults={"created_by": self.user, "modified_by": self.user})
        marking = Marking.objects.create(
            code=code, type="RATEMARK", inscription_txt=inscription_txt,
            rate_val=Decimal(rate_val), is_manuscript=False, is_irreg=False,
            post_office=office, created_by=self.user, modified_by=self.user)
        Citation.objects.create(
            reference_work=self.reference, subject_type="MARKING",
            subject_id=marking.pk, citation_detail="p. 4",
            created_by=self.user, modified_by=self.user)
        return marking

    def run_repair(self, **kw):
        out = io.StringIO()
        call_command("repair_vphc_rate", actor=self.user.pk, stdout=out, **kw)
        return out.getvalue()

    # ------------------------------------------------------------------ core

    def test_the_drawing_number_is_replaced_by_the_device_s_rate(self):
        self.run_repair(commit=True)
        self.wrong.refresh_from_db()
        self.assertEqual(self.wrong.submitted_data["rate_val"], "3")

    def test_a_dry_run_writes_nothing(self):
        output = self.run_repair()
        self.wrong.refresh_from_db()
        self.assertEqual(self.wrong.submitted_data["rate_val"], "4")
        self.assertIn("DRY RUN", output)

    def test_a_human_submission_is_never_touched(self):
        before = self.human.modified_date
        self.run_repair(commit=True)
        self.human.refresh_from_db()
        self.assertEqual(self.human.submitted_data["rate_val"], "9")
        self.assertEqual(self.human.modified_date, before)

    def test_a_stale_expect_aborts_rather_than_writing(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_repair(commit=True, expect=99)
        self.assertIn("99", str(ctx.exception))
        self.wrong.refresh_from_db()
        self.assertEqual(self.wrong.submitted_data["rate_val"], "4")

    def test_an_underivable_rate_removes_the_key_rather_than_emptying_it(self):
        """Absent means keep, "" means clear. The difference is a live rate."""
        row = self._contribution(payload(inscription_txt="PAID", rate_val="8"))
        self.run_repair(commit=True)
        row.refresh_from_db()
        self.assertNotIn("rate_val", row.submitted_data)

    def test_an_already_approved_row_is_reported_but_not_edited(self):
        """Its payload is already in the catalog; editing it here would desync
        the two and still leave the wrong rate live."""
        row = self._contribution(payload(), status=Contribution.STATUS_APPROVED)
        output = self.run_repair(commit=True)
        row.refresh_from_db()
        self.assertEqual(row.submitted_data["rate_val"], "4")
        self.assertIn("audit-live", output)

    def test_an_unwritable_report_warns_and_still_repairs(self):
        """Hit for real on woco.dev: the command runs as `wocod` and the
        operator's scratch directory belongs to `reese`, so --report raised
        PermissionError and aborted the run *before* any repair. The report
        documents the work; it does not get to cancel it."""
        with mock.patch("builtins.open",
                        side_effect=PermissionError(13, "Permission denied")):
            output = self.run_repair(commit=True, report="/anywhere/census.csv")
        self.wrong.refresh_from_db()
        self.assertEqual(self.wrong.submitted_data["rate_val"], "3")
        self.assertIn("could not write the report", output)

    def test_a_space_separated_device_is_read_the_same_as_a_slashed_one(self):
        """This call site passes a SINGLE stored inscription, not the
        crosswalk's semicolon key set -- "DUE 3" rather than "3/DUE;DUE 3".
        Both spellings reach production, so both must yield 3."""
        row = self._contribution(payload(inscription_txt="DUE 3", rate_val="4"))
        self.run_repair(commit=True)
        row.refresh_from_db()
        self.assertEqual(row.submitted_data["rate_val"], "3")

    # ----------------------------------------------------- the live catalog

    def test_a_fractional_live_rate_is_not_truncated_into_a_false_match(self):
        """rate_val is DecimalField(decimal_places=2). Comparing via int()
        read a stored 2.50 as 2, matched a stated "2", and skipped a marking
        that needed repairing."""
        marking = self._marking(inscription_txt="2/PAID", rate_val="2.50")
        self.run_repair(commit=True, audit_live=True)
        marking.refresh_from_db()
        self.assertEqual(marking.rate_val, Decimal("2.00"))

    def test_audit_live_finds_and_repairs_an_approved_marking(self):
        marking = self._marking(inscription_txt="3/DUE", rate_val="4")
        self.run_repair(commit=True, audit_live=True)
        marking.refresh_from_db()
        self.assertEqual(int(marking.rate_val), 3)
