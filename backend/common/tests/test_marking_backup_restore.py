import json
import tempfile
from pathlib import Path
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from common.audit import restore_marking_from_snapshot
from common.models import (
    Citation,
    Collection,
    CollectionAssignment,
    Color,
    Contribution,
    Cover,
    CoverMarking,
    DateSeen,
    Image,
    Marking,
    MarkingVersion,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
    SubmissionTransaction,
)


User = get_user_model()


class MarkingBackupRestoreCommandTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

        self.contributor = User.objects.create_user(
            username="contributor",
            email="contributor@example.com",
            password="pw",
        )
        self.editor = User.objects.create_superuser(
            username="editor",
            email="editor@example.com",
            password="pw",
        )
        self.virginia_editor = User.objects.create_user(
            username="virginia-editor",
            email="virginia-editor@example.com",
            password="pw",
        )
        self.color = Color.objects.create(
            name="Black",
            created_by=self.editor,
            modified_by=self.editor,
        )
        self.region = Region.objects.create(
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.editor,
            modified_by=self.editor,
        )
        self.collection = Collection.objects.create(
            name="Virginia",
            region=self.region,
            created_by=self.editor,
            modified_by=self.editor,
        )
        CollectionAssignment.objects.create(
            user=self.virginia_editor,
            collection=self.collection,
            created_by=self.editor,
            modified_by=self.editor,
        )
        self.post_office = PostOffice.objects.create(
            name="Richmond",
            created_by=self.editor,
            modified_by=self.editor,
        )
        PostOfficeRegion.objects.create(
            post_office=self.post_office,
            region=self.region,
            created_by=self.editor,
            modified_by=self.editor,
        )
        self.marking = Marking.objects.create(
            code="ASCC1-VA-M0001",
            type="TOWNMARK",
            catalog_txt="RICHMOND/VA.",
            inscription_txt="RICHMOND VA",
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.editor,
            modified_by=self.editor,
        )
        self.cover = Cover.objects.create(
            code="ASCC1-VA-C0001",
            color=self.color,
            type="FC",
            created_by=self.editor,
            modified_by=self.editor,
        )
        CoverMarking.objects.create(
            cover=self.cover,
            marking=self.marking,
            review_status=CoverMarking.REVIEW_APPROVED,
            reviewer=self.editor,
            created_by=self.editor,
            modified_by=self.editor,
        )
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_MARKING,
            subject_id=self.marking.pk,
            date="1845-01-01",
            granularity="YEAR",
            created_by=self.editor,
            modified_by=self.editor,
        )
        self.reference_work = ReferenceWork.objects.create(
            code="ASCC1",
            title="A Catalog",
            authorship="Author",
            publisher="Publisher",
            publication_year=1900,
            created_by=self.editor,
            modified_by=self.editor,
        )
        Citation.objects.create(
            reference_work=self.reference_work,
            subject_type="MARKING",
            subject_id=self.marking.pk,
            citation_detail="p. 1",
            created_by=self.editor,
            modified_by=self.editor,
        )
        Image.objects.create(
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
            is_tracing=False,
            display_order=0,
            uploaded_by=self.contributor,
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        self.contribution = Contribution.objects.create(
            contributor=self.contributor,
            marking=self.marking,
            collection=self.collection,
            submitted_data={
                "submission_kind": "marking",
                "state": "VA",
                "town": "Richmond",
                "catalog_code": self.marking.code,
            },
            status=Contribution.STATUS_APPROVED,
            reviewer=self.editor,
            review_notes="Approved.",
            created_by=self.contributor,
            modified_by=self.editor,
        )
        self.transaction = SubmissionTransaction.objects.create(
            transaction_uuid=uuid4(),
            actor=self.editor,
            action=SubmissionTransaction.ACTION_APPROVE,
            contribution=self.contribution,
            marking=self.marking,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload={},
            after_payload={"code": self.marking.code},
            diff_payload=[
                {
                    "field": "code",
                    "before": None,
                    "after": self.marking.code,
                }
            ],
            extra_payload={},
        )
        MarkingVersion.objects.create(
            marking=self.marking,
            version_no=1,
            snapshot={
                "code": self.marking.code,
                "town": "Richmond",
                "state": "Virginia",
                "type": self.marking.type,
                "inscription_txt": self.marking.inscription_txt,
            },
            transaction=self.transaction,
            created_by=self.editor,
        )

    def _backup_path(self):
        return self.tmp_path / "marking.json"

    def _backup(self):
        out_path = self._backup_path()
        call_command(
            "backup_marking",
            self.marking.code,
            str(out_path),
            verbosity=0,
        )
        return out_path

    def _payload(self):
        return json.loads(self._backup().read_text(encoding="utf-8"))

    def test_backup_marking_exports_user_data_and_display_metadata(self):
        payload = self._payload()

        self.assertEqual(payload["schema"], "worldcovers.marking_backup.v1")
        self.assertEqual(payload["root_marking_code"], self.marking.code)
        self.assertEqual(payload["media_policy"], "metadata_only")

        datasets = payload["datasets"]
        self.assertEqual(len(datasets["markings"]["rows"]), 1)
        self.assertEqual(len(datasets["contributions"]["rows"]), 1)
        self.assertEqual(len(datasets["submission_transactions"]["rows"]), 1)
        self.assertEqual(len(datasets["marking_versions"]["rows"]), 1)
        self.assertEqual(len(datasets["images"]["rows"]), 1)
        self.assertEqual(len(datasets["citations"]["rows"]), 1)
        self.assertEqual(len(datasets["dates_seen"]["rows"]), 1)

        contribution = datasets["contributions"]["rows"][0]
        self.assertEqual(contribution["contributor"], self.contributor.username)
        self.assertEqual(contribution["status"], Contribution.STATUS_APPROVED)
        self.assertEqual(contribution["marking_code"], self.marking.code)

    def test_restore_marking_restores_dashboard_and_history_rows(self):
        backup_path = self._backup()
        call_command("wipe_user_data", no_input=True, verbosity=0)

        self.assertFalse(Contribution.objects.exists())
        self.assertFalse(SubmissionTransaction.objects.exists())
        self.assertFalse(MarkingVersion.objects.exists())

        call_command("restore_marking", str(backup_path), verbosity=0)

        restored = Contribution.objects.get(marking=self.marking)
        self.assertEqual(restored.contributor, self.contributor)
        self.assertEqual(restored.status, Contribution.STATUS_APPROVED)
        self.assertEqual(restored.review_notes, "Approved.")

        client = APIClient()
        client.force_authenticate(self.contributor)
        response = client.get("/api/v2/contributions/", format="json")
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data.get("results", response.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], restored.pk)
        self.assertEqual(rows[0]["status"], Contribution.STATUS_APPROVED)

        client.force_authenticate(self.editor)
        history = client.get(
            f"/api/v2/markings/{self.marking.pk}/changelog/",
            format="json",
        )
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(len(history.data["events"]), 1)
        self.assertEqual(len(history.data["versions"]), 1)
        self.assertEqual(len(history.data["approved_versions"]), 1)

    def test_snapshot_restore_preserves_null_marking_color(self):
        snapshot = {
            "code": self.marking.code,
            "type": self.marking.type,
            "catalog_txt": self.marking.catalog_txt,
            "inscription_txt": self.marking.inscription_txt,
            "desc": self.marking.desc,
            "post_office_id": self.marking.post_office_id,
            "shape_id": self.marking.shape_id,
            "lettering_id": self.marking.lettering_id,
            "color_id": None,
            "is_manuscript": self.marking.is_manuscript,
            "impression": self.marking.impression,
            "is_irreg": self.marking.is_irreg,
            "width": self.marking.width,
            "height": self.marking.height,
            "date_fmt": self.marking.date_fmt,
            "rate_val": self.marking.rate_val,
        }

        restore_marking_from_snapshot(self.marking, snapshot, self.editor)

        self.marking.refresh_from_db()
        self.assertIsNone(self.marking.color_id)

    def test_restore_marking_is_idempotent(self):
        backup_path = self._backup()
        call_command("wipe_user_data", no_input=True, verbosity=0)

        call_command("restore_marking", str(backup_path), verbosity=0)
        first_counts = self._round_trip_counts()

        call_command("restore_marking", str(backup_path), verbosity=0)
        self.assertEqual(self._round_trip_counts(), first_counts)

    def test_restore_marking_dry_run_rolls_back(self):
        backup_path = self._backup()
        call_command("wipe_user_data", no_input=True, verbosity=0)

        call_command("restore_marking", str(backup_path), dry_run=True, verbosity=0)

        self.assertFalse(Contribution.objects.exists())
        self.assertFalse(SubmissionTransaction.objects.exists())
        self.assertFalse(MarkingVersion.objects.exists())

    def test_restore_marking_requires_local_contributor_user(self):
        payload = self._payload()
        payload["datasets"]["contributions"]["rows"][0]["contributor"] = (
            "missing-user"
        )
        edited_path = self.tmp_path / "missing-user.json"
        edited_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        call_command("wipe_user_data", no_input=True, verbosity=0)

        with self.assertRaises(CommandError):
            call_command("restore_marking", str(edited_path), verbosity=0)
        self.assertFalse(Contribution.objects.exists())

    def test_restore_marking_maps_stale_collection_name_by_region(self):
        payload = self._payload()
        payload["datasets"]["collections"]["rows"][0]["name"] = "Virgin Islands"
        payload["datasets"]["collections"]["rows"][0]["region"] = "Virginia"
        payload["datasets"]["contributions"]["rows"][0]["collection"] = (
            "Virgin Islands"
        )
        edited_path = self.tmp_path / "stale-collection.json"
        edited_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        call_command("wipe_user_data", no_input=True, verbosity=0)
        call_command("restore_marking", str(edited_path), verbosity=0)

        restored = Contribution.objects.get(marking=self.marking)
        self.assertEqual(restored.collection, self.collection)
        self.assertEqual(self.collection.name, "Virginia")

        client = APIClient()
        client.force_authenticate(self.virginia_editor)
        response = client.get(
            "/api/v2/contributions/",
            {"mode": "editor"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data.get("results", response.data)
        self.assertEqual([row["id"] for row in rows], [restored.pk])

    def test_restore_marking_round_trips_pending_cover_contribution(self):
        pending_cover = Contribution.objects.create(
            contributor=self.contributor,
            collection=self.collection,
            submitted_data={
                "submission_kind": "cover",
                "entity_type": "cover",
                "parent_marking_id": str(self.marking.pk),
                "marking_id": str(self.marking.pk),
                "state": "VA",
                "type": "FC",
                "cover_date": "1850-06-01",
                "cover_granularity": "DAY",
                "catalog_code": "ASCC1-VA-C0002",
                "image_meta": {"file_checksum": "cover-checksum"},
                "cover_image_metas": [
                    {
                        "storage_filename": "va/cover.jpg",
                        "original_filename": "cover.jpg",
                        "file_checksum": "cover-checksum",
                        "mime_type": "image/jpeg",
                        "image_width": 100,
                        "image_height": 80,
                        "file_size_bytes": 123,
                    }
                ],
            },
            status=Contribution.STATUS_PENDING,
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        backup_path = self._backup()
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
        rows = payload["datasets"]["contributions"]["rows"]
        cover_row = next(
            row
            for row in rows
            if row["id"] == str(pending_cover.pk)
        )
        self.assertEqual(cover_row["target_marking_code"], self.marking.code)

        cover_row["submitted_data"]["parent_marking_id"] = "999999"
        cover_row["submitted_data"]["marking_id"] = "999999"
        edited_path = self.tmp_path / "pending-cover.json"
        edited_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        call_command("wipe_user_data", no_input=True, verbosity=0)
        call_command("restore_marking", str(edited_path), verbosity=0)

        restored = Contribution.objects.get(
            submitted_data__catalog_code="ASCC1-VA-C0002",
        )
        self.assertEqual(restored.status, Contribution.STATUS_PENDING)
        self.assertIsNone(restored.marking_id)
        self.assertEqual(
            restored.submitted_data["parent_marking_id"],
            str(self.marking.pk),
        )
        self.assertEqual(restored.submitted_data["marking_id"], str(self.marking.pk))

    def test_restore_marking_repairs_existing_transaction_with_stale_marking_fk(self):
        backup_path = self._backup()
        marking_pk = self.marking.pk
        transaction_uuid = self.transaction.transaction_uuid

        # Simulates import_ascc_bundle --truncate without wipe_user_data: audit
        # rows keep their UUIDs, but their catalog FKs can point at deleted rows.
        table = connection.ops.quote_name(Marking._meta.db_table)
        with connection.constraint_checks_disabled(), connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {table} WHERE id = %s", [marking_pk])

        self.assertFalse(Marking.all_objects.filter(pk=marking_pk).exists())
        self.assertTrue(
            SubmissionTransaction.objects.filter(
                transaction_uuid=transaction_uuid,
                marking_id=marking_pk,
            ).exists()
        )

        call_command("restore_marking", str(backup_path), verbosity=0)

        restored = SubmissionTransaction.objects.get(
            transaction_uuid=transaction_uuid,
        )
        self.assertEqual(restored.marking.code, self.marking.code)
        self.assertEqual(restored.contribution.marking.code, self.marking.code)

    def _round_trip_counts(self):
        return {
            "contributions": Contribution.objects.count(),
            "submission_transactions": SubmissionTransaction.objects.count(),
            "marking_versions": MarkingVersion.objects.count(),
            "dates_seen": DateSeen.objects.count(),
            "images": Image.objects.count(),
            "citations": Citation.objects.count(),
        }
