from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import Cover, CoverMarking, Image, Marking, PostOffice


User = get_user_model()


def make_image(subject_type, subject_id, user, view, order=0, name="img.jpg"):
    return Image.objects.create(
        subject_type=subject_type,
        subject_id=subject_id,
        original_filename=name,
        storage_filename=f"va/{subject_type.lower()}-{subject_id}-{order}-{name}",
        file_checksum="abc123",
        mime_type="image/jpeg",
        image_width=800,
        image_height=600,
        file_size_bytes=12345,
        image_view=view,
        is_tracing=False,
        display_order=order,
        uploaded_by=user,
        created_by=user,
        modified_by=user,
    )


class ImageMoveTests(TestCase):
    """PATCHing subject_type/subject_id moves an image between a marking and
    a cover (issue #48 -- v1 attached every upload to the marking)."""

    def setUp(self):
        self.client = APIClient()
        self.contributor = User.objects.create_user(username="contributor", password="pw")
        self.editor = User.objects.create_user(username="editor", password="pw")
        editors = Group.objects.create(name="Editors")
        editors.permissions.add(Permission.objects.get(codename="review_contribution"))
        self.editor.groups.add(editors)

        audit = {"created_by": self.editor, "modified_by": self.editor}
        self.post_office = PostOffice.objects.create(name="Charlestown", **audit)
        self.marking = Marking.objects.create(
            code="ASCC1-VA-M0001",
            type="TOWNMARK",
            inscription_txt="CHARLESTOWN VA",
            is_manuscript=False,
            post_office=self.post_office,
            **audit,
        )
        self.cover = Cover.objects.create(code="ASCC1-VA-C0001", type="FC", **audit)
        CoverMarking.objects.create(cover=self.cover, marking=self.marking, **audit)

        # The Charlestown case: the cover photo sits on the marking.
        self.misfiled = make_image(
            Image.SUBJECT_MARKING, self.marking.pk, self.editor, "FULL", order=0
        )
        self.tracing = make_image(
            Image.SUBJECT_MARKING, self.marking.pk, self.editor, "DETAIL",
            order=1, name="tracing.jpg",
        )

    def _move_payload(self, view="FRONT"):
        return {
            "subject_type": Image.SUBJECT_COVER,
            "subject_id": self.cover.pk,
            "image_view": view,
        }

    def test_editor_moves_image_marking_to_cover(self):
        existing_cover_default = make_image(
            Image.SUBJECT_COVER, self.cover.pk, self.editor, "FRONT",
            order=0, name="cover-front.jpg",
        )
        self.client.force_authenticate(self.editor)

        response = self.client.patch(
            f"/api/v2/images/{self.misfiled.pk}/", self._move_payload(), format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.misfiled.refresh_from_db()
        self.assertEqual(self.misfiled.subject_type, Image.SUBJECT_COVER)
        self.assertEqual(self.misfiled.subject_id, self.cover.pk)
        self.assertEqual(self.misfiled.image_view, "FRONT")
        # Moved image must not displace the target's existing default.
        self.assertEqual(self.misfiled.display_order, 1)
        existing_cover_default.refresh_from_db()
        self.assertEqual(existing_cover_default.display_order, 0)
        # The source subject promotes a new default (was display_order=1).
        self.tracing.refresh_from_db()
        self.assertEqual(self.tracing.display_order, 0)

    def test_move_cover_to_marking_direction(self):
        cover_img = make_image(
            Image.SUBJECT_COVER, self.cover.pk, self.editor, "FRONT",
            order=0, name="cover-front.jpg",
        )
        self.client.force_authenticate(self.editor)

        response = self.client.patch(
            f"/api/v2/images/{cover_img.pk}/",
            {
                "subject_type": Image.SUBJECT_MARKING,
                "subject_id": self.marking.pk,
                "image_view": "FULL",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        cover_img.refresh_from_db()
        self.assertEqual(cover_img.subject_type, Image.SUBJECT_MARKING)
        self.assertEqual(cover_img.display_order, 2)

    def test_move_to_nonexistent_target_is_400(self):
        self.client.force_authenticate(self.editor)

        response = self.client.patch(
            f"/api/v2/images/{self.misfiled.pk}/",
            {"subject_type": "COVER", "subject_id": 99999, "image_view": "FRONT"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("subject_id", response.data)

    def test_move_with_mismatched_view_is_400_not_500(self):
        self.client.force_authenticate(self.editor)

        # FULL is a marking view; moving to a cover without a cover view.
        response = self.client.patch(
            f"/api/v2/images/{self.misfiled.pk}/",
            {"subject_type": "COVER", "subject_id": self.cover.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("image_view", response.data)

    def test_contributor_cannot_move_images(self):
        self.client.force_authenticate(self.contributor)

        response = self.client.patch(
            f"/api/v2/images/{self.misfiled.pk}/", self._move_payload(), format="json"
        )

        self.assertEqual(response.status_code, 403)
