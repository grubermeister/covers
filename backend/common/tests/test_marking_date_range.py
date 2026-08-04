from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from common.api.v2.serializers import MarkingSerializer
from common.models import Color, Cover, CoverMarking, DateSeen, Image, Marking, PostOffice, Region


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

    def test_with_date_range_includes_boundary_cover_ids(self):
        marking = self._make_marking("RICHMOND VA")
        early_cover = Cover.objects.create(
            code="C-early",
            created_by=self.user,
            modified_by=self.user,
        )
        late_cover = Cover.objects.create(
            code="C-late",
            created_by=self.user,
            modified_by=self.user,
        )
        for cover in (early_cover, late_cover):
            CoverMarking.objects.create(
                cover=cover,
                marking=marking,
                created_by=self.user,
                modified_by=self.user,
            )
        self._add_date(DateSeen.SUBJECT_COVER, early_cover.pk, "1840-01-01", "YEAR")
        self._add_date(DateSeen.SUBJECT_COVER, late_cover.pk, "1865-08-14", "DAY")

        annotated = Marking.objects.with_date_range().get(pk=marking.pk)
        data = MarkingSerializer(annotated).data

        self.assertEqual(annotated.earliest_seen_cover_id, early_cover.pk)
        self.assertEqual(annotated.latest_seen_cover_id, late_cover.pk)
        self.assertEqual(data["earliest_seen_cover_id"], early_cover.pk)
        self.assertEqual(data["latest_seen_cover_id"], late_cover.pk)

    def test_with_date_range_does_not_link_direct_boundary_dates(self):
        marking = self._make_marking("RICHMOND VA")
        cover = Cover.objects.create(
            code="C-tie",
            created_by=self.user,
            modified_by=self.user,
        )
        CoverMarking.objects.create(
            cover=cover,
            marking=marking,
            created_by=self.user,
            modified_by=self.user,
        )
        self._add_date(DateSeen.SUBJECT_MARKING, marking.pk, "1850-01-01", "YEAR")
        self._add_date(DateSeen.SUBJECT_COVER, cover.pk, "1850-01-01", "YEAR")

        annotated = Marking.objects.with_date_range().get(pk=marking.pk)

        self.assertEqual(annotated.earliest_seen.isoformat(), "1850-01-01")
        self.assertEqual(annotated.latest_seen.isoformat(), "1850-01-01")
        self.assertIsNone(annotated.earliest_seen_cover_id)
        self.assertIsNone(annotated.latest_seen_cover_id)

    def test_with_date_range_ignores_unapproved_cover_links(self):
        marking = self._make_marking("RICHMOND VA")
        pending_cover = Cover.objects.create(
            code="C-pending",
            created_by=self.user,
            modified_by=self.user,
        )
        CoverMarking.objects.create(
            cover=pending_cover,
            marking=marking,
            review_status=CoverMarking.REVIEW_PENDING,
            created_by=self.user,
            modified_by=self.user,
        )
        self._add_date(DateSeen.SUBJECT_COVER, pending_cover.pk, "1840-01-01", "YEAR")
        self._add_date(DateSeen.SUBJECT_MARKING, marking.pk, "1850-01-01", "YEAR")

        annotated = Marking.objects.with_date_range().get(pk=marking.pk)

        self.assertEqual(annotated.earliest_seen.isoformat(), "1850-01-01")
        self.assertEqual(annotated.latest_seen.isoformat(), "1850-01-01")
        self.assertIsNone(annotated.earliest_seen_cover_id)
        self.assertIsNone(annotated.latest_seen_cover_id)

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

    def test_dates_seen_subject_date_granularity_is_unique(self):
        marking = self._make_marking("ALEXANDRIA VA")
        self._add_date(DateSeen.SUBJECT_MARKING, marking.pk, "1850-01-01", "YEAR")

        with self.assertRaises(IntegrityError):
            self._add_date(DateSeen.SUBJECT_MARKING, marking.pk, "1850-01-01", "YEAR")

    def test_partial_dates_without_generated_date_do_not_affect_range(self):
        marking = self._make_marking("PETERSBURG VA")
        cover = Cover.objects.create(
            code="C-10",
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
            date="1850-01-01",
            granularity="YEAR",
            created_by=self.user,
            modified_by=self.user,
        )
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_COVER,
            subject_id=cover.pk,
            date_year=1840,
            date_day=12,
            granularity="YEAR_DAY",
            created_by=self.user,
            modified_by=self.user,
        )
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_COVER,
            subject_id=cover.pk,
            date_month=6,
            date_day=12,
            granularity="MONTH_DAY",
            created_by=self.user,
            modified_by=self.user,
        )

        annotated = Marking.objects.with_date_range().get(pk=marking.pk)

        self.assertEqual(annotated.earliest_seen.isoformat(), "1850-01-01")
        self.assertEqual(annotated.latest_seen.isoformat(), "1850-01-01")

    def test_partial_date_components_are_unique(self):
        marking = self._make_marking("NORFOLK VA")
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_MARKING,
            subject_id=marking.pk,
            date_month=6,
            granularity="MONTH_ONLY",
            created_by=self.user,
            modified_by=self.user,
        )

        with self.assertRaises(IntegrityError):
            DateSeen.objects.create(
                subject_type=DateSeen.SUBJECT_MARKING,
                subject_id=marking.pk,
                date_month=6,
                granularity="MONTH_ONLY",
                created_by=self.user,
                modified_by=self.user,
            )

    def test_region_code_is_unique(self):
        Region.objects.create(
            code="USA-VA1",
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.user,
            modified_by=self.user,
        )

        with self.assertRaises(IntegrityError):
            Region.objects.create(
                code="USA-VA1",
                name="Virginia Duplicate",
                abbrev="VD",
                region_tier="STATE",
                created_by=self.user,
                modified_by=self.user,
            )

    def test_post_office_code_is_unique(self):
        PostOffice.objects.create(
            code="USA-VA1-1",
            name="Alexandria",
            created_by=self.user,
            modified_by=self.user,
        )

        with self.assertRaises(IntegrityError):
            PostOffice.objects.create(
                code="USA-VA1-1",
                name="Alexandria Duplicate",
                created_by=self.user,
                modified_by=self.user,
            )

    def test_image_storage_filename_subject_tuple_is_unique(self):
        marking = self._make_marking("PETERSBURG VA")
        Image.objects.create(
            subject_type=Image.SUBJECT_MARKING,
            subject_id=marking.pk,
            original_filename="marking.png",
            storage_filename="va/marking.png",
            file_checksum="abc123",
            mime_type="image/png",
            image_width=20,
            image_height=10,
            file_size_bytes=100,
            image_view="FULL",
            uploaded_by=self.user,
            created_by=self.user,
            modified_by=self.user,
        )

        with self.assertRaises(IntegrityError):
            Image.objects.create(
                subject_type=Image.SUBJECT_MARKING,
                subject_id=marking.pk,
                original_filename="marking.png",
                storage_filename="va/marking.png",
                file_checksum="abc123",
                mime_type="image/png",
                image_width=20,
                image_height=10,
                file_size_bytes=100,
                image_view="FULL",
                uploaded_by=self.user,
                created_by=self.user,
                modified_by=self.user,
            )
