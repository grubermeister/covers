"""Crop a marking out of a whole-cover scan (issue #77).

Prerequisite for the #78 backfill: much of the catalog has a scan of a whole
cover sitting in a marking's image slot. The full scan belongs on a Cover, but
moving it there first would leave the marking with nothing -- 112 markings on
prod hold no other image. Cropping gives the marking a real closeup so the
original can then be moved with the existing PATCH (issue #48).
"""
import io
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from PIL import Image as PILImage
from rest_framework.test import APIClient

from common.models import (
    Collection,
    CollectionAssignment,
    Image,
    Marking,
    PostOffice,
    PostOfficeRegion,
    Region,
)


User = get_user_model()

MEDIA_ROOT = tempfile.mkdtemp(prefix="woco-crop-tests-")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ImageCropTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser("admin", password="pw")
        self.editor = User.objects.create_user("va-editor", password="pw")
        editors = Group.objects.create(name="Editors")
        editors.permissions.add(
            Permission.objects.get(codename="review_contribution")
        )
        self.editor.groups.add(editors)

        audit = {"created_by": self.admin, "modified_by": self.admin}
        self.region = Region.objects.create(
            name="Virginia", abbrev="VA", region_tier="STATE", **audit
        )
        collection = Collection.objects.create(
            name="Virginia Collection", region=self.region, **audit
        )
        CollectionAssignment.objects.create(
            user=self.editor, collection=collection, **audit
        )
        self.post_office = PostOffice.objects.create(name="Fetterman", **audit)
        PostOfficeRegion.objects.create(
            post_office=self.post_office, region=self.region, **audit
        )
        self.marking = Marking.objects.create(
            code="ASCC1-VA-M0001",
            type="TOWNMARK",
            inscription_txt="FETTERMAN VA",
            is_manuscript=False,
            post_office=self.post_office,
            **audit,
        )
        # A whole-cover scan filed as this marking's image -- the real shape of
        # the problem (prod image 2417 is 2631x1290).
        self.source = self._make_image("va/cover-scan.jpg", width=400, height=200)

    def _make_image(self, storage_filename, width, height):
        path = Path(MEDIA_ROOT) / storage_filename
        path.parent.mkdir(parents=True, exist_ok=True)
        PILImage.new("RGB", (width, height), color=(200, 180, 150)).save(path)
        content = path.read_bytes()
        return Image.objects.create(
            subject_type=Image.SUBJECT_MARKING,
            subject_id=self.marking.pk,
            original_filename=Path(storage_filename).name,
            storage_filename=storage_filename,
            file_checksum="seed",
            mime_type="image/jpeg",
            image_width=width,
            image_height=height,
            file_size_bytes=len(content),
            image_view="FULL",
            display_order=0,
            uploaded_by=self.admin,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _crop(self, **body):
        payload = {"x": 10, "y": 20, "width": 100, "height": 80}
        payload.update(body)
        return self.client.post(
            f"/api/v2/images/{self.source.pk}/crop/", payload, format="json"
        )

    # --- happy path ----------------------------------------------------------

    def test_editor_crops_a_marking_out_of_a_cover_scan(self):
        self.client.force_authenticate(self.editor)

        response = self._crop()

        self.assertEqual(response.status_code, 201, response.data)
        cropped = Image.objects.get(pk=response.data["image_id"])
        # Lands on the same marking, so the marking gains a usable image.
        self.assertEqual(cropped.subject_type, Image.SUBJECT_MARKING)
        self.assertEqual(cropped.subject_id, self.marking.pk)
        # Metadata is recomputed from the cropped bytes, not copied.
        self.assertEqual((cropped.image_width, cropped.image_height), (100, 80))
        self.assertNotEqual(cropped.file_checksum, self.source.file_checksum)
        self.assertEqual(cropped.cropped_from_id, self.source.pk)
        # Appended, never promoted over the existing default.
        self.assertEqual(cropped.display_order, 1)

        on_disk = Path(MEDIA_ROOT) / cropped.storage_filename
        self.assertTrue(on_disk.is_file())
        with PILImage.open(io.BytesIO(on_disk.read_bytes())) as img:
            self.assertEqual(img.size, (100, 80))

    def test_crop_is_non_destructive(self):
        self.client.force_authenticate(self.editor)
        before = (Path(MEDIA_ROOT) / self.source.storage_filename).read_bytes()

        self._crop()

        after = (Path(MEDIA_ROOT) / self.source.storage_filename).read_bytes()
        self.assertEqual(before, after)
        self.source.refresh_from_db()
        self.assertEqual(self.source.image_width, 400)

    def test_crop_is_stored_beside_its_source(self):
        """Media stays sorted by state rather than pooling in uploads/."""
        self.client.force_authenticate(self.editor)

        response = self._crop()

        self.assertTrue(response.data["storage_filename"].startswith("va/"))

    # --- validation ----------------------------------------------------------

    def test_crop_reaching_outside_the_image_is_rejected(self):
        self.client.force_authenticate(self.editor)

        response = self._crop(x=350, width=100)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Image.objects.filter(cropped_from=self.source).count(), 0)

    def test_zero_area_crop_is_rejected(self):
        self.client.force_authenticate(self.editor)

        self.assertEqual(self._crop(width=0).status_code, 400)
        self.assertEqual(self._crop(height=0).status_code, 400)

    def test_non_numeric_rectangle_is_rejected(self):
        self.client.force_authenticate(self.editor)

        self.assertEqual(self._crop(x="left").status_code, 400)

    def test_image_view_must_be_valid_for_the_subject(self):
        """FRONT is a cover view; the CheckConstraint would reject it anyway."""
        self.client.force_authenticate(self.editor)

        response = self._crop(image_view="FRONT")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Image.objects.filter(cropped_from=self.source).count(), 0)

    def test_missing_source_file_is_reported_not_crashed(self):
        self.client.force_authenticate(self.editor)
        (Path(MEDIA_ROOT) / self.source.storage_filename).unlink()

        response = self._crop()

        self.assertEqual(response.status_code, 404)

    # --- permissions ---------------------------------------------------------

    def test_editor_of_another_state_cannot_crop(self):
        other = User.objects.create_user("md-editor", password="pw")
        other.groups.add(Group.objects.get(name="Editors"))
        audit = {"created_by": self.admin, "modified_by": self.admin}
        md_region = Region.objects.create(
            name="Maryland", abbrev="MD", region_tier="STATE", **audit
        )
        md_collection = Collection.objects.create(
            name="Maryland Collection", region=md_region, **audit
        )
        CollectionAssignment.objects.create(
            user=other, collection=md_collection, **audit
        )
        self.client.force_authenticate(other)

        response = self._crop()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Image.objects.filter(cropped_from=self.source).count(), 0)

    def test_contributor_cannot_crop(self):
        contributor = User.objects.create_user("contributor", password="pw")
        self.client.force_authenticate(contributor)

        response = self._crop()

        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_crop(self):
        response = self._crop()

        self.assertIn(response.status_code, (401, 403))
