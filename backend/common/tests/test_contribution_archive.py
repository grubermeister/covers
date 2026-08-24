from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Collection,
    CollectionAssignment,
    Contribution,
    ContributionRecycleBin,
    Region,
)


User = get_user_model()


class ContributionArchiveTests(TestCase):
    """
    Issue #89: editors clear reviewed entries off the review dashboard without
    deleting them, and the contributor keeps seeing their own submission.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pw"
        )
        self.editor = User.objects.create_user(username="editor", password="pw")
        editors = Group.objects.create(name="Editors")
        editors.permissions.add(Permission.objects.get(codename="review_contribution"))
        self.editor.groups.add(editors)
        self.other_editor = User.objects.create_user(username="other-editor", password="pw")
        self.other_editor.groups.add(editors)
        self.contributor = User.objects.create_user(username="contributor", password="pw")

        virginia = Region.objects.create(
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.collection = Collection.objects.create(
            name="Virginia Collection",
            region=virginia,
            created_by=self.admin,
            modified_by=self.admin,
        )
        CollectionAssignment.objects.create(
            user=self.editor,
            collection=self.collection,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _contribution(self, status=Contribution.STATUS_REJECTED):
        return Contribution.objects.create(
            contributor=self.contributor,
            collection=self.collection,
            submitted_data={"state": "Virginia", "town": "Richmond"},
            status=status,
            created_by=self.contributor,
            modified_by=self.contributor,
        )

    def _archive(self, contribution, user, reason="Duplicate"):
        self.client.force_authenticate(user)
        return self.client.post(
            f"/api/v2/contributions/{contribution.pk}/archive/",
            {"reason": reason},
            format="json",
        )

    def _editor_queue_ids(self, mode="editor"):
        self.client.force_authenticate(self.editor)
        response = self.client.get(f"/api/v2/contributions/?mode={mode}")
        self.assertEqual(response.status_code, 200, response.data)
        results = response.data.get("results", response.data)
        return [row["id"] for row in results]

    def test_archive_then_restore_round_trip(self):
        contribution = self._contribution()

        response = self._archive(contribution, self.editor)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            ContributionRecycleBin.objects.filter(contribution=contribution).exists()
        )
        # The contribution row itself is untouched -- status and history survive.
        contribution.refresh_from_db()
        self.assertEqual(contribution.status, Contribution.STATUS_REJECTED)
        self.assertNotIn(contribution.pk, self._editor_queue_ids())
        self.assertIn(contribution.pk, self._editor_queue_ids(mode="archived"))

        self.client.force_authenticate(self.editor)
        restore = self.client.post(
            f"/api/v2/contributions/{contribution.pk}/restore/", {}, format="json"
        )

        self.assertEqual(restore.status_code, 200, restore.data)
        self.assertFalse(
            ContributionRecycleBin.objects.filter(contribution=contribution).exists()
        )
        self.assertIn(contribution.pk, self._editor_queue_ids())

    def test_archived_entry_stays_visible_to_its_contributor(self):
        contribution = self._contribution()
        self._archive(contribution, self.editor)

        self.client.force_authenticate(self.contributor)
        response = self.client.get("/api/v2/contributions/")

        self.assertEqual(response.status_code, 200, response.data)
        results = response.data.get("results", response.data)
        self.assertIn(contribution.pk, [row["id"] for row in results])

    def test_pending_contribution_cannot_be_archived(self):
        contribution = self._contribution(status=Contribution.STATUS_PENDING)

        response = self._archive(contribution, self.editor)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(
            ContributionRecycleBin.objects.filter(contribution=contribution).exists()
        )

    def test_editor_without_the_collection_cannot_archive(self):
        contribution = self._contribution()

        response = self._archive(contribution, self.other_editor)

        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(
            ContributionRecycleBin.objects.filter(contribution=contribution).exists()
        )

    def test_archive_records_who_and_why(self):
        contribution = self._contribution(status=Contribution.STATUS_NEEDS_REVISION)
        self._archive(contribution, self.editor, reason="Blatantly wrong")

        entry = ContributionRecycleBin.objects.get(contribution=contribution)
        self.assertEqual(entry.archived_by, self.editor)
        self.assertEqual(entry.reason, "Blatantly wrong")

        rows = self.client.get("/api/v2/contributions/?mode=archived").data
        row = (rows.get("results", rows))[0]
        self.assertTrue(row["is_archived"])
        self.assertEqual(row["archived_by_username"], "editor")
        self.assertEqual(row["archive_reason"], "Blatantly wrong")
