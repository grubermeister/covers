from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import Citation, Color, Cover, Marking, PostOffice, ReferenceWork


User = get_user_model()


def _make_reference_work(user, code, title):
    return ReferenceWork.objects.create(
        code=code,
        title=title,
        authorship="Author",
        publisher="Publisher",
        publication_year=1900,
        created_by=user,
        modified_by=user,
    )


def _make_marking(user, color, post_office, text):
    return Marking.objects.create(
        type="TOWNMARK",
        catalog_txt=text,
        inscription_txt=text,
        desc="",
        is_manuscript=True,
        color=color,
        post_office=post_office,
        created_by=user,
        modified_by=user,
    )


def _make_marking_with_text(user, color, post_office, catalog_txt, inscription_txt):
    return Marking.objects.create(
        type="TOWNMARK",
        catalog_txt=catalog_txt,
        inscription_txt=inscription_txt,
        desc="",
        is_manuscript=True,
        color=color,
        post_office=post_office,
        created_by=user,
        modified_by=user,
    )


def _result_ids(response):
    return {row["id"] for row in response.data["results"]}


class MarkingCitationSearchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("citation-search-user", password="pw")
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
        self.ascc = _make_reference_work(
            self.user,
            "ASCC1",
            "American Stampless Cover Catalog",
        )
        self.vphc = _make_reference_work(
            self.user,
            "VPHC1",
            "Virginia Postal History Catalog",
        )
        self.unrelated = _make_reference_work(
            self.user,
            "OTHER",
            "Unrelated Reference",
        )
        self.uncoded = _make_reference_work(
            self.user,
            "",
            "Uncoded Reference",
        )
        self.ascc_marking = _make_marking(
            self.user,
            self.color,
            self.post_office,
            "North landing manuscript",
        )
        self.vphc_marking = _make_marking(
            self.user,
            self.color,
            self.post_office,
            "River crossing manuscript",
        )
        self.unrelated_marking = _make_marking(
            self.user,
            self.color,
            self.post_office,
            "Mountain gap manuscript",
        )
        Citation.objects.create(
            reference_work=self.ascc,
            subject_type="MARKING",
            subject_id=self.ascc_marking.pk,
            citation_detail="p. 1",
            created_by=self.user,
            modified_by=self.user,
        )
        Citation.objects.create(
            reference_work=self.vphc,
            subject_type="MARKING",
            subject_id=self.vphc_marking.pk,
            citation_detail="p. 2",
            created_by=self.user,
            modified_by=self.user,
        )
        cover = Cover.objects.create(
            code="C-1",
            created_by=self.user,
            modified_by=self.user,
        )
        Citation.objects.create(
            reference_work=self.unrelated,
            subject_type="COVER",
            subject_id=cover.pk,
            citation_detail="p. 3",
            created_by=self.user,
            modified_by=self.user,
        )

    def test_reference_work_code_filter_matches_cited_markings(self):
        response = self.client.get(
            "/api/v2/markings/",
            {"reference_work_code": "ASCC1", "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), {self.ascc_marking.pk})

    def test_reference_work_code_filter_is_case_insensitive(self):
        response = self.client.get(
            "/api/v2/markings/",
            {"reference_work_code": "asCc1", "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), {self.ascc_marking.pk})

    def test_reference_work_code_filter_does_not_treat_ids_as_public_input(self):
        response = self.client.get(
            "/api/v2/markings/",
            {"reference_work_code": str(self.ascc.pk), "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), set())

    def test_top_search_matches_reference_work_code(self):
        response = self.client.get(
            "/api/v2/markings/",
            {"search": "ASCC1", "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), {self.ascc_marking.pk})

    def test_top_search_matches_reference_work_title_fragment_as_union(self):
        response = self.client.get(
            "/api/v2/markings/",
            {"search": "Catalog", "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), {self.ascc_marking.pk, self.vphc_marking.pk})

    def test_cover_citations_do_not_match_marking_search(self):
        response = self.client.get(
            "/api/v2/markings/",
            {"search": "OTHER", "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(_result_ids(response), set())

    def test_top_search_ignores_catalog_text(self):
        catalog_only = _make_marking_with_text(
            self.user,
            self.color,
            self.post_office,
            "CATALOGONLYTOKEN",
            "Visible inscription",
        )
        response = self.client.get(
            "/api/v2/markings/",
            {"search": "CATALOGONLYTOKEN", "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(catalog_only.pk, _result_ids(response))

    def test_top_search_still_matches_inscription_text(self):
        inscription_match = _make_marking_with_text(
            self.user,
            self.color,
            self.post_office,
            "Catalog text",
            "INSCRIPTIONONLYTOKEN",
        )
        response = self.client.get(
            "/api/v2/markings/",
            {"search": "INSCRIPTIONONLYTOKEN", "page_size": "50"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(inscription_match.pk, _result_ids(response))

    def test_reference_work_options_excludes_uncoded_works(self):
        response = self.client.get("/api/v2/reference-works/options/")

        self.assertEqual(response.status_code, 200, response.data)
        codes = {row["code"] for row in response.data}
        self.assertIn("ASCC1", codes)
        self.assertIn("VPHC1", codes)
        self.assertNotIn("", codes)
        self.assertNotIn(None, codes)
