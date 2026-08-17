"""
Issue #104 / C3 -- exact `post_office` filter on the markings list.

The marking detail page's "move image to another marking" picker needs every
marking at one post office and nothing else. The pre-existing `town` filter
cannot do that job: it is `post_office__name__icontains`, so "Richmond" also
matches "New Richmond", and a same-named town in another state matches too.
A candidate missing from that list is an editor who cannot complete the move,
so the filter has to key on the office id.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import Color, Marking, PostOffice


User = get_user_model()


def _result_ids(response):
    return {row["id"] for row in response.data["results"]}


class MarkingPostOfficeFilterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            "admin", email="admin@example.com", password="pw"
        )
        self.color = Color.objects.create(
            name="Black", created_by=self.admin, modified_by=self.admin
        )

        self.richmond = self._office("Richmond")
        # Name-collides with Richmond under `icontains`.
        self.new_richmond = self._office("New Richmond")

        self.a = self._marking(self.richmond, "richmond-a")
        self.b = self._marking(self.richmond, "richmond-b")
        self.other = self._marking(self.new_richmond, "new-richmond-a")

    def _office(self, name):
        return PostOffice.objects.create(
            name=name, created_by=self.admin, modified_by=self.admin
        )

    def _marking(self, post_office, text):
        return Marking.objects.create(
            type="TOWNMARK",
            catalog_txt=text,
            inscription_txt=text,
            desc="",
            is_manuscript=True,
            color=self.color,
            post_office=post_office,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def test_returns_every_marking_at_that_office_and_nothing_else(self):
        response = self.client.get(
            "/api/v2/markings/",
            {"post_office": str(self.richmond.id), "page_size": "50"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_result_ids(response), {self.a.id, self.b.id})

    def test_name_colliding_office_is_excluded(self):
        """The whole reason this filter exists -- `town` would return both."""
        by_id = self.client.get(
            "/api/v2/markings/",
            {"post_office": str(self.richmond.id), "page_size": "50"},
        )
        self.assertNotIn(self.other.id, _result_ids(by_id))

        by_name = self.client.get(
            "/api/v2/markings/", {"town": "Richmond", "page_size": "50"}
        )
        self.assertIn(self.other.id, _result_ids(by_name))

    def test_absent_does_not_filter(self):
        response = self.client.get("/api/v2/markings/", {"page_size": "50"})
        self.assertEqual(
            _result_ids(response), {self.a.id, self.b.id, self.other.id}
        )

    def test_unknown_office_returns_empty(self):
        response = self.client.get(
            "/api/v2/markings/", {"post_office": "999999", "page_size": "50"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_result_ids(response), set())
