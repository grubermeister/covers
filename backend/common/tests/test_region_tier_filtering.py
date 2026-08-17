"""
Issue #103 / LEFT_OFF.md B3 -- counties must not be offered as states.

import_vphc_reference creates county Regions with abbrev=<state abbrev>, so 141
rows carry 'VA'/'WV' and collide with the two real state rows. The ingest is
correct and nothing needs migrating; every defect is on the read side. Two
surfaces are covered here (the third, the marking list, is
test_marking_list_fanout):

  GET /regions/                    -> the Search / Contribute State dropdown
  GET /post-offices/town-options/  -> the submission-form autocomplete
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import PostOffice, PostOfficeRegion, Region

User = get_user_model()


class RegionTierFilteringTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            "admin", email="admin@example.com", password="pw"
        )
        self.state = self._region("Virginia", "VA", "STATE")
        self.territory = self._region("Northwest Territory", "NT", "TERRITORY")
        self.district = self._region("District of Columbia", "DC", "DISTRICT")
        self.country = self._region("United States of America", "USA", "COUNTRY")
        # Counties carry their STATE's abbrev -- this is the collision.
        self.county = self._region("Accomack", "VA", "COUNTY", parent=self.state)
        self.county_two = self._region("Campbell", "VA", "COUNTY", parent=self.state)

        self.town = self._town("Modesttown", [self.state, self.county])
        self.orphan = self._town("Quarantined", [self.county_two])

    def _region(self, name, abbrev, tier, parent=None):
        return Region.objects.create(
            name=name, abbrev=abbrev, region_tier=tier, parent_region=parent,
            created_by=self.user, modified_by=self.user,
        )

    def _town(self, name, regions):
        town = PostOffice.objects.create(
            name=name, created_by=self.user, modified_by=self.user
        )
        for region in regions:
            PostOfficeRegion.objects.create(
                post_office=town, region=region,
                created_by=self.user, modified_by=self.user,
            )
        return town

    # ------------------------------------------------------------- /regions/

    def test_region_tier_in_filter_excludes_counties(self):
        response = self.client.get(
            "/api/v2/regions/",
            {"region_tier__in": "STATE,TERRITORY,DISTRICT", "page_size": 100},
        )
        self.assertEqual(response.status_code, 200, response.data)
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(
            names,
            {"Virginia", "Northwest Territory", "District of Columbia"},
        )
        # The dropdown asks for exactly this set, so DC must be in it and the
        # country must not -- "United States of America" is not a state.
        self.assertNotIn("Accomack", names)
        self.assertNotIn("United States of America", names)

    def test_unfiltered_regions_still_returns_everything(self):
        # The filter is opt-in. Admin surfaces (Collections) still need the
        # full list, so the default must not silently change.
        response = self.client.get("/api/v2/regions/", {"page_size": 100})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 6)

    def test_exact_tier_filter_still_works(self):
        response = self.client.get("/api/v2/regions/", {"region_tier": "COUNTY"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["name"] for row in response.data["results"]},
            {"Accomack", "Campbell"},
        )

    # --------------------------------------------------------- town-options

    def test_town_options_never_offers_a_county_as_a_state(self):
        response = self.client.get("/api/v2/post-offices/town-options/")
        self.assertEqual(response.status_code, 200)
        rows = response.data
        self.assertNotIn(
            "Accomack", {row["state"] for row in rows},
            "a county is being offered as a state in the autocomplete",
        )
        self.assertIn({"town": "Modesttown", "state": "Virginia"}, rows)

    def test_town_options_keeps_a_town_whose_only_link_is_a_county(self):
        # The regression that the obvious .exclude() on PostOffice would cause:
        # excluding across a to-many relation is NOT EXISTS, so every town with
        # a county link disappears. Modesttown proves the common case survives;
        # Quarantined proves the county-only case survives with a blank state
        # rather than being told it is in Campbell.
        response = self.client.get("/api/v2/post-offices/town-options/")
        towns = {row["town"] for row in response.data}
        self.assertEqual(towns, {"Modesttown", "Quarantined"})
        self.assertIn({"town": "Quarantined", "state": ""}, response.data)

    def test_town_options_emits_one_row_per_town_and_primary_region(self):
        # Modesttown has two links but only one is a state, so exactly one row.
        response = self.client.get("/api/v2/post-offices/town-options/")
        modesttown = [r for r in response.data if r["town"] == "Modesttown"]
        self.assertEqual(len(modesttown), 1, modesttown)
