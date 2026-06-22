from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import Color, Marking, PostOffice


User = get_user_model()


def _make_marking(user, color, post_office, *, mtype, text):
    return Marking.objects.create(
        type=mtype,
        catalog_txt=text,
        inscription_txt=text,
        desc="",
        is_manuscript=True,
        color=color,
        post_office=post_office,
        created_by=user,
        modified_by=user,
    )


def _result_ids(response):
    return {row["id"] for row in response.data["results"]}


class MarkingPaidRateSearchTests(TestCase):
    """#40 -- the public marking text search must find paid/free/rate markings.

    "PAID"/"FREE" live as free text in catalog_txt/inscription_txt (no
    structured rate-keyword field exists), and `type` is now in search_fields so
    a class search ("ratemark") resolves by marking type.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("paid-rate-search-user", password="pw")
        self.color = Color.objects.create(
            name="Black", created_by=self.user, modified_by=self.user
        )
        self.post_office = PostOffice.objects.create(
            name="Richmond", created_by=self.user, modified_by=self.user
        )
        self.paid = _make_marking(
            self.user, self.color, self.post_office,
            mtype="RATEMARK", text="RICHMOND 5 PAID",
        )
        self.free = _make_marking(
            self.user, self.color, self.post_office,
            mtype="AUXMARK", text="RICHMOND FREE",
        )
        self.plain_town = _make_marking(
            self.user, self.color, self.post_office,
            mtype="TOWNMARK", text="RICHMOND blue circle",
        )

    def test_search_paid_matches_only_the_paid_marking(self):
        response = self.client.get(
            "/api/v2/markings/", {"search": "paid", "page_size": "50"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), {self.paid.pk})

    def test_search_free_matches_only_the_free_marking(self):
        response = self.client.get(
            "/api/v2/markings/", {"search": "free", "page_size": "50"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), {self.free.pk})

    def test_search_by_marking_class_matches_all_of_that_type(self):
        # `type` is in search_fields: searching the stored enum value finds the
        # whole class. (The human searches "ratemark"; the column holds
        # "RATEMARK"; icontains is case-insensitive.)
        response = self.client.get(
            "/api/v2/markings/", {"search": "ratemark", "page_size": "50"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), {self.paid.pk})
