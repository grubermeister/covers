import csv
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from common.models import (
    Citation,
    Color,
    Collection,
    Cover,
    CoverMarking,
    CoverValuation,
    DateSeen,
    Image,
    Marking,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
)


User = get_user_model()


AUDIT_COLUMNS = ["created_date", "modified_date", "created_by", "modified_by"]


def _write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _stamp(row, user):
    out = dict(row)
    out.update({
        "created_date": "2026-01-01T00:00:00+00:00",
        "modified_date": "2026-01-01T00:00:00+00:00",
        "created_by": str(user.pk),
        "modified_by": str(user.pk),
    })
    return out


class AsccAdditiveImportTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = Path(self.tmp.name) / "bundle"
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pw",
        )
        self._write_bundle()

    def _write_bundle(self):
        user = self.user
        _write_csv(
            self.bundle / "colors.csv",
            ["name", "hex_val", "pantone_code", *AUDIT_COLUMNS],
            [_stamp({"name": "BLACK", "hex_val": "#000000", "pantone_code": ""}, user)],
        )
        _write_csv(
            self.bundle / "letterings.csv",
            ["name", *AUDIT_COLUMNS],
            [_stamp({"name": "Italic"}, user)],
        )
        _write_csv(
            self.bundle / "shapes.csv",
            ["name", "code", *AUDIT_COLUMNS],
            [_stamp({"name": "C - Circle", "code": "C"}, user)],
        )
        _write_csv(
            self.bundle / "regions.csv",
            ["code", "name", "abbrev", "region_tier", "parent_region", "established_date", "defunct_date", *AUDIT_COLUMNS],
            [
                _stamp({
                    "code": "USA",
                    "name": "United States",
                    "abbrev": "USA",
                    "region_tier": "COUNTRY",
                    "parent_region": "",
                    "established_date": "1776-07-04",
                    "defunct_date": "",
                }, user),
                _stamp({
                    "code": "USA-VA1",
                    "name": "Virginia",
                    "abbrev": "VA",
                    "region_tier": "STATE",
                    "parent_region": "USA",
                    "established_date": "1788-06-25",
                    "defunct_date": "",
                }, user),
            ],
        )
        _write_csv(
            self.bundle / "reference_works.csv",
            ["code", "title", "authorship", "publisher", "publication_year", "edition", "volume", "isbn", "url", *AUDIT_COLUMNS],
            [_stamp({
                "code": "ASCC1",
                "title": "American Stampless Cover Catalog",
                "authorship": "Author",
                "publisher": "Publisher",
                "publication_year": "1900",
                "edition": "1",
                "volume": "",
                "isbn": "",
                "url": "",
            }, user)],
        )
        _write_csv(
            self.bundle / "post_offices.csv",
            ["name", "code", *AUDIT_COLUMNS],
            [_stamp({"name": "RICHMOND", "code": "USA-VA1-1"}, user)],
        )
        _write_csv(
            self.bundle / "post_office_regions.csv",
            ["post_office", "region", *AUDIT_COLUMNS],
            [_stamp({"post_office": "USA-VA1-1", "region": "USA-VA1"}, user)],
        )
        _write_csv(
            self.bundle / "markings.csv",
            [
                "code",
                "type",
                "catalog_txt",
                "inscription_txt",
                "desc",
                "is_manuscript",
                "shape",
                "lettering",
                "color",
                "is_irreg",
                "width",
                "height",
                "date_fmt",
                "impression",
                "rate_val",
                "post_office",
                *AUDIT_COLUMNS,
            ],
            [_stamp({
                "code": "ASCC1-VA-M1001",
                "type": "TOWNMARK",
                "catalog_txt": "RICHMOND",
                "inscription_txt": "RICHMOND",
                "desc": "",
                "is_manuscript": "False",
                "shape": "C - Circle",
                "lettering": "Italic",
                "color": "BLACK",
                "is_irreg": "False",
                "width": "26",
                "height": "27",
                "date_fmt": "YD",
                "impression": "Normal",
                "rate_val": "",
                "post_office": "USA-VA1-1",
            }, user)],
        )
        _write_csv(
            self.bundle / "covers.csv",
            ["code", "color", "type", "has_adhesive", "height", "is_institutional", "width", "display_submitter_name", "description", *AUDIT_COLUMNS],
            [_stamp({
                "code": "ASCC1-VA-C1001",
                "color": "BLACK",
                "type": "FC",
                "has_adhesive": "False",
                "height": "80",
                "is_institutional": "False",
                "width": "120",
                "display_submitter_name": "False",
                "description": "imported cover",
            }, user)],
        )
        _write_csv(
            self.bundle / "cover_valuations.csv",
            ["cover", "amt", "appraisal_date", *AUDIT_COLUMNS],
            [_stamp({"cover": "ASCC1-VA-C1001", "amt": "10.00", "appraisal_date": "1900-01-01"}, user)],
        )
        _write_csv(
            self.bundle / "dates_seen.csv",
            ["subject_type", "subject_id", "date", "granularity", *AUDIT_COLUMNS],
            [_stamp({"subject_type": "MARKING", "subject_id": "ASCC1-VA-M1001", "date": "1850-01-01", "granularity": "YEAR"}, user)],
        )
        _write_csv(
            self.bundle / "cover_markings.csv",
            ["cover", "marking", "is_backstamp", "placement", "contributor_comment", "review_status", "reviewer", "review_notes", "reviewed_at", *AUDIT_COLUMNS],
            [_stamp({
                "cover": "ASCC1-VA-C1001",
                "marking": "ASCC1-VA-M1001",
                "is_backstamp": "False",
                "placement": "",
                "contributor_comment": "",
                "review_status": CoverMarking.REVIEW_APPROVED,
                "reviewer": "",
                "review_notes": "",
                "reviewed_at": "",
            }, user)],
        )
        _write_csv(
            self.bundle / "citations.csv",
            ["reference_work", "subject_type", "subject_id", "citation_detail", *AUDIT_COLUMNS],
            [_stamp({"reference_work": "ASCC1", "subject_type": "MARKING", "subject_id": "ASCC1-VA-M1001", "citation_detail": "p. 1"}, user)],
        )
        _write_csv(
            self.bundle / "images.csv",
            [
                "subject_type",
                "subject_id",
                "original_filename",
                "storage_filename",
                "file_checksum",
                "mime_type",
                "image_width",
                "image_height",
                "file_size_bytes",
                "image_view",
                "image_description",
                "is_tracing",
                "display_order",
                "uploaded_by",
                *AUDIT_COLUMNS,
            ],
            [_stamp({
                "subject_type": "MARKING",
                "subject_id": "ASCC1-VA-M1001",
                "original_filename": "richmond.png",
                "storage_filename": "va/richmond.png",
                "file_checksum": "abc",
                "mime_type": "image/png",
                "image_width": "10",
                "image_height": "11",
                "file_size_bytes": "12",
                "image_view": "FULL",
                "image_description": "",
                "is_tracing": "True",
                "display_order": "1",
                "uploaded_by": str(user.pk),
            }, user)],
        )

    def test_import_reimport_skips_existing_and_preserves_manual_edit(self):
        call_command("import_ascc_bundle", str(self.bundle), verbosity=0)

        marking = Marking.objects.get(code="ASCC1-VA-M1001")
        self.assertEqual(marking.post_office.code, "USA-VA1-1")
        self.assertEqual(DateSeen.objects.get().subject_id, marking.pk)
        self.assertEqual(Citation.objects.get().subject_id, marking.pk)
        self.assertEqual(Image.objects.get().subject_id, marking.pk)
        self.assertEqual(CoverValuation.objects.get().cover.code, "ASCC1-VA-C1001")

        marking.inscription_txt = "MANUAL EDIT"
        marking.save()
        call_command("import_ascc_bundle", str(self.bundle), verbosity=0)

        self.assertEqual(Marking.objects.count(), 1)
        self.assertEqual(PostOffice.objects.count(), 1)
        self.assertEqual(CoverValuation.objects.count(), 1)
        self.assertEqual(Marking.objects.get().inscription_txt, "MANUAL EDIT")

    def test_drop_ascc_state_dry_run_then_delete(self):
        call_command("import_ascc_bundle", str(self.bundle), verbosity=0)

        call_command("drop_ascc_state", "VA", dry_run=True, verbosity=0)
        self.assertEqual(Marking.objects.count(), 1)
        self.assertEqual(Cover.objects.count(), 1)

        call_command("drop_ascc_state", "VA", verbosity=0)

        self.assertEqual(Marking.all_objects.count(), 0)
        self.assertEqual(PostOffice.objects.count(), 0)
        self.assertEqual(Cover.all_objects.count(), 0)
        self.assertEqual(DateSeen.objects.count(), 0)
        self.assertEqual(Citation.objects.count(), 0)
        self.assertEqual(Image.objects.count(), 0)
        self.assertEqual(CoverValuation.objects.count(), 0)
        self.assertEqual(Color.objects.count(), 1)
        self.assertEqual(Region.objects.count(), 2)
        self.assertEqual(ReferenceWork.objects.count(), 1)
        self.assertEqual(Collection.objects.count(), 2)
