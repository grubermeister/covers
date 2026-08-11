"""
Tests for contribution consolidation helpers and operator cleanup.

Runbook:
  cwd: repo root
  command: .venv/bin/python backend/manage.py test common.tests.test_contribution_consolidation
  expected exit code: 0
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from common.models import (
    Collection,
    Color,
    Contribution,
    Marking,
    PostOffice,
    Region,
    SubmissionTransaction,
)


User = get_user_model()


class ConsolidateSupersededContributionsCommandTests(TestCase):
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
        self.marking = Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="RICHMOND VA",
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.user,
            modified_by=self.user,
        )

    def _create_pair(self):
        old = Contribution.objects.create(
            contributor=self.user,
            collection=self.collection,
            submitted_data={"submission_kind": "marking", "state": "VA"},
            status=Contribution.STATUS_APPROVED,
            marking=self.marking,
            created_by=self.user,
            modified_by=self.user,
        )
        latest = Contribution.objects.create(
            contributor=self.user,
            collection=self.collection,
            submitted_data={
                "submission_kind": "marking",
                "edit_marking_id": self.marking.pk,
                "state": "VA",
            },
            status=Contribution.STATUS_PENDING,
            created_by=self.user,
            modified_by=self.user,
        )
        return old, latest

    def test_dry_run_reports_without_deleting_or_writing_tombstones(self):
        old, latest = self._create_pair()
        out = StringIO()

        call_command(
            "consolidate_superseded_contributions",
            dry_run=True,
            no_input=True,
            stdout=out,
        )

        self.assertIn("[DRY RUN]", out.getvalue())
        self.assertTrue(Contribution.objects.filter(pk=old.pk).exists())
        self.assertTrue(Contribution.objects.filter(pk=latest.pk).exists())
        self.assertFalse(
            SubmissionTransaction.objects.filter(
                action=SubmissionTransaction.ACTION_CONTRIBUTION_SUPERSEDED
            ).exists()
        )

    def test_no_input_deletes_older_rows_and_writes_tombstones(self):
        old, latest = self._create_pair()
        out = StringIO()

        call_command(
            "consolidate_superseded_contributions",
            no_input=True,
            stdout=out,
        )

        self.assertIn("deleted 1 superseded Contribution row(s)", out.getvalue())
        self.assertFalse(Contribution.objects.filter(pk=old.pk).exists())
        self.assertTrue(Contribution.objects.filter(pk=latest.pk).exists())
        tombstone = SubmissionTransaction.objects.get(
            action=SubmissionTransaction.ACTION_CONTRIBUTION_SUPERSEDED,
            marking=self.marking,
        )
        self.assertEqual(tombstone.before_payload["contribution_id"], old.pk)
        self.assertEqual(
            tombstone.extra_payload["superseded_by_contribution_id"],
            latest.pk,
        )
