"""
Issue #22 -- state editor "reviewed/confirmed" flag on Marking.

Covers the load-bearing slice: the field round-trips through the detail
serializer, the search filter splits reviewed vs. unreviewed, and only
editors/admins can flip the flag (contributors and anonymous are rejected).
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Collection,
    CollectionAssignment,
    Color,
    Marking,
    PostOffice,
    PostOfficeRegion,
    Region,
)


User = get_user_model()


def _make_marking(user, color, post_office, text, *, is_reviewed=False):
    return Marking.objects.create(
        type="TOWNMARK",
        catalog_txt=text,
        inscription_txt=text,
        desc="",
        is_manuscript=True,
        color=color,
        post_office=post_office,
        is_reviewed=is_reviewed,
        created_by=user,
        modified_by=user,
    )


def _result_ids(response):
    return {row["id"] for row in response.data["results"]}


class MarkingReviewedTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.contributor = User.objects.create_user("contrib", password="pw")
        self.editor = User.objects.create_user("editor", password="pw")
        self.admin = User.objects.create_superuser(
            "admin", email="admin@example.com", password="pw"
        )
        editors_group = Group.objects.create(name="Editors")
        editors_group.permissions.add(
            Permission.objects.get(codename="review_contribution")
        )
        self.editor.groups.add(editors_group)

        # Marking writes are region-scoped (IsResponsibleForRegion): the editor
        # must be assigned, via a Collection, to the region of the marking's
        # post office. Wire that chain up so the editor is the state editor.
        self.region = Region.objects.create(
            name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=self.admin, modified_by=self.admin,
        )
        self.collection = Collection.objects.create(
            name="Virginia Collection", region=self.region,
            created_by=self.admin, modified_by=self.admin,
        )
        CollectionAssignment.objects.create(
            user=self.editor, collection=self.collection,
            created_by=self.admin, modified_by=self.admin,
        )

        self.color = Color.objects.create(
            name="Black", created_by=self.admin, modified_by=self.admin
        )
        self.post_office = PostOffice.objects.create(
            name="Richmond", created_by=self.admin, modified_by=self.admin
        )
        PostOfficeRegion.objects.create(
            post_office=self.post_office, region=self.region,
            created_by=self.admin, modified_by=self.admin,
        )
        self.reviewed = _make_marking(
            self.admin, self.color, self.post_office, "vetted", is_reviewed=True
        )
        self.unreviewed = _make_marking(
            self.admin, self.color, self.post_office, "pending", is_reviewed=False
        )

    # --- serializer ----------------------------------------------------------
    def test_detail_exposes_is_reviewed(self):
        response = self.client.get(f"/api/v2/markings/{self.reviewed.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_reviewed"])

        response = self.client.get(f"/api/v2/markings/{self.unreviewed.id}/")
        self.assertFalse(response.data["is_reviewed"])

    # --- search filter -------------------------------------------------------
    def test_filter_reviewed_true_and_false(self):
        reviewed = self.client.get("/api/v2/markings/", {"reviewed": "true", "page_size": "50"})
        self.assertEqual(_result_ids(reviewed), {self.reviewed.id})

        unreviewed = self.client.get("/api/v2/markings/", {"reviewed": "false", "page_size": "50"})
        self.assertEqual(_result_ids(unreviewed), {self.unreviewed.id})

        # Blank/absent -> no filtering (both rows returned).
        both = self.client.get("/api/v2/markings/", {"reviewed": "", "page_size": "50"})
        self.assertEqual(_result_ids(both), {self.reviewed.id, self.unreviewed.id})

    # --- permission gate -----------------------------------------------------
    def test_editor_can_set_reviewed(self):
        self.client.force_authenticate(self.editor)
        response = self.client.patch(
            f"/api/v2/markings/{self.unreviewed.id}/",
            {"is_reviewed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.unreviewed.refresh_from_db()
        self.assertTrue(self.unreviewed.is_reviewed)

    def test_editor_of_other_state_cannot_set_reviewed(self):
        # State editors are scoped to their assigned region; an editor for a
        # different state may not flip this marking's flag.
        other = User.objects.create_user("md-editor", password="pw")
        other.groups.add(Group.objects.get(name="Editors"))
        md_region = Region.objects.create(
            name="Maryland", abbrev="MD", region_tier="STATE",
            created_by=self.admin, modified_by=self.admin,
        )
        md_collection = Collection.objects.create(
            name="Maryland Collection", region=md_region,
            created_by=self.admin, modified_by=self.admin,
        )
        CollectionAssignment.objects.create(
            user=other, collection=md_collection,
            created_by=self.admin, modified_by=self.admin,
        )
        self.client.force_authenticate(other)
        response = self.client.patch(
            f"/api/v2/markings/{self.unreviewed.id}/",
            {"is_reviewed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.unreviewed.refresh_from_db()
        self.assertFalse(self.unreviewed.is_reviewed)

    def test_contributor_cannot_set_reviewed(self):
        self.client.force_authenticate(self.contributor)
        response = self.client.patch(
            f"/api/v2/markings/{self.unreviewed.id}/",
            {"is_reviewed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.unreviewed.refresh_from_db()
        self.assertFalse(self.unreviewed.is_reviewed)

    def test_anonymous_cannot_set_reviewed(self):
        response = self.client.patch(
            f"/api/v2/markings/{self.unreviewed.id}/",
            {"is_reviewed": True},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.unreviewed.refresh_from_db()
        self.assertFalse(self.unreviewed.is_reviewed)
