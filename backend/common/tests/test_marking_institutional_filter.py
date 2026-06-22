"""
Issue #29 -- "institutional" search filter on the markings list.

"Institutional" lives on Cover (is_institutional); a marking is institutional
when at least one of its non-removed covers is. Covers the load-bearing slice:
the filter selects only markings with an institutional cover, excludes
recycle-binned covers, and is a no-op when blank/absent.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Color,
    Cover,
    CoverMarking,
    CoverRecycleBin,
    Marking,
    PostOffice,
)


User = get_user_model()


def _result_ids(response):
    return {row["id"] for row in response.data["results"]}


class MarkingInstitutionalFilterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            "admin", email="admin@example.com", password="pw"
        )
        self.color = Color.objects.create(
            name="Black", created_by=self.admin, modified_by=self.admin
        )
        self.post_office = PostOffice.objects.create(
            name="Richmond", created_by=self.admin, modified_by=self.admin
        )

        # Three markings: one with an institutional cover, one with a
        # non-institutional cover, one whose only institutional cover is
        # recycle-binned (must NOT count).
        self.inst_marking = self._marking("institutional")
        self.plain_marking = self._marking("plain")
        self.removed_marking = self._marking("removed-institutional")

        inst_cover = self._cover(is_institutional=True)
        plain_cover = self._cover(is_institutional=False)
        removed_cover = self._cover(is_institutional=True)
        # Soft-remove the institutional cover by putting it in the recycle bin.
        CoverRecycleBin.objects.create(cover=removed_cover, removed_by=self.admin)

        self._link(self.inst_marking, inst_cover)
        self._link(self.plain_marking, plain_cover)
        self._link(self.removed_marking, removed_cover)

    def _marking(self, text):
        return Marking.objects.create(
            type="TOWNMARK",
            catalog_txt=text,
            inscription_txt=text,
            desc="",
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _cover(self, *, is_institutional):
        return Cover.objects.create(
            is_institutional=is_institutional,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _link(self, marking, cover):
        return CoverMarking.objects.create(
            marking=marking,
            cover=cover,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def test_institutional_true_returns_only_markings_with_institutional_cover(self):
        response = self.client.get(
            "/api/v2/markings/", {"institutional": "true", "page_size": "50"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_result_ids(response), {self.inst_marking.id})

    def test_recycle_binned_institutional_cover_does_not_count(self):
        response = self.client.get(
            "/api/v2/markings/", {"institutional": "true", "page_size": "50"}
        )
        self.assertNotIn(self.removed_marking.id, _result_ids(response))

    def test_blank_or_absent_does_not_filter(self):
        all_ids = {
            self.inst_marking.id,
            self.plain_marking.id,
            self.removed_marking.id,
        }
        blank = self.client.get(
            "/api/v2/markings/", {"institutional": "", "page_size": "50"}
        )
        self.assertEqual(_result_ids(blank) & all_ids, all_ids)

        absent = self.client.get("/api/v2/markings/", {"page_size": "50"})
        self.assertEqual(_result_ids(absent) & all_ids, all_ids)
