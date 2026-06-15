from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from common.models import (
    Citation,
    Color,
    Cover,
    CoverRecycleBin,
    DateSeen,
    Image,
    Marking,
    MarkingRecycleBin,
    PostOffice,
    ReferenceWork,
    SubmissionTransaction,
)


User = get_user_model()


class PurgeRecycleBinCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("purge-user", password="pw")
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
        self.reference_work = ReferenceWork.objects.create(
            code="REF1",
            title="Reference Work",
            authorship="Author",
            publisher="Publisher",
            publication_year=1900,
            created_by=self.user,
            modified_by=self.user,
        )

    def test_dry_run_reports_deletions_and_leaves_records_restorable(self):
        binned_marking = self._marking("BINNED MARKING")
        binned_cover = self._cover("C-BINNED")
        self._bin_marking(binned_marking)
        self._bin_cover(binned_cover)

        out = StringIO()
        call_command("purge_recycle_bin", dry_run=True, no_input=True, stdout=out)

        self.assertIn("[DRY RUN]", out.getvalue())
        self.assertTrue(Marking.all_objects.filter(pk=binned_marking.pk).exists())
        self.assertTrue(Cover.all_objects.filter(pk=binned_cover.pk).exists())
        self.assertTrue(
            MarkingRecycleBin.objects.filter(marking=binned_marking).exists()
        )
        self.assertTrue(CoverRecycleBin.objects.filter(cover=binned_cover).exists())
        self.assertFalse(Marking.objects.filter(pk=binned_marking.pk).exists())
        self.assertFalse(Cover.objects.filter(pk=binned_cover.pk).exists())

    def test_default_command_aborts_without_exact_confirmation(self):
        binned_marking = self._marking("BINNED MARKING")
        self._bin_marking(binned_marking)

        with patch("builtins.input", return_value="no"):
            with self.assertRaises(CommandError):
                call_command("purge_recycle_bin", stdout=StringIO())

        self.assertTrue(Marking.all_objects.filter(pk=binned_marking.pk).exists())
        self.assertTrue(
            MarkingRecycleBin.objects.filter(marking=binned_marking).exists()
        )

    def test_no_input_purges_only_binned_subjects_and_their_polymorphic_rows(self):
        binned_marking = self._marking("BINNED MARKING")
        kept_marking = self._marking("KEPT MARKING")
        binned_cover = self._cover("C-BINNED")
        kept_cover = self._cover("C-KEPT")
        self._bin_marking(binned_marking)
        self._bin_cover(binned_cover)
        binned_marking_txn = self._transaction(marking=binned_marking)
        binned_cover_txn = self._transaction(cover=binned_cover)

        self._polymorphic_rows("MARKING", binned_marking.pk, "binned-marking")
        self._polymorphic_rows("MARKING", kept_marking.pk, "kept-marking")
        self._polymorphic_rows("COVER", binned_cover.pk, "binned-cover")
        self._polymorphic_rows("COVER", kept_cover.pk, "kept-cover")

        call_command("purge_recycle_bin", no_input=True, stdout=StringIO())

        self.assertFalse(Marking.all_objects.filter(pk=binned_marking.pk).exists())
        self.assertFalse(Cover.all_objects.filter(pk=binned_cover.pk).exists())
        self.assertFalse(
            MarkingRecycleBin.objects.filter(marking_id=binned_marking.pk).exists()
        )
        self.assertFalse(
            CoverRecycleBin.objects.filter(cover_id=binned_cover.pk).exists()
        )
        self.assert_subject_rows_deleted("MARKING", binned_marking.pk)
        self.assert_subject_rows_deleted("COVER", binned_cover.pk)

        self.assertTrue(Marking.objects.filter(pk=kept_marking.pk).exists())
        self.assertTrue(Cover.objects.filter(pk=kept_cover.pk).exists())
        self.assert_subject_rows_kept("MARKING", kept_marking.pk)
        self.assert_subject_rows_kept("COVER", kept_cover.pk)

        binned_marking_txn.refresh_from_db()
        binned_cover_txn.refresh_from_db()
        self.assertIsNone(binned_marking_txn.marking_id)
        self.assertIsNone(binned_cover_txn.cover_id)

    def _marking(self, text):
        return Marking.objects.create(
            type="TOWNMARK",
            inscription_txt=text,
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.user,
            modified_by=self.user,
        )

    def _cover(self, code):
        return Cover.objects.create(
            code=code,
            created_by=self.user,
            modified_by=self.user,
        )

    def _bin_marking(self, marking):
        return MarkingRecycleBin.objects.create(
            marking=marking,
            removed_by=self.user,
            reason="test",
        )

    def _bin_cover(self, cover):
        return CoverRecycleBin.objects.create(
            cover=cover,
            removed_by=self.user,
            reason="test",
        )

    def _transaction(self, *, marking=None, cover=None):
        return SubmissionTransaction.objects.create(
            actor=self.user,
            action=SubmissionTransaction.ACTION_MARKING_REMOVED,
            marking=marking,
            cover=cover,
            source=SubmissionTransaction.SOURCE_SYSTEM,
        )

    def _polymorphic_rows(self, subject_type, subject_id, label):
        Image.objects.create(
            subject_type=subject_type,
            subject_id=subject_id,
            original_filename=f"{label}.jpg",
            storage_filename=f"{label}.jpg",
            file_checksum=label,
            mime_type="image/jpeg",
            image_width=100,
            image_height=100,
            file_size_bytes=1000,
            image_view="FULL" if subject_type == "MARKING" else "FRONT",
            uploaded_by=self.user,
            created_by=self.user,
            modified_by=self.user,
        )
        DateSeen.objects.create(
            subject_type=subject_type,
            subject_id=subject_id,
            date="1860-01-01",
            granularity="DAY",
            created_by=self.user,
            modified_by=self.user,
        )
        Citation.objects.create(
            reference_work=self.reference_work,
            subject_type=subject_type,
            subject_id=subject_id,
            citation_detail="p. 1",
            created_by=self.user,
            modified_by=self.user,
        )

    def assert_subject_rows_deleted(self, subject_type, subject_id):
        self.assertFalse(
            Image.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).exists()
        )
        self.assertFalse(
            DateSeen.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).exists()
        )
        self.assertFalse(
            Citation.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).exists()
        )

    def assert_subject_rows_kept(self, subject_type, subject_id):
        self.assertTrue(
            Image.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).exists()
        )
        self.assertTrue(
            DateSeen.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).exists()
        )
        self.assertTrue(
            Citation.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).exists()
        )
