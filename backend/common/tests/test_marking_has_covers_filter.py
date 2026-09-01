"""
Issue #133 -- "has covers" search filter on the markings list.

Ian, 2026-08-27: "Build in a search for 'has covers'." A marking has covers when
at least one non-removed Cover is linked to it through CoverMarking.

⛔ The trap this filter exists inside is issue #29's, and it is silent. Cover
sets base_manager_name='all_objects', so any FK traversal (cover__...,
cover_markings__isnull=False) resolves through the manager that INCLUDES
recycle-binned covers. A marking whose only cover was removed would answer
"yes" and there would be nothing in the response to say so -- which is why
`test_recycle_binned_cover_does_not_count` is the load-bearing case here, not
the happy path.

Also asserts that has_covers and institutional stay DISTINCT questions: a
marking with a non-institutional cover must appear under has_covers and must
not appear under institutional. Folding them together is the obvious wrong
implementation and nothing else would catch it.

Mirrors test_marking_institutional_filter.py, which covers the same traversal.
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


class MarkingHasCoversFilterTests(TestCase):
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

        # Four markings spanning every answer the filter can give:
        #   one with a live institutional cover
        #   one with a live non-institutional cover  (has_covers yes, institutional no)
        #   one whose ONLY cover is recycle-binned   (must NOT count)
        #   one with no cover at all
        self.inst_marking = self._marking("institutional-cover")
        self.plain_marking = self._marking("plain-cover")
        self.removed_marking = self._marking("removed-cover")
        self.no_cover_marking = self._marking("no-cover")

        removed_cover = self._cover(is_institutional=False)
        CoverRecycleBin.objects.create(cover=removed_cover, removed_by=self.admin)

        self._link(self.inst_marking, self._cover(is_institutional=True))
        self._link(self.plain_marking, self._cover(is_institutional=False))
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

    def _has_covers(self):
        response = self.client.get(
            "/api/v2/markings/", {"has_covers": "true", "page_size": "50"}
        )
        self.assertEqual(response.status_code, 200)
        return _result_ids(response)

    def test_returns_only_markings_with_a_live_cover(self):
        self.assertEqual(
            self._has_covers(),
            {self.inst_marking.id, self.plain_marking.id},
        )

    def test_recycle_binned_cover_does_not_count(self):
        """⭐ The one that fails silently if the traversal is wrong (issue #29)."""
        self.assertNotIn(self.removed_marking.id, self._has_covers())

    def test_marking_with_no_cover_is_excluded(self):
        self.assertNotIn(self.no_cover_marking.id, self._has_covers())

    def test_has_covers_is_not_the_institutional_question(self):
        """The two filters must not be folded into one."""
        institutional = self.client.get(
            "/api/v2/markings/", {"institutional": "true", "page_size": "50"}
        )
        # A plain cover makes a marking "has covers" but NOT "institutional".
        self.assertIn(self.plain_marking.id, self._has_covers())
        self.assertNotIn(self.plain_marking.id, _result_ids(institutional))

    def test_blank_or_absent_does_not_filter(self):
        all_ids = {
            self.inst_marking.id,
            self.plain_marking.id,
            self.removed_marking.id,
            self.no_cover_marking.id,
        }
        blank = self.client.get(
            "/api/v2/markings/", {"has_covers": "", "page_size": "50"}
        )
        self.assertEqual(_result_ids(blank) & all_ids, all_ids)

        absent = self.client.get("/api/v2/markings/", {"page_size": "50"})
        self.assertEqual(_result_ids(absent) & all_ids, all_ids)
