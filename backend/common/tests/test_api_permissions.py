from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import Collection, CollectionAssignment, Color, Region


User = get_user_model()


class CatalogWritePermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.contributor = User.objects.create_user(username="contributor", password="pw")
        self.editor = User.objects.create_user(username="editor", password="pw")
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pw",
        )
        self.editors_group = Group.objects.create(name="Editors")
        review_perm = Permission.objects.get(codename="review_contribution")
        self.editors_group.permissions.add(review_perm)
        self.editor.groups.add(self.editors_group)

    def test_anonymous_can_read_catalog_lookup(self):
        Color.objects.create(name="Black", created_by=self.admin, modified_by=self.admin)

        response = self.client.get("/api/v2/colors/")

        self.assertEqual(response.status_code, 200)

    def test_contributor_cannot_direct_write_catalog_lookup(self):
        self.client.force_authenticate(self.contributor)

        response = self.client.post("/api/v2/colors/", {"name": "Blue"}, format="json")

        self.assertEqual(response.status_code, 403)

    def test_editor_can_direct_write_catalog_lookup(self):
        self.client.force_authenticate(self.editor)

        response = self.client.post("/api/v2/colors/", {"name": "Blue"}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(Color.objects.filter(name="Blue").exists())

    def test_admin_can_direct_write_catalog_lookup(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post("/api/v2/colors/", {"name": "Green"}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(Color.objects.filter(name="Green").exists())


class AuthPayloadRoleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pw",
        )
        self.editor = User.objects.create_user(
            username="editor",
            email="editor@example.com",
            password="pw",
        )
        self.editors_group = Group.objects.create(name="Editors")
        review_perm = Permission.objects.get(codename="review_contribution")
        self.editors_group.permissions.add(review_perm)
        self.editor.groups.add(self.editors_group)
        self.region = Region.objects.create(
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.collection = Collection.objects.create(
            name="Virginia Collection",
            region=self.region,
            created_by=self.admin,
            modified_by=self.admin,
        )
        CollectionAssignment.objects.create(
            user=self.editor,
            collection=self.collection,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def test_editor_payload_uses_final_role_and_assigned_collections(self):
        self.client.force_authenticate(self.editor)

        response = self.client.get("/api/v2/me/")

        self.assertEqual(response.status_code, 200)
        user_payload = response.data["user"]
        self.assertEqual(user_payload["role"], "editor")
        self.assertEqual(user_payload["assigned_collections"][0]["name"], "Virginia Collection")
        self.assertEqual(user_payload["assigned_collections"][0]["region"]["abbrev"], "VA")

    def test_superuser_payload_uses_administrator_role(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v2/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["role"], "administrator")
