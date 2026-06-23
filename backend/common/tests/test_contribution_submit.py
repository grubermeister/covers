from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Collection,
    Color,
    Contribution,
    Image,
    Marking,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
)


User = get_user_model()


class ContributionSubmitMarkingEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="contributor", password="pw")
        self.region = Region.objects.create(
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.user,
            modified_by=self.user,
        )
        self.collection = Collection.objects.create(
            name="Virginia",
            region=self.region,
            created_by=self.user,
            modified_by=self.user,
        )
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
        PostOfficeRegion.objects.create(
            post_office=self.post_office,
            region=self.region,
            created_by=self.user,
            modified_by=self.user,
        )
        self.marking = Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="RICHMOND VA",
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.user,
            modified_by=self.user,
        )
        self.image = Image.objects.create(
            subject_type=Image.SUBJECT_MARKING,
            subject_id=self.marking.pk,
            original_filename="front.jpg",
            storage_filename="va/front.jpg",
            file_checksum="abc123",
            mime_type="image/jpeg",
            image_width=800,
            image_height=600,
            file_size_bytes=12345,
            image_view="FULL",
            is_tracing=True,
            display_order=0,
            uploaded_by=self.user,
            created_by=self.user,
            modified_by=self.user,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _make_reference_work(self, code="ASCC1", title="A Catalog"):
        return ReferenceWork.objects.create(
            code=code,
            title=title,
            authorship="Author",
            publisher="Pub",
            publication_year=1900,
            created_by=self.user,
            modified_by=self.user,
        )

    def _make_pending_marking_contribution(self, **overrides):
        submitted_data = {
            "submission_kind": "marking",
            "state": "VA",
            "town": "Richmond",
            "type": "TOWNMARK",
            "color": "Black",
            "color_id": self.color.pk,
            "is_manuscript": True,
            "inscription_txt": "RICHMOND VA",
            "marking_image_metas": [
                {
                    "storage_filename": self.image.storage_filename,
                    "original_filename": self.image.original_filename,
                    "file_checksum": self.image.file_checksum,
                    "mime_type": self.image.mime_type,
                    "image_width": self.image.image_width,
                    "image_height": self.image.image_height,
                    "file_size_bytes": self.image.file_size_bytes,
                }
            ],
        }
        submitted_data.update(overrides)
        return Contribution.objects.create(
            contributor=self.user,
            collection=self.collection,
            submitted_data=submitted_data,
            status=Contribution.STATUS_PENDING,
            created_by=self.user,
            modified_by=self.user,
        )

    def _editor(self, username="editor"):
        return User.objects.create_superuser(
            username=username,
            email="{}@example.com".format(username),
            password="pw",
        )

    def _approve_url(self, contribution):
        return "/api/v2/contributions/{}/approve/".format(contribution.pk)

    def _suggestion_url(self, contribution):
        return "/api/v2/contributions/{}/catalog-code-suggestion/".format(
            contribution.pk
        )

    def test_fresh_marking_edit_preserves_existing_images(self):
        response = self.client.post(
            "/api/v2/contributions/",
            {
                "edit_marking_id": self.marking.pk,
                "state": "VA",
                "town": "Richmond",
                "type": "TOWNMARK",
                "color": "Black",
                "color_id": self.color.pk,
                "is_manuscript": True,
                "inscription_txt": "RICHMOND VA",
                "existing_image_tags": {
                    "/media/va/front.jpg": "photograph",
                },
                "image_order": ["/media/va/front.jpg"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        contribution = Contribution.objects.get(pk=response.data["id"])
        self.assertEqual(contribution.status, Contribution.STATUS_PENDING)
        self.assertEqual(contribution.submitted_data["edit_marking_id"], self.marking.pk)
        metas = contribution.submitted_data["marking_image_metas"]
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["storage_filename"], self.image.storage_filename)
        self.assertFalse(metas[0]["tracing"])

    def test_contributor_submitted_catalog_code_keys_are_stripped(self):
        response = self.client.post(
            "/api/v2/contributions/",
            {
                "state": "VA",
                "town": "Richmond",
                "type": "TOWNMARK",
                "color": "Black",
                "color_id": self.color.pk,
                "is_manuscript": True,
                "inscription_txt": "RICHMOND VA",
                "code": "BAD-VA-M0001",
                "catalog_code": "BAD-VA-M0002",
                "catalogCode": "BAD-VA-M0003",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        contribution = Contribution.objects.get(pk=response.data["id"])
        self.assertNotIn("code", contribution.submitted_data)
        self.assertNotIn("catalog_code", contribution.submitted_data)
        self.assertNotIn("catalogCode", contribution.submitted_data)

    def test_editor_submitted_catalog_code_is_preserved(self):
        editor = self._editor("submit-code-editor")
        self.client.force_authenticate(editor)

        response = self.client.post(
            "/api/v2/contributions/",
            {
                "state": "VA",
                "town": "Richmond",
                "type": "TOWNMARK",
                "color": "Black",
                "color_id": self.color.pk,
                "is_manuscript": True,
                "inscription_txt": "RICHMOND VA",
                "catalog_code": "APMC-VA-M0001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        contribution = Contribution.objects.get(pk=response.data["id"])
        self.assertEqual(contribution.submitted_data["catalog_code"], "APMC-VA-M0001")

    def test_contributor_submitted_marking_date_keys_are_stripped(self):
        # Setting a marking's date is editor-only (issue #27): a contributor's
        # ERD/LRD must not survive into submitted_data.
        response = self.client.post(
            "/api/v2/contributions/",
            {
                "state": "VA",
                "town": "Richmond",
                "type": "TOWNMARK",
                "color": "Black",
                "color_id": self.color.pk,
                "is_manuscript": True,
                "inscription_txt": "RICHMOND VA",
                "marking_erd": "1845-01-01",
                "marking_erd_granularity": "DAY",
                "marking_lrd": "1850-12-31",
                "marking_lrd_granularity": "DAY",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        contribution = Contribution.objects.get(pk=response.data["id"])
        self.assertNotIn("marking_erd", contribution.submitted_data)
        self.assertNotIn("marking_erd_granularity", contribution.submitted_data)
        self.assertNotIn("marking_lrd", contribution.submitted_data)
        self.assertNotIn("marking_lrd_granularity", contribution.submitted_data)

    def test_editor_submitted_marking_dates_are_preserved(self):
        editor = self._editor("submit-date-editor")
        self.client.force_authenticate(editor)

        response = self.client.post(
            "/api/v2/contributions/",
            {
                "state": "VA",
                "town": "Richmond",
                "type": "TOWNMARK",
                "color": "Black",
                "color_id": self.color.pk,
                "is_manuscript": True,
                "inscription_txt": "RICHMOND VA",
                "marking_erd": "1845-01-01",
                "marking_erd_granularity": "DAY",
                "marking_lrd": "1850-12-31",
                "marking_lrd_granularity": "DAY",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        contribution = Contribution.objects.get(pk=response.data["id"])
        self.assertEqual(contribution.submitted_data["marking_erd"], "1845-01-01")
        self.assertEqual(contribution.submitted_data["marking_lrd"], "1850-12-31")

    def test_direct_marking_suggestion_uses_apmc_without_reference(self):
        editor = User.objects.create_superuser(
            username="direct-editor",
            email="direct-editor@example.com",
            password="pw",
        )
        self.client.force_authenticate(editor)

        response = self.client.post(
            "/api/v2/catalog-code-suggestions/",
            {"subject_type": "MARKING", "state": "VA"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["catalog_code"], "APMC-VA-M0001")

    def test_marking_suggestion_uses_first_submitted_reference_work(self):
        first = self._make_reference_work(code="ASCC1", title="First Catalog")
        second = self._make_reference_work(code="VPHC1", title="Second Catalog")
        contribution = self._make_pending_marking_contribution(
            reference_work_ids=[first.pk, second.pk],
        )
        self.client.force_authenticate(self._editor("suggest-editor"))

        response = self.client.post(self._suggestion_url(contribution), {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["catalog_code"], "ASCC1-VA-M0001")
        contribution.refresh_from_db()
        self.assertEqual(contribution.submitted_data["catalog_code"], "ASCC1-VA-M0001")

    def test_marking_suggestion_falls_back_when_reference_code_blank(self):
        reference = self._make_reference_work(code="", title="Blank Code Catalog")
        self.client.force_authenticate(self._editor("blank-ref-editor"))

        response = self.client.post(
            "/api/v2/catalog-code-suggestions/",
            {
                "subject_type": "MARKING",
                "state": "VA",
                "reference_work_id": reference.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["catalog_code"], "APMC-VA-M0001")

    def test_approve_marking_applies_editor_override_when_unique(self):
        contribution = self._make_pending_marking_contribution()
        self.client.force_authenticate(self._editor("override-editor"))

        response = self.client.post(
            self._approve_url(contribution),
            {"catalog_code": "ASCC1-VA-M0042"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        marking = Marking.objects.get(pk=response.data["markingId"])
        self.assertEqual(marking.code, "ASCC1-VA-M0042")

    def test_approve_marking_blocks_duplicate_override(self):
        Marking.objects.create(
            code="ASCC1-VA-M0042",
            type="TOWNMARK",
            inscription_txt="NORFOLK VA",
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.user,
            modified_by=self.user,
        )
        contribution = self._make_pending_marking_contribution()
        self.client.force_authenticate(self._editor("duplicate-editor"))

        response = self.client.post(
            self._approve_url(contribution),
            {"catalog_code": "ASCC1-VA-M0042"},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("already exists", response.data["detail"])
        contribution.refresh_from_db()
        self.assertEqual(contribution.status, Contribution.STATUS_PENDING)

    def test_approve_marking_blank_regenerates_catalog_code(self):
        reference = self._make_reference_work(code="ASCC1")
        contribution = self._make_pending_marking_contribution(
            reference_work_ids=[reference.pk],
        )
        self.client.force_authenticate(self._editor("blank-approve-editor"))

        response = self.client.post(
            self._approve_url(contribution),
            {"catalog_code": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        marking = Marking.objects.get(pk=response.data["markingId"])
        self.assertEqual(marking.code, "ASCC1-VA-M0001")

    def test_approve_marking_edit_updates_existing_catalog_code(self):
        self.marking.code = "ASCC1-VA-M0001"
        self.marking.save(update_fields=["code"])
        contribution = self._make_pending_marking_contribution(
            edit_marking_id=self.marking.pk,
        )
        self.client.force_authenticate(self._editor("edit-code-editor"))

        response = self.client.post(
            self._approve_url(contribution),
            {"catalog_code": "ASCC1-VA-M0002"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.marking.refresh_from_db()
        self.assertEqual(self.marking.code, "ASCC1-VA-M0002")

    def test_non_editor_marking_response_redacts_catalog_code(self):
        self.marking.code = "ASCC1-VA-M0001"
        self.marking.save(update_fields=["code"])
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/v2/markings/{}/".format(self.marking.pk),
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["code"])

    def test_approving_marking_edit_does_not_duplicate_marking_link(self):
        editor = self._editor()
        original = Contribution.objects.create(
            contributor=self.user,
            collection=self.collection,
            submitted_data={"state": "VA", "town": "Richmond"},
            status=Contribution.STATUS_APPROVED,
            marking=self.marking,
            created_by=self.user,
            modified_by=self.user,
        )
        edit = Contribution.objects.create(
            contributor=self.user,
            collection=self.collection,
            created_by=self.user,
            modified_by=self.user,
            submitted_data={
                "edit_marking_id": self.marking.pk,
                "submission_kind": "marking",
                "state": "VA",
                "town": "Richmond",
                "type": "TOWNMARK",
                "color": "Black",
                "color_id": self.color.pk,
                "is_manuscript": True,
                "inscription_txt": "RICHMOND VA",
                "marking_image_metas": [
                    {
                        "storage_filename": self.image.storage_filename,
                        "original_filename": self.image.original_filename,
                        "file_checksum": self.image.file_checksum,
                        "mime_type": self.image.mime_type,
                        "image_width": self.image.image_width,
                        "image_height": self.image.image_height,
                        "file_size_bytes": self.image.file_size_bytes,
                    }
                ],
            },
            status=Contribution.STATUS_PENDING,
        )
        self.client.force_authenticate(editor)

        response = self.client.post(
            f"/api/v2/contributions/{edit.pk}/approve/",
            {"review_notes": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        original.refresh_from_db()
        edit.refresh_from_db()
        self.assertEqual(original.marking_id, self.marking.pk)
        self.assertIsNone(edit.marking_id)
        self.assertEqual(edit.status, Contribution.STATUS_APPROVED)
        self.assertEqual(response.data["markingId"], self.marking.pk)
