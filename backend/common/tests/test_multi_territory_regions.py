"""
Multi-territory support (Issue #24).

A PostOffice can be linked to several Regions over time via the
post_office_regions junction (e.g. Michigan Territory + Michigan). The
single-region `.region` property collapses that to one; `.regions` and the
serializer's `regions` field must expose all of them, current/active first.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from types import SimpleNamespace

from common.api.v2.serializers import MarkingSerializer, _marking_regions
from common.models import (
    Color,
    Marking,
    PostOffice,
    PostOfficeRegion,
    Region,
)

User = get_user_model()


class MultiTerritoryRegionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ed", password="pw")
        # Historical territory (defunct) + the state that succeeded it (active).
        self.territory = Region.objects.create(
            name="Michigan Territory",
            abbrev="MIT",
            region_tier="TERRITORY",
            established_date=date(1805, 6, 30),
            defunct_date=date(1837, 1, 26),
            created_by=self.user,
            modified_by=self.user,
        )
        self.state = Region.objects.create(
            name="Michigan",
            abbrev="MI",
            region_tier="STATE",
            established_date=date(1837, 1, 26),
            defunct_date=None,  # active
            created_by=self.user,
            modified_by=self.user,
        )
        self.post_office = PostOffice.objects.create(
            name="Detroit", created_by=self.user, modified_by=self.user
        )
        for region in (self.territory, self.state):
            PostOfficeRegion.objects.create(
                post_office=self.post_office,
                region=region,
                created_by=self.user,
                modified_by=self.user,
            )
        self.color = Color.objects.create(
            name="Black", created_by=self.user, modified_by=self.user
        )

    def _marking(self, post_office):
        return Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="DETROIT",
            is_manuscript=True,
            color=self.color,
            post_office=post_office,
            created_by=self.user,
            modified_by=self.user,
        )

    def test_model_regions_lists_all_linked_current_first(self):
        # .region still collapses to the single active region (back-compat)...
        self.assertEqual(self.post_office.region, self.state)
        # ...while .regions exposes the full history, active first.
        names = [r.name for r in self.post_office.regions]
        self.assertEqual(names, ["Michigan", "Michigan Territory"])

    def test_serializer_exposes_all_regions_with_fields(self):
        data = MarkingSerializer(self._marking(self.post_office)).data
        # The single-region fields are unchanged (active region wins).
        self.assertEqual(data["region_name"], "Michigan")
        # The new list carries every territory the town belonged to.
        self.assertEqual(
            data["regions"],
            [
                {"id": self.state.id, "name": "Michigan", "abbrev": "MI",
                 "region_tier": "STATE"},
                {"id": self.territory.id, "name": "Michigan Territory",
                 "abbrev": "MIT", "region_tier": "TERRITORY"},
            ],
        )

    def test_single_region_and_no_post_office_edges(self):
        # One link -> one-element list.
        solo_po = PostOffice.objects.create(
            name="Ypsilanti", created_by=self.user, modified_by=self.user
        )
        PostOfficeRegion.objects.create(
            post_office=solo_po, region=self.state,
            created_by=self.user, modified_by=self.user,
        )
        self.assertEqual(
            MarkingSerializer(self._marking(solo_po)).data["regions"],
            [{"id": self.state.id, "name": "Michigan", "abbrev": "MI",
              "region_tier": "STATE"}],
        )
        # Defensive path: a marking with no post office yields [] (the DB
        # forbids a null post_office on a real Marking, so use a stub).
        self.assertEqual(
            _marking_regions(SimpleNamespace(post_office_id=None, post_office=None)),
            [],
        )
