from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Collection,
    CollectionAssignment,
    Cover,
    CoverMarking,
    CoverRecycleBin,
    Marking,
    MarkingRecycleBin,
    PostOffice,
    PostOfficeRegion,
    Region,
)


User = get_user_model()


class EntryRemovePermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pw",
        )
        self.editor = User.objects.create_user(
            username="editor",
            password="pw",
        )
        editors = Group.objects.create(name="Editors")
        editors.permissions.add(
            Permission.objects.get(codename="review_contribution")
        )
        self.editor.groups.add(editors)

        self.virginia = self._region("Virginia", "VA")
        self.maryland = self._region("Maryland", "MD")
        virginia_collection = Collection.objects.create(
            name="Virginia Collection",
            region=self.virginia,
            created_by=self.admin,
            modified_by=self.admin,
        )
        Collection.objects.create(
            name="Maryland Collection",
            region=self.maryland,
            created_by=self.admin,
            modified_by=self.admin,
        )
        CollectionAssignment.objects.create(
            user=self.editor,
            collection=virginia_collection,
            created_by=self.admin,
            modified_by=self.admin,
        )

        self.virginia_marking = self._marking("Richmond", self.virginia)
        self.maryland_marking = self._marking("Baltimore", self.maryland)

    def _region(self, name, abbrev):
        return Region.objects.create(
            name=name,
            abbrev=abbrev,
            region_tier="STATE",
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _marking(self, town, region):
        post_office = PostOffice.objects.create(
            name=town,
            created_by=self.admin,
            modified_by=self.admin,
        )
        PostOfficeRegion.objects.create(
            post_office=post_office,
            region=region,
            created_by=self.admin,
            modified_by=self.admin,
        )
        return Marking.objects.create(
            type="TOWNMARK",
            inscription_txt=town.upper(),
            is_manuscript=True,
            post_office=post_office,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _cover(self, *markings):
        cover = Cover.objects.create(
            type="FC",
            created_by=self.admin,
            modified_by=self.admin,
        )
        for marking in markings:
            CoverMarking.objects.create(
                cover=cover,
                marking=marking,
                created_by=self.admin,
                modified_by=self.admin,
            )
        return cover

    def _remove(self, cover, user):
        self.client.force_authenticate(user)
        return self.client.post(
            f"/api/v2/covers/{cover.pk}/remove/",
            {"reason": "Test"},
            format="json",
        )

    def test_editor_cannot_remove_marking_in_unassigned_state(self):
        self.client.force_authenticate(self.editor)

        response = self.client.post(
            f"/api/v2/markings/{self.maryland_marking.pk}/remove/",
            {"reason": "Test"},
            format="json",
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(
            MarkingRecycleBin.objects.filter(
                marking=self.maryland_marking
            ).exists()
        )

    def test_editor_can_remove_cover_entirely_in_assigned_state(self):
        cover = self._cover(self.virginia_marking)

        response = self._remove(cover, self.editor)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(CoverRecycleBin.objects.filter(cover=cover).exists())

    def test_editor_cannot_remove_cover_with_unassigned_state(self):
        cover = self._cover(self.virginia_marking, self.maryland_marking)
        self.client.force_authenticate(self.editor)

        detail_response = self.client.get(f"/api/v2/covers/{cover.pk}/")
        self.assertEqual(detail_response.status_code, 200, detail_response.data)
        self.assertFalse(detail_response.data["can_remove"])

        response = self._remove(cover, self.editor)

        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(CoverRecycleBin.objects.filter(cover=cover).exists())

    def test_editor_cannot_restore_cover_with_unassigned_state(self):
        cover = self._cover(self.virginia_marking, self.maryland_marking)
        CoverRecycleBin.objects.create(
            cover=cover,
            removed_by=self.admin,
            reason="Test",
        )
        self.client.force_authenticate(self.editor)

        response = self.client.post(f"/api/v2/covers/{cover.pk}/restore/")

        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(CoverRecycleBin.objects.filter(cover=cover).exists())

    def test_admin_can_remove_cover_with_multiple_states(self):
        cover = self._cover(self.virginia_marking, self.maryland_marking)

        response = self._remove(cover, self.admin)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(CoverRecycleBin.objects.filter(cover=cover).exists())
