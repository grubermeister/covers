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

    def test_fresh_marking_edit_preserves_existing_images(self):
        response = self.client.post(
            "/api/v2/contributions/",
            {
                "edit_postmark_id": self.marking.pk,
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
        self.assertEqual(contribution.submitted_data["edit_postmark_id"], self.marking.pk)
        metas = contribution.submitted_data["marking_image_metas"]
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["storage_filename"], self.image.storage_filename)
        self.assertFalse(metas[0]["tracing"])

    def test_approving_marking_edit_does_not_duplicate_marking_link(self):
        editor = User.objects.create_superuser(
            username="editor",
            email="editor@example.com",
            password="pw",
        )
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
                "edit_postmark_id": self.marking.pk,
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
