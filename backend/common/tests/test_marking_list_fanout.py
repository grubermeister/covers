"""
Issue #103 / LEFT_OFF.md B2 -- the marking list must not fan out on the
post_office_regions junction.

The VPHC ingest gave every VA/WV post office a second, COUNTY-tier region link
alongside its state. The list endpoint ordered across that junction with no
de-duplication, so each of those markings was emitted twice and an equal number
were pushed off the end by LIMIT/OFFSET: 502 duplicated and 502 unreachable on
woco.dev, while `count` stayed correct because Django strips ordering when it
counts.

These tests walk *every page* and compare against `?ordering=id`, because that
is the only thing that catches it -- a correct total is not evidence that
pagination is correct, and page 1 looks perfectly fine (the two copies of a
duplicated marking sort under different region names, so they land far apart).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Color,
    Marking,
    PostOffice,
    PostOfficeRegion,
    Region,
)

User = get_user_model()

# Small enough that the walk crosses several page boundaries, which is where
# LIMIT/OFFSET drops rows.
PAGE_SIZE = 3


class MarkingListFanoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            "admin", email="admin@example.com", password="pw"
        )
        self.color = Color.objects.create(
            name="Black", created_by=self.user, modified_by=self.user
        )

        # Virginia carries an established_date; its counties do not, and they
        # carry Virginia's abbrev. That is exactly what
        # import_vphc_reference._counties() produces.
        self.state = self._region("Virginia", "VA", "STATE", established="1788-06-25")
        self.county = self._region("Accomack", "VA", "COUNTY", parent=self.state)
        self.other_county = self._region("Albemarle", "VA", "COUNTY", parent=self.state)

        # One town with THREE region links (state + two counties) so the
        # fan-out is 3x, not 2x -- a 2x bug can hide behind an even page size.
        self.town = PostOffice.objects.create(
            name="Modesttown", created_by=self.user, modified_by=self.user
        )
        for region in (self.state, self.county, self.other_county):
            PostOfficeRegion.objects.create(
                post_office=self.town, region=region,
                created_by=self.user, modified_by=self.user,
            )

        # A second town in the same state with only its state link, so the
        # tests also prove the un-fanned-out rows are not collateral damage.
        self.clean_town = PostOffice.objects.create(
            name="Richmond", created_by=self.user, modified_by=self.user
        )
        PostOfficeRegion.objects.create(
            post_office=self.clean_town, region=self.state,
            created_by=self.user, modified_by=self.user,
        )

        self.expected_ids = set()
        for index in range(7):
            self.expected_ids.add(self._marking(self.town, index).pk)
        for index in range(4):
            self.expected_ids.add(self._marking(self.clean_town, index).pk)

    def _region(self, name, abbrev, tier, parent=None, established=None):
        return Region.objects.create(
            name=name, abbrev=abbrev, region_tier=tier, parent_region=parent,
            established_date=established,
            created_by=self.user, modified_by=self.user,
        )

    def _marking(self, post_office, index):
        return Marking.objects.create(
            type="TOWNMARK",
            is_manuscript=False,
            inscription_txt=f"{post_office.name.upper()} {index}",
            post_office=post_office,
            color=self.color,
            created_by=self.user,
            modified_by=self.user,
        )

    def _walk(self, **params):
        """Every id the list endpoint serves, in order, across all pages."""
        ids = []
        page = 1
        while True:
            response = self.client.get(
                "/api/v2/markings/",
                {**params, "page_size": PAGE_SIZE, "page": page},
            )
            self.assertEqual(response.status_code, 200, response.data)
            ids.extend(row["id"] for row in response.data["results"])
            if not response.data["next"]:
                return ids, response.data["count"]
            page += 1

    def assertWalkIsClean(self, label, **params):
        ids, count = self._walk(**params)
        self.assertEqual(
            len(ids) - len(set(ids)), 0,
            f"{label}: {len(ids) - len(set(ids))} duplicated rows across pages",
        )
        self.assertEqual(
            len(ids), count,
            f"{label}: served {len(ids)} rows for a reported count of {count}",
        )
        return set(ids)

    def test_default_ordering_serves_every_marking_exactly_once(self):
        # `?ordering=id` is ground truth: a single-column sort on the markings
        # table itself cannot fan out.
        truth, _ = self._walk(ordering="id")
        self.assertEqual(set(truth), self.expected_ids)

        served = self.assertWalkIsClean("default ordering")
        self.assertEqual(
            served, self.expected_ids,
            f"unreachable by browsing: {sorted(self.expected_ids - served)}",
        )

    def test_state_abbrev_filter_does_not_fan_out(self):
        # The counties carry abbrev='VA' too, so ?state=VA used to match the
        # state and both counties and return each Modesttown marking 3x.
        served = self.assertWalkIsClean("?state=VA", state="VA")
        self.assertEqual(served, self.expected_ids)

    def test_state_name_filter_still_matches_a_county_by_name(self):
        # Abbreviations are state-only; names are not. Looking up "Accomack"
        # is legitimate and must keep working.
        served = self.assertWalkIsClean("?state=Accomack", state="Accomack")
        self.assertEqual(
            served, {m.pk for m in Marking.objects.filter(post_office=self.town)},
        )

    def test_state_abbrev_does_not_match_a_county(self):
        # Albemarle county carries abbrev 'VA'; that must not make 'VA' mean
        # Albemarle, nor must Richmond (state-only) drop out of ?state=VA.
        served = self.assertWalkIsClean("?state=VA", state="VA")
        self.assertIn(
            Marking.objects.filter(post_office=self.clean_town).first().pk, served,
        )

    def test_retired_ordering_key_is_rewritten_not_honoured(self):
        # A bookmarked search URL still carries the junction spelling. Honour
        # it literally and the fan-out is back; reject it and the sort silently
        # changes under the user. It must be translated.
        served = self.assertWalkIsClean(
            "legacy ordering key",
            ordering="post_office__post_office_regions__region__name,id",
        )
        self.assertEqual(served, self.expected_ids)

    def test_primary_region_annotation_ignores_county_rows(self):
        # The annotation is what the default sort orders by; if a county could
        # win it, markings would sort under "Accomack" instead of "Virginia".
        response = self.client.get("/api/v2/markings/", {"page_size": PAGE_SIZE})
        self.assertEqual(response.status_code, 200)
        marking = Marking.objects.filter(post_office=self.town).first()
        self.assertEqual(self.town.region, self.state)
        from common.api.v2.views import _marking_list_queryset
        annotated = _marking_list_queryset().get(pk=marking.pk)
        self.assertEqual(annotated.primary_region_name, "Virginia")
        self.assertEqual(annotated.primary_region_abbrev, "VA")
