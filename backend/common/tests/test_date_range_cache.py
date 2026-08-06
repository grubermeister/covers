"""
Issue #59: Marking.earliest_seen/latest_seen are cached columns maintained by
common.date_range via DateSeen/CoverMarking signals. These tests lock in the
maintenance behavior (create/update/delete/move/cascade), the tie policy that
the retired with_date_range() annotation defined, the year filters (first-ever
coverage), and the recompute command's --verify contract.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from common.date_range import refresh_marking_date_ranges, suppress_date_range_recompute
from common.models import (
    Color, Cover, CoverMarking, DateSeen, Marking, MarkingRecycleBin, PostOffice,
)

User = get_user_model()


class DateRangeCacheBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("cache-admin", password="pw")
        self.color = Color.objects.create(name="Black", created_by=self.user, modified_by=self.user)
        self.post_office = PostOffice.objects.create(
            name="Petersburg", created_by=self.user, modified_by=self.user
        )

    def _marking(self, inscription="PETERSBURG VA"):
        return Marking.objects.create(
            type="TOWNMARK", inscription_txt=inscription, is_manuscript=True,
            color=self.color, post_office=self.post_office,
            created_by=self.user, modified_by=self.user,
        )

    def _cover(self, marking=None, code=None):
        cover = Cover.objects.create(code=code, created_by=self.user, modified_by=self.user)
        if marking is not None:
            CoverMarking.objects.create(
                cover=cover, marking=marking,
                created_by=self.user, modified_by=self.user,
            )
        return cover

    def _date(self, subject_type, subject_id, d, granularity="YEAR"):
        return DateSeen.objects.create(
            subject_type=subject_type, subject_id=subject_id,
            date=d, granularity=granularity,
            created_by=self.user, modified_by=self.user,
        )

    def _range(self, marking):
        marking.refresh_from_db()
        return (
            marking.earliest_seen, marking.earliest_seen_granularity,
            marking.latest_seen, marking.latest_seen_granularity,
        )


class DateRangeSignalTests(DateRangeCacheBase):
    def test_direct_date_create_update_delete_maintains_columns(self):
        marking = self._marking()
        self.assertEqual(self._range(marking), (None, None, None, None))

        row = self._date("MARKING", marking.pk, "1850-01-01")
        self.assertEqual(self._range(marking), (date(1850, 1, 1), "YEAR", date(1850, 1, 1), "YEAR"))

        row.date = "1848-06-01"
        row.granularity = "MONTH"
        row.save()
        self.assertEqual(self._range(marking), (date(1848, 6, 1), "MONTH", date(1848, 6, 1), "MONTH"))

        row.delete()
        self.assertEqual(self._range(marking), (None, None, None, None))

    def test_tie_policy_direct_granularity_beats_cover_on_equal_boundary(self):
        marking = self._marking()
        cover = self._cover(marking)
        self._date("MARKING", marking.pk, "1850-01-01", "YEAR")
        self._date("COVER", cover.pk, "1850-01-01", "DAY")

        # Equal earliest boundary: the direct MARKING row supplies granularity.
        self.assertEqual(self._range(marking)[0:2], (date(1850, 1, 1), "YEAR"))
        self.assertEqual(self._range(marking)[2:4], (date(1850, 1, 1), "YEAR"))

    def test_cover_date_widens_and_fans_out_to_all_linked_markings(self):
        m1, m2 = self._marking("A"), self._marking("B")
        cover = self._cover(m1)
        CoverMarking.objects.create(
            cover=cover, marking=m2, created_by=self.user, modified_by=self.user
        )
        self._date("MARKING", m1.pk, "1850-01-01")
        cover_date = self._date("COVER", cover.pk, "1861-04-12", "DAY")

        self.assertEqual(self._range(m1)[2], date(1861, 4, 12))
        self.assertEqual(self._range(m2), (date(1861, 4, 12), "DAY", date(1861, 4, 12), "DAY"))

        cover_date.delete()
        self.assertEqual(self._range(m1)[2], date(1850, 1, 1))
        self.assertEqual(self._range(m2), (None, None, None, None))

    def test_date_seen_subject_move_refreshes_both_markings(self):
        m1, m2 = self._marking("A"), self._marking("B")
        row = self._date("MARKING", m1.pk, "1850-01-01")
        self.assertEqual(self._range(m1)[0], date(1850, 1, 1))

        row.subject_id = m2.pk
        row.save()

        self.assertEqual(self._range(m1), (None, None, None, None))
        self.assertEqual(self._range(m2)[0], date(1850, 1, 1))

    def test_cover_marking_unlink_and_cover_delete_cascade_refresh(self):
        marking = self._marking()
        cover = self._cover(marking)
        self._date("COVER", cover.pk, "1855-01-01", "DAY")
        self.assertEqual(self._range(marking)[0], date(1855, 1, 1))

        CoverMarking.objects.filter(cover=cover, marking=marking).delete()
        self.assertEqual(self._range(marking), (None, None, None, None))

        # Re-link, then delete the whole cover: the CASCADE to CoverMarking
        # must fire the refresh even though nobody deletes the link directly.
        CoverMarking.objects.create(
            cover=cover, marking=marking, created_by=self.user, modified_by=self.user
        )
        self.assertEqual(self._range(marking)[0], date(1855, 1, 1))
        cover.delete()
        self.assertEqual(self._range(marking), (None, None, None, None))

    def test_recycle_binned_marking_stays_fresh(self):
        marking = self._marking()
        MarkingRecycleBin.objects.create(marking=marking, removed_by=self.user)
        self.assertFalse(Marking.objects.filter(pk=marking.pk).exists())

        self._date("MARKING", marking.pk, "1850-01-01")
        binned = Marking.all_objects.get(pk=marking.pk)
        self.assertEqual(binned.earliest_seen, date(1850, 1, 1))

    def test_suppress_context_skips_then_explicit_refresh_converges(self):
        marking = self._marking()
        with suppress_date_range_recompute():
            self._date("MARKING", marking.pk, "1850-01-01")
            self.assertEqual(self._range(marking), (None, None, None, None))

        refresh_marking_date_ranges([marking.pk])
        self.assertEqual(self._range(marking)[0], date(1850, 1, 1))


class DateRangeApiTests(DateRangeCacheBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_api_date_seen_crud_maintains_columns(self):
        marking = self._marking()
        resp = self.client.post("/api/v2/dates-seen/", {
            "subject_type": "MARKING", "subject_id": marking.pk,
            "date": "1850-01-01", "granularity": "YEAR",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(self._range(marking)[0], date(1850, 1, 1))

        resp = self.client.delete(f"/api/v2/dates-seen/{resp.data['id']}/")
        self.assertIn(resp.status_code, (200, 204))
        self.assertEqual(self._range(marking), (None, None, None, None))

    def test_year_filters_use_columns(self):
        early = self._marking("EARLY")
        late = self._marking("LATE")
        undated = self._marking("UNDATED")
        self._date("MARKING", early.pk, "1810-01-01")
        self._date("MARKING", late.pk, "1890-01-01")

        resp = self.client.get("/api/v2/markings/?earliest_use_year_min=1850")
        ids = {r["id"] for r in resp.data["results"]}
        self.assertEqual(ids, {late.pk})

        resp = self.client.get("/api/v2/markings/?latest_use_year_max=1850")
        ids = {r["id"] for r in resp.data["results"]}
        self.assertEqual(ids, {early.pk})
        self.assertNotIn(undated.pk, ids)

    def test_year_filters_match_displayed_range_boundaries(self):
        spanning = self._marking("SPANNING")
        before = self._marking("BEFORE")
        after = self._marking("AFTER")
        self._date("MARKING", spanning.pk, "1792-01-01")
        self._date("MARKING", spanning.pk, "1899-01-01")
        self._date("MARKING", before.pk, "1791-01-01")
        self._date("MARKING", after.pk, "1900-01-01")

        resp = self.client.get("/api/v2/markings/?earliest_use_year_min=1899")
        ids = {r["id"] for r in resp.data["results"]}
        self.assertEqual(ids, {after.pk})

        resp = self.client.get("/api/v2/markings/?latest_use_year_max=1792")
        ids = {r["id"] for r in resp.data["results"]}
        self.assertEqual(ids, {before.pk})

        resp = self.client.get("/api/v2/markings/?latest_use_year_max=1791")
        ids = {r["id"] for r in resp.data["results"]}
        self.assertEqual(ids, {before.pk})

        resp = self.client.get(
            "/api/v2/markings/?earliest_use_year_min=1899&latest_use_year_max=1899"
        )
        ids = {r["id"] for r in resp.data["results"]}
        self.assertEqual(ids, set())

    def test_markings_range_uses_searchable_marking_columns(self):
        active = self._marking("ACTIVE")
        hidden = self._marking("HIDDEN")
        cover = self._cover(hidden, code="H-1")
        self._date("MARKING", active.pk, "1850-01-01")
        self._date("COVER", cover.pk, "1899-01-01")
        MarkingRecycleBin.objects.create(marking=hidden, removed_by=self.user)

        resp = self.client.get("/api/v2/markings-range/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data, {"earliest_year": 1850, "latest_year": 1850})

    def test_ordering_by_earliest_seen(self):
        b = self._marking("B-1890")
        a = self._marking("A-1810")
        undated = self._marking("UNDATED")
        self._date("MARKING", a.pk, "1810-01-01")
        self._date("MARKING", b.pk, "1890-01-01")

        resp = self.client.get("/api/v2/markings/?ordering=earliest_seen,id&page_size=10")
        ids = [r["id"] for r in resp.data["results"]]
        # MySQL sorts NULLs first ascending — same behavior the annotation had.
        self.assertEqual(ids, [undated.pk, a.pk, b.pk])


class RecomputeCommandTests(DateRangeCacheBase):
    def test_verify_detects_and_recompute_repairs_corruption(self):
        marking = self._marking()
        self._date("MARKING", marking.pk, "1850-01-01")

        call_command("recompute_marking_date_ranges", "--all", "--verify")

        Marking.all_objects.filter(pk=marking.pk).update(earliest_seen=date(1700, 1, 1))
        with self.assertRaises(CommandError):
            call_command("recompute_marking_date_ranges", "--all", "--verify")

        call_command("recompute_marking_date_ranges", "--ids", str(marking.pk))
        self.assertEqual(self._range(marking)[0], date(1850, 1, 1))
        call_command("recompute_marking_date_ranges", "--all", "--verify")
