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
    DateSeen,
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


class WvVaCrossListingTests(TestCase):
    """Issue #123 -- pre-statehood WV markings also appear under Virginia.

    West Virginia separated from Virginia on 1863-06-20, so a marking used at a
    WV town before then was, at the time, a Virginia marking. It cross-lists.

    THE RULE IS EVIDENCE-REQUIRED (Reese, 2026-08-25): "under no circumstances
    should it cross unless told. If any date is predated before June 20 1863,
    then you mark it as cross." An undated marking therefore does NOT cross --
    absence of a date is not evidence of an early one.

    The boundary is INCLUSIVE (Ian, 2026-08-24): "count it as both. So THAT DAY
    should be under a WV and VA." A marking dated exactly 1863-06-20 appears
    under both states.

    These subclass the same walk-every-page discipline as the fan-out tests
    above, and for the same reason: widening a filter with an OR is exactly the
    change that looks right on page 1 and serves a row twice on page 9.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            "crosslist-admin", email="cross@example.com", password="pw"
        )
        self.color = Color.objects.create(
            name="Black", created_by=self.user, modified_by=self.user
        )
        self.virginia = self._region("Virginia", "VA", "STATE", established="1788-06-25")
        self.west_virginia = self._region(
            "West Virginia", "WV", "STATE", established="1863-06-20")
        # A county link on the WV town, carrying the state's abbrev, so the
        # junction is multi-valued and any fan-out in the new OR is exposed.
        self.wv_county = self._region(
            "Berkeley", "WV", "COUNTY", parent=self.west_virginia)

        self.va_town = self._town("Berryville", [self.virginia])
        self.wv_town = self._town(
            "Berkeley Springs", [self.west_virginia, self.wv_county])

        # Virginia's own marking -- must be unaffected by the rule.
        self.va_marking = self._marking(self.va_town, "BERRYVILLE VA")

        # The boundary, to the day. before/on cross-list; after does not.
        self.wv_before = self._dated("WV BEFORE", "1863-06-19", "DAY")
        self.wv_on_boundary = self._dated("WV BOUNDARY", "1863-06-20", "DAY")
        self.wv_after = self._dated("WV AFTER", "1863-06-21", "DAY")

        # A bare year stores as its FLOOR, 1863-01-01 (models.py
        # generated_date_for_parts). It therefore qualifies. Pinned explicitly
        # so the behaviour is a decision rather than an accident of storage.
        self.wv_year_only = self._dated("WV YEAR ONLY", "1863-01-01", "YEAR")

        # In use either side of the boundary: it WAS a Virginia marking for
        # part of its life, so it cross-lists.
        self.wv_spanning = self._dated("WV SPANNING", "1860-04-01", "DAY")
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_MARKING, subject_id=self.wv_spanning.pk,
            date="1870-04-01", granularity="DAY",
            created_by=self.user, modified_by=self.user,
        )

        # No DateSeen at all -- no evidence, so no cross-listing.
        self.wv_undated = self._marking(self.wv_town, "WV UNDATED")

    def _region(self, name, abbrev, tier, parent=None, established=None):
        return Region.objects.create(
            name=name, abbrev=abbrev, region_tier=tier, parent_region=parent,
            established_date=established,
            created_by=self.user, modified_by=self.user,
        )

    def _town(self, name, regions):
        town = PostOffice.objects.create(
            name=name, created_by=self.user, modified_by=self.user)
        for region in regions:
            PostOfficeRegion.objects.create(
                post_office=town, region=region,
                created_by=self.user, modified_by=self.user,
            )
        return town

    def _marking(self, post_office, inscription):
        return Marking.objects.create(
            type="TOWNMARK", is_manuscript=False, inscription_txt=inscription,
            post_office=post_office, color=self.color,
            created_by=self.user, modified_by=self.user,
        )

    def _dated(self, inscription, date, granularity):
        marking = self._marking(self.wv_town, inscription)
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_MARKING, subject_id=marking.pk,
            date=date, granularity=granularity,
            created_by=self.user, modified_by=self.user,
        )
        return marking

    def _walk(self, **params):
        ids, page = [], 1
        while True:
            response = self.client.get(
                "/api/v2/markings/", {**params, "page_size": PAGE_SIZE, "page": page})
            self.assertEqual(response.status_code, 200, response.data)
            ids.extend(row["id"] for row in response.data["results"])
            if not response.data["next"]:
                return ids, response.data["count"]
            page += 1

    def assertWalkIsClean(self, label, **params):
        ids, count = self._walk(**params)
        self.assertEqual(
            len(ids) - len(set(ids)), 0,
            f"{label}: {len(ids) - len(set(ids))} duplicated rows across pages")
        self.assertEqual(
            len(ids), count,
            f"{label}: served {len(ids)} rows for a reported count of {count}")
        return set(ids)

    def test_a_marking_dated_before_statehood_appears_under_virginia(self):
        served = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        self.assertIn(self.wv_before.pk, served)

    def test_the_boundary_date_itself_appears_under_both_states(self):
        """Ian: 'count it as both. So THAT DAY should be under a WV and VA.'

        This is the test an off-by-one `<` fails.
        """
        va = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        wv = self.assertWalkIsClean("?state=West Virginia", state="West Virginia")
        self.assertIn(self.wv_on_boundary.pk, va)
        self.assertIn(self.wv_on_boundary.pk, wv)

    def test_a_marking_dated_after_statehood_does_not_cross_list(self):
        served = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        self.assertNotIn(self.wv_after.pk, served)

    def test_an_undated_marking_does_not_cross_list(self):
        """Evidence required. A LEFT JOIN that treats 'no rows' as 'passes'
        breaks this one and nothing else."""
        served = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        self.assertNotIn(self.wv_undated.pk, served)

    def test_a_year_only_1863_date_cross_lists(self):
        """Stored as its floor, 1863-01-01, so it qualifies."""
        served = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        self.assertIn(self.wv_year_only.pk, served)

    def test_a_marking_spanning_the_boundary_cross_lists(self):
        served = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        self.assertIn(self.wv_spanning.pk, served)

    def test_a_cross_listed_marking_is_served_exactly_once(self):
        """The fan-out regression in its most likely new form: the WV town has
        a county link, so an OR that joins the junction twice emits doubles."""
        ids, count = self._walk(state="Virginia")
        self.assertEqual(len(ids), len(set(ids)), "cross-listed rows duplicated")
        self.assertEqual(len(ids), count)

    def test_cross_listing_is_one_directional(self):
        """A Virginia marking never appears under West Virginia."""
        served = self.assertWalkIsClean("?state=West Virginia", state="West Virginia")
        self.assertNotIn(self.va_marking.pk, served)

    def test_west_virginia_still_returns_all_its_own_markings(self):
        served = self.assertWalkIsClean("?state=West Virginia", state="West Virginia")
        for marking in (self.wv_before, self.wv_on_boundary, self.wv_after,
                        self.wv_year_only, self.wv_spanning, self.wv_undated):
            self.assertIn(marking.pk, served)

    def test_virginias_own_markings_are_unaffected(self):
        served = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        self.assertIn(self.va_marking.pk, served)

    def test_the_abbrev_form_cross_lists_the_same_way(self):
        """?state=VA and ?state=Virginia must agree -- they did not before #113."""
        by_name = self.assertWalkIsClean("?state=Virginia", state="Virginia")
        by_abbrev = self.assertWalkIsClean("?state=VA", state="VA")
        self.assertEqual(by_name, by_abbrev)

    def test_a_cross_listed_marking_is_flagged_in_the_payload(self):
        """The label's data source. Without it a reader sees a West Virginia
        town under Virginia with no explanation -- the State column correctly
        still reads "West Virginia", because a marking has one home state."""
        response = self.client.get(
            "/api/v2/markings/", {"state": "Virginia", "page_size": 50})
        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.data["results"]}

        crossed = rows[self.wv_before.pk]
        self.assertTrue(crossed["cross_listed_pre_statehood"])
        self.assertEqual(crossed["state"], "West Virginia")

        native = rows[self.va_marking.pk]
        self.assertFalse(native["cross_listed_pre_statehood"])
        self.assertEqual(native["state"], "Virginia")

    def test_an_undated_wv_marking_is_not_flagged(self):
        response = self.client.get(
            "/api/v2/markings/", {"state": "West Virginia", "page_size": 50})
        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.data["results"]}
        self.assertFalse(rows[self.wv_undated.pk]["cross_listed_pre_statehood"])
        self.assertTrue(rows[self.wv_before.pk]["cross_listed_pre_statehood"])

    def test_cross_listed_markings_interleave_by_town_under_a_state_filter(self):
        """Cross-listed rows must not clump after every Virginia marking.

        The default sort leads with the marking's own state name, which would
        put all of West Virginia after all of Virginia -- page 41 of 51 at live
        counts. With a state filter the leading key is dropped so towns
        interleave alphabetically: Berkeley Springs (WV) BEFORE Berryville (VA).
        """
        response = self.client.get(
            "/api/v2/markings/", {"state": "Virginia", "page_size": 50})
        self.assertEqual(response.status_code, 200)
        towns = [row["town"] for row in response.data["results"]]
        self.assertIn("Berkeley Springs", towns)
        self.assertIn("Berryville", towns)
        self.assertLess(
            towns.index("Berkeley Springs"), towns.index("Berryville"),
            f"cross-listed markings did not interleave by town: {towns}")

    def test_an_explicit_ordering_is_never_overridden(self):
        """?ordering= is the user's choice; the interleave must not fight it."""
        response = self.client.get(
            "/api/v2/markings/",
            {"state": "Virginia", "ordering": "id", "page_size": 50})
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids, sorted(ids))

    def test_without_a_state_filter_the_default_sort_is_unchanged(self):
        response = self.client.get("/api/v2/markings/", {"page_size": 50})
        self.assertEqual(response.status_code, 200)
        states = [row["state"] for row in response.data["results"]]
        # Virginia sorts before West Virginia when the region key still leads.
        self.assertEqual(states, sorted(states))
