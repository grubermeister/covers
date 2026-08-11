import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from common.models import (
    Collection,
    Color,
    Cover,
    CoverMarking,
    Marking,
    PostOffice,
    PostOfficeRegion,
    Region,
)


User = get_user_model()


class UserMarkingsBatchTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = Path(self.tmp.name) / "backups"

        self.editor = User.objects.create_superuser(
            username="editor", email="editor@example.com", password="pw"
        )
        audit = {"created_by": self.editor, "modified_by": self.editor}
        self.color = Color.objects.create(name="Black", **audit)
        self.region = Region.objects.create(
            name="Virginia", abbrev="VA", code="USA-VA1", region_tier="STATE", **audit
        )
        Collection.objects.create(name="Virginia", region=self.region, **audit)
        self.post_office = PostOffice.objects.create(
            name="Richmond", code="USA-VA1-RICH", **audit
        )
        PostOfficeRegion.objects.create(
            post_office=self.post_office, region=self.region, **audit
        )

        # Signal: has a cover -> must be backed up.
        self.covered = Marking.objects.create(
            code="ASCC1-VA-M0001",
            type="TOWNMARK",
            inscription_txt="RICHMOND VA",
            is_manuscript=False,
            color=self.color,
            post_office=self.post_office,
            **audit,
        )
        self.cover = Cover.objects.create(
            code="ASCC1-VA-C0001", color=self.color, type="FC", **audit
        )
        CoverMarking.objects.create(
            cover=self.cover,
            marking=self.covered,
            review_status=CoverMarking.REVIEW_APPROVED,
            reviewer=self.editor,
            **audit,
        )

        # Signal: editor-vetted, no cover -> must still be backed up.
        self.reviewed = Marking.objects.create(
            code="ASCC1-VA-M0002",
            type="RATEMARK",
            inscription_txt="PAID 5",
            is_manuscript=False,
            post_office=self.post_office,
            is_reviewed=True,
            **audit,
        )

        # Pure catalog row, untouched -> must NOT be backed up.
        self.pristine = Marking.objects.create(
            code="ASCC1-VA-M0003",
            type="AUXMARK",
            inscription_txt="FORWARDED",
            is_manuscript=False,
            post_office=self.post_office,
            **audit,
        )

    def _manifest(self):
        return json.loads((self.out_dir / "manifest.json").read_text())

    def test_backup_selects_user_content_markings_only(self):
        call_command("backup_user_markings", str(self.out_dir), verbosity=0)

        manifest = self._manifest()
        self.assertEqual(
            manifest["codes"], [self.covered.code, self.reviewed.code]
        )
        self.assertTrue((self.out_dir / f"{self.covered.code}.json").exists())
        self.assertTrue((self.out_dir / f"{self.reviewed.code}.json").exists())
        self.assertFalse((self.out_dir / f"{self.pristine.code}.json").exists())

    def test_backup_reports_markings_without_code(self):
        no_code = Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="NORFOLK VA",
            is_manuscript=False,
            post_office=self.post_office,
            is_reviewed=True,
            created_by=self.editor,
            modified_by=self.editor,
        )
        call_command("backup_user_markings", str(self.out_dir), verbosity=0)
        self.assertEqual(
            self._manifest()["skipped_no_code_marking_pks"], [no_code.pk]
        )

    def test_round_trip_restores_covers_after_state_drop(self):
        call_command("backup_user_markings", str(self.out_dir), verbosity=0)
        call_command("drop_ascc_state", "VA", verbosity=0)
        self.assertFalse(Cover.all_objects.filter(code=self.cover.code).exists())
        self.assertFalse(Marking.all_objects.filter(code=self.covered.code).exists())

        call_command("restore_user_markings", str(self.out_dir), verbosity=0)

        restored_cover = Cover.all_objects.get(code=self.cover.code)
        self.assertTrue(
            CoverMarking.objects.filter(
                cover=restored_cover, marking__code=self.covered.code
            ).exists()
        )
        self.assertTrue(
            Marking.all_objects.filter(code=self.reviewed.code).exists()
        )
        report = json.loads((self.out_dir / "restore_report.json").read_text())
        self.assertEqual(len(report["restored"]), 2)
        self.assertEqual(report["failures"], [])

    def test_restore_continues_past_bad_file_and_reports_it(self):
        call_command("backup_user_markings", str(self.out_dir), verbosity=0)
        bad = self.out_dir / "AAA-corrupt.json"
        bad.write_text("{not json")

        with self.assertRaises(CommandError):
            call_command("restore_user_markings", str(self.out_dir), verbosity=0)

        report = json.loads((self.out_dir / "restore_report.json").read_text())
        self.assertEqual(len(report["restored"]), 2)
        self.assertEqual(len(report["failures"]), 1)
        self.assertEqual(report["failures"][0]["file"], bad.name)
