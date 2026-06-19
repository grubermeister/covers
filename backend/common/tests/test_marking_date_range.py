from django.contrib.auth import get_user_model
from django.test import TestCase

from common.api.v2.serializers import MarkingSerializer
from common.models import Color, Cover, CoverMarking, DateSeen, Marking, PostOffice


User = get_user_model()


class MarkingDateRangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("date-range-user", password="pw")
        self.color = Color.objects.create(
            name="Black",
            created_by=self.user,
            modified_by=self.user,
        )
        self.post_office = PostOffice.objects.create(
            name="Richmond",
            created_by=self.user,
            modified_by=self.user,
        )

    def test_with_date_range_includes_boundary_granularities(self):
        marking = Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="RICHMOND VA",
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.user,
            modified_by=self.user,
        )
        cover = Cover.objects.create(
            code="C-1",
            created_by=self.user,
            modified_by=self.user,
        )
        CoverMarking.objects.create(
            cover=cover,
            marking=marking,
            created_by=self.user,
            modified_by=self.user,
        )
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_MARKING,
            subject_id=marking.pk,
            date="1860-01-01",
            granularity="YEAR",
            created_by=self.user,
            modified_by=self.user,
        )
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_COVER,
            subject_id=cover.pk,
            date="1865-08-14",
            granularity="DAY",
            created_by=self.user,
            modified_by=self.user,
        )

        annotated = Marking.objects.with_date_range().get(pk=marking.pk)

        self.assertEqual(annotated.earliest_seen.isoformat(), "1860-01-01")
        self.assertEqual(annotated.earliest_seen_granularity, "YEAR")
        self.assertEqual(annotated.latest_seen.isoformat(), "1865-08-14")
        self.assertEqual(annotated.latest_seen_granularity, "DAY")

    def _make_marking(self, inscription):
        return Marking.objects.create(
            type="TOWNMARK",
            inscription_txt=inscription,
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.user,
            modified_by=self.user,
        )

    def _add_date(self, subject_type, subject_id, date, granularity="YEAR"):
        return DateSeen.objects.create(
            subject_type=subject_type,
            subject_id=subject_id,
            date=date,
            granularity=granularity,
            created_by=self.user,
            modified_by=self.user,
        )

    def test_serializer_dates_seen_lists_marking_rows_ordered_and_scoped(self):
        # issue #25: the detail serializer exposes the full set of MARKING-scoped
        # DateSeen rows (the "Dates Seen" listing), ordered by date, and must not
        # leak COVER rows or another marking's rows.
        marking = self._make_marking("AQUILA VA")
        other = self._make_marking("AMELIA VA")
        cover = Cover.objects.create(code="C-9", created_by=self.user, modified_by=self.user)

        # Insert out of order to prove the serializer sorts.
        for d in ("1855-01-01", "1811-01-01", "1849-01-01"):
            self._add_date(DateSeen.SUBJECT_MARKING, marking.pk, d)
        # These two must be excluded: a cover date and another marking's date.
        self._add_date(DateSeen.SUBJECT_COVER, cover.pk, "1900-01-01", "DAY")
        self._add_date(DateSeen.SUBJECT_MARKING, other.pk, "1700-01-01")

        rows = MarkingSerializer().get_dates_seen(marking)

        self.assertEqual(
            [r["date"] for r in rows],
            ["1811-01-01", "1849-01-01", "1855-01-01"],
        )
        self.assertTrue(
            all(
                r["subject_type"] == DateSeen.SUBJECT_MARKING and r["subject_id"] == marking.pk
                for r in rows
            )
        )
