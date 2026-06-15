from django.contrib.auth import get_user_model
from django.test import TestCase

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
