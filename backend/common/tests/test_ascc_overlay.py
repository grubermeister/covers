import csv
import sys
import tempfile
from pathlib import Path

from PIL import Image as PILImage

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from common.models import (
    Citation,
    Color,
    DateSeen,
    Image,
    Marking,
    MarkingRecycleBin,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
    Shape,
    SubmissionTransaction,
)


REPO_ROOT = Path(settings.REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.v1_to_v2_catalog_format import convert, normalize_page  # noqa: E402


User = get_user_model()


def write_csv(path: Path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class V1ToV2CatalogFormatTests(SimpleTestCase):
    def test_normalize_page_collapses_integral_float(self):
        self.assertEqual(normalize_page("419.0"), "419")
        self.assertEqual(normalize_page("419.5"), "419.5")
        self.assertEqual(normalize_page(""), "")

    def test_convert_carries_txt_pdf_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src = tmp / "v1.csv"
            out = tmp / "out.csv"
            write_csv(
                src,
                ["txtRawStateData", "nRawStateDataID", "txtPDFPage"],
                [
                    {
                        "txtRawStateData": "RICHMOND/VA.(1840;Black) 10",
                        "nRawStateDataID": "71",
                        "txtPDFPage": "419.0",
                    }
                ],
            )
            written, row_ids = convert(src, out, {})
            self.assertEqual(written, 1)
            self.assertEqual(row_ids, ["71"])
            rows = list(csv.DictReader(out.open(newline="", encoding="utf-8")))
            self.assertEqual(rows[0]["Page"], "419")


class ApplyAscc2OverlayTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username="editor", password="pw")
        self.region = Region.objects.create(
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.actor,
            modified_by=self.actor,
        )
        self.color = Color.objects.create(
            name="Black",
            created_by=self.actor,
            modified_by=self.actor,
        )
        self.shape = Shape.objects.create(
            name="C - Circle",
            code=None,
            created_by=self.actor,
            modified_by=self.actor,
        )
        self.post_office = PostOffice.objects.create(
            name="Richmond",
            created_by=self.actor,
            modified_by=self.actor,
        )
        PostOfficeRegion.objects.create(
            post_office=self.post_office,
            region=self.region,
            created_by=self.actor,
            modified_by=self.actor,
        )
        self.ascc1 = ReferenceWork.objects.create(
            code="ASCC1",
            title="ASCC Fifth",
            authorship="Test",
            publisher="Test",
            publication_year=2000,
            created_by=self.actor,
            modified_by=self.actor,
        )
        self.ascc2 = ReferenceWork.objects.create(
            code="ASCC2",
            title="ASCC Sixth",
            authorship="Test",
            publisher="Test",
            publication_year=2005,
            created_by=self.actor,
            modified_by=self.actor,
        )

    def make_bundle_dir(self, root: Path, lineage_rows, marking_rows=None, citation_rows=None, date_rows=None):
        bundle = root
        common_lookup = {
            "colors.csv": (
                ["id", "name", "hex_val", "pantone_code", "created_date", "modified_date", "created_by", "modified_by"],
                [
                    {
                        "id": 1,
                        "name": "Black",
                        "hex_val": "",
                        "pantone_code": "",
                        "created_date": "2026-01-01T00:00:00Z",
                        "modified_date": "2026-01-01T00:00:00Z",
                        "created_by": self.actor.id,
                        "modified_by": self.actor.id,
                    }
                ],
            ),
            "shapes.csv": (
                ["id", "name", "code", "created_date", "modified_date", "created_by", "modified_by"],
                [
                    {
                        "id": 1,
                        "name": "C - Circle",
                        "code": "",
                        "created_date": "2026-01-01T00:00:00Z",
                        "modified_date": "2026-01-01T00:00:00Z",
                        "created_by": self.actor.id,
                        "modified_by": self.actor.id,
                    }
                ],
            ),
            "letterings.csv": (
                ["id", "name", "created_date", "modified_date", "created_by", "modified_by"],
                [],
            ),
            "post_offices.csv": (
                ["id", "name", "created_date", "modified_date", "created_by", "modified_by"],
                [
                    {
                        "id": 1,
                        "name": "Richmond",
                        "created_date": "2026-01-01T00:00:00Z",
                        "modified_date": "2026-01-01T00:00:00Z",
                        "created_by": self.actor.id,
                        "modified_by": self.actor.id,
                    }
                ],
            ),
            "markings.csv": (
                [
                    "id", "code", "type", "catalog_txt", "inscription_txt", "desc",
                    "is_manuscript", "shape", "lettering", "color", "is_irreg", "width",
                    "height", "date_fmt", "impression", "rate_val", "post_office",
                    "created_date", "modified_date", "created_by", "modified_by",
                ],
                marking_rows or [],
            ),
            "dates_seen.csv": (
                ["id", "subject_type", "subject_id", "date", "granularity", "created_date", "modified_date", "created_by", "modified_by"],
                date_rows or [],
            ),
            "citations.csv": (
                ["id", "reference_work", "subject_type", "subject_id", "citation_detail", "created_date", "modified_date", "created_by", "modified_by"],
                citation_rows or [],
            ),
            "marking_lineage.csv": (
                [
                    "marking_id", "marking_code", "mark_type", "mark_kind", "local_index",
                    "source_listing_idx", "source_chunk", "source_page", "family_root_idx",
                    "family_root_chunk", "family_root_page", "family_role",
                    "parent_mark_type", "parent_mark_source_id", "parent_local_index",
                    "color_name", "fanout_index", "inscription_txt", "catalog_txt", "rate_raw",
                ],
                lineage_rows,
            ),
        }
        for filename, (fieldnames, rows) in common_lookup.items():
            write_csv(bundle / filename, fieldnames, rows)
        return bundle

    def make_media_file(self, relative_name: str):
        path = REPO_ROOT / "backend" / "media" / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        PILImage.new("RGB", (4, 4), color="black").save(path)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_apply_overlay_updates_marking_and_adds_v1_images(self):
        marking = Marking.objects.create(
            code="ASCC1-VA-1",
            type="TOWNMARK",
            catalog_txt="RICHMOND/VA.(1840;Black) 10",
            inscription_txt="RICHMOND/VA.",
            is_manuscript=False,
            shape=self.shape,
            color=self.color,
            is_irreg=False,
            impression="Normal",
            post_office=self.post_office,
            created_by=self.actor,
            modified_by=self.actor,
        )
        Citation.objects.create(
            reference_work=self.ascc1,
            subject_type="MARKING",
            subject_id=marking.pk,
            citation_detail="410",
            created_by=self.actor,
            modified_by=self.actor,
        )
        Image.objects.create(
            subject_type="MARKING",
            subject_id=marking.pk,
            original_filename="baseline.png",
            storage_filename="va/baseline.png",
            file_checksum="abc123",
            mime_type="image/png",
            image_width=10,
            image_height=10,
            file_size_bytes=100,
            image_view="FULL",
            image_description="baseline",
            is_tracing=True,
            display_order=1,
            uploaded_by=self.actor,
            created_by=self.actor,
            modified_by=self.actor,
        )

        self.make_media_file("va/from-v1.png")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            base_dir = self.make_bundle_dir(
                tmp / "base",
                lineage_rows=[
                    {
                        "marking_id": 1,
                        "marking_code": "ASCC1-VA-1",
                        "mark_type": "TOWNMARK",
                        "mark_kind": "TM",
                        "local_index": 0,
                        "source_listing_idx": 0,
                        "source_chunk": "11",
                        "source_page": "410",
                        "family_root_idx": 0,
                        "family_root_chunk": "11",
                        "family_root_page": "410",
                        "family_role": "parent",
                        "parent_mark_type": "",
                        "parent_mark_source_id": "",
                        "parent_local_index": "",
                        "color_name": "Black",
                        "fanout_index": "0",
                        "inscription_txt": "RICHMOND/VA.",
                        "catalog_txt": "RICHMOND/VA.(1840;Black) 10",
                        "rate_raw": "",
                    }
                ],
            )
            overlay_dir = self.make_bundle_dir(
                tmp / "overlay",
                lineage_rows=[
                    {
                        "marking_id": 1,
                        "marking_code": "ASCC2-VA-1",
                        "mark_type": "TOWNMARK",
                        "mark_kind": "TM",
                        "local_index": 0,
                        "source_listing_idx": 0,
                        "source_chunk": "71",
                        "source_page": "419",
                        "family_root_idx": 0,
                        "family_root_chunk": "71",
                        "family_root_page": "419",
                        "family_role": "parent",
                        "parent_mark_type": "",
                        "parent_mark_source_id": "",
                        "parent_local_index": "",
                        "color_name": "Black",
                        "fanout_index": "0",
                        "inscription_txt": "RICHMOND/VA.",
                        "catalog_txt": "RICHMOND/VA.(1841;Black) 20",
                        "rate_raw": "",
                    }
                ],
                marking_rows=[
                    {
                        "id": 1,
                        "code": "ASCC2-VA-1",
                        "type": "TOWNMARK",
                        "catalog_txt": "RICHMOND/VA.(1841;Black) 20",
                        "inscription_txt": "RICHMOND/VA.",
                        "desc": "",
                        "is_manuscript": "False",
                        "shape": 1,
                        "lettering": "",
                        "color": 1,
                        "is_irreg": "False",
                        "width": "",
                        "height": "",
                        "date_fmt": "",
                        "impression": "Normal",
                        "rate_val": "",
                        "post_office": 1,
                        "created_date": "2026-01-01T00:00:00Z",
                        "modified_date": "2026-01-01T00:00:00Z",
                        "created_by": self.actor.id,
                        "modified_by": self.actor.id,
                    }
                ],
                citation_rows=[
                    {
                        "id": 1,
                        "reference_work": self.ascc2.id,
                        "subject_type": "MARKING",
                        "subject_id": 1,
                        "citation_detail": "419",
                        "created_date": "2026-01-01T00:00:00Z",
                        "modified_date": "2026-01-01T00:00:00Z",
                        "created_by": self.actor.id,
                        "modified_by": self.actor.id,
                    }
                ],
                date_rows=[
                    {
                        "id": 1,
                        "subject_type": "MARKING",
                        "subject_id": 1,
                        "date": "1841-01-01",
                        "granularity": "YEAR",
                        "created_date": "2026-01-01T00:00:00Z",
                        "modified_date": "2026-01-01T00:00:00Z",
                        "created_by": self.actor.id,
                        "modified_by": self.actor.id,
                    }
                ],
            )
            overlay_map = tmp / "overlay_map.csv"
            write_csv(
                overlay_map,
                [
                    "compare_source_id", "compare_representative_id", "compare_family_id",
                    "compare_family_chunk", "compare_family_page", "compare_chunk",
                    "compare_page", "base_source_id", "base_representative_id",
                    "base_family_id", "base_chunk", "base_page", "row_disposition",
                    "content_change", "representative_row_disposition",
                    "representative_content_change", "is_compare_duplicate",
                    "include_in_overlay", "family_action",
                ],
                [
                    {
                        "compare_source_id": "COMPARE:71",
                        "compare_representative_id": "COMPARE:71",
                        "compare_family_id": "COMPARE:71",
                        "compare_family_chunk": "71",
                        "compare_family_page": "419",
                        "compare_chunk": "71",
                        "compare_page": "419",
                        "base_source_id": "BASE:410:11",
                        "base_representative_id": "BASE:410:11",
                        "base_family_id": "BASE:410:11",
                        "base_chunk": "11",
                        "base_page": "410",
                        "row_disposition": "matched",
                        "content_change": "material",
                        "representative_row_disposition": "matched",
                        "representative_content_change": "material",
                        "is_compare_duplicate": "false",
                        "include_in_overlay": "true",
                        "family_action": "material",
                    }
                ],
            )
            image_refs = tmp / "image_refs.csv"
            write_csv(
                image_refs,
                [
                    "source_row_id", "townmark_image_id", "source_filename",
                    "storage_filename", "display_order", "image_view",
                    "image_description", "is_tracing",
                ],
                [
                    {
                        "source_row_id": "71",
                        "townmark_image_id": "900",
                        "source_filename": "from-v1.png",
                        "storage_filename": "va/from-v1.png",
                        "display_order": "1",
                        "image_view": "FULL",
                        "image_description": "v1 image",
                        "is_tracing": "False",
                    }
                ],
            )

            call_command(
                "apply_ascc2_overlay",
                base_dir=str(base_dir),
                overlay_dir=str(overlay_dir),
                overlay_map=str(overlay_map),
                v1_image_refs=str(image_refs),
                region_abbrev="VA",
                ascc1_code="ASCC1",
                ascc2_code="ASCC2",
                audit_user_id=self.actor.id,
            )

        marking.refresh_from_db()
        self.assertEqual(marking.code, "ASCC2-VA-1")
        self.assertEqual(marking.catalog_txt, "RICHMOND/VA.(1841;Black) 20")
        citations = list(Citation.objects.filter(subject_type="MARKING", subject_id=marking.pk))
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0].reference_work_id, self.ascc2.id)
        self.assertEqual(citations[0].citation_detail, "419")
        self.assertEqual(
            DateSeen.objects.filter(subject_type="MARKING", subject_id=marking.pk).count(),
            1,
        )
        images = list(Image.objects.filter(subject_type="MARKING", subject_id=marking.pk).order_by("display_order"))
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0].storage_filename, "va/baseline.png")
        self.assertEqual(images[1].storage_filename, "va/from-v1.png")
        actions = list(
            SubmissionTransaction.objects.filter(marking=marking).values_list("action", flat=True)
        )
        self.assertIn(SubmissionTransaction.ACTION_RECORD_CREATE, actions)
        self.assertIn(SubmissionTransaction.ACTION_CATALOG_DIRECT_EDIT, actions)

    def test_apply_overlay_soft_removes_base_only_family(self):
        marking = Marking.objects.create(
            code="ASCC1-VA-2",
            type="TOWNMARK",
            catalog_txt="NORFOLK/VA.(1840;Black) 10",
            inscription_txt="NORFOLK/VA.",
            is_manuscript=False,
            shape=self.shape,
            color=self.color,
            is_irreg=False,
            impression="Normal",
            post_office=self.post_office,
            created_by=self.actor,
            modified_by=self.actor,
        )
        Citation.objects.create(
            reference_work=self.ascc1,
            subject_type="MARKING",
            subject_id=marking.pk,
            citation_detail="411",
            created_by=self.actor,
            modified_by=self.actor,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            base_dir = self.make_bundle_dir(
                tmp / "base",
                lineage_rows=[
                    {
                        "marking_id": 2,
                        "marking_code": "ASCC1-VA-2",
                        "mark_type": "TOWNMARK",
                        "mark_kind": "TM",
                        "local_index": 0,
                        "source_listing_idx": 0,
                        "source_chunk": "12",
                        "source_page": "411",
                        "family_root_idx": 0,
                        "family_root_chunk": "12",
                        "family_root_page": "411",
                        "family_role": "parent",
                        "parent_mark_type": "",
                        "parent_mark_source_id": "",
                        "parent_local_index": "",
                        "color_name": "Black",
                        "fanout_index": "0",
                        "inscription_txt": "NORFOLK/VA.",
                        "catalog_txt": "NORFOLK/VA.(1840;Black) 10",
                        "rate_raw": "",
                    }
                ],
            )
            overlay_dir = self.make_bundle_dir(tmp / "overlay", lineage_rows=[])
            overlay_map = tmp / "overlay_map.csv"
            write_csv(
                overlay_map,
                [
                    "compare_source_id", "compare_representative_id", "compare_family_id",
                    "compare_family_chunk", "compare_family_page", "compare_chunk",
                    "compare_page", "base_source_id", "base_representative_id",
                    "base_family_id", "base_chunk", "base_page", "row_disposition",
                    "content_change", "representative_row_disposition",
                    "representative_content_change", "is_compare_duplicate",
                    "include_in_overlay", "family_action",
                ],
                [
                    {
                        "compare_source_id": "",
                        "compare_representative_id": "",
                        "compare_family_id": "",
                        "compare_family_chunk": "",
                        "compare_family_page": "",
                        "compare_chunk": "",
                        "compare_page": "",
                        "base_source_id": "BASE:411:12",
                        "base_representative_id": "BASE:411:12",
                        "base_family_id": "BASE:411:12",
                        "base_chunk": "12",
                        "base_page": "411",
                        "row_disposition": "removed",
                        "content_change": "",
                        "representative_row_disposition": "removed",
                        "representative_content_change": "",
                        "is_compare_duplicate": "false",
                        "include_in_overlay": "false",
                        "family_action": "removed",
                    }
                ],
            )
            image_refs = tmp / "image_refs.csv"
            write_csv(
                image_refs,
                [
                    "source_row_id", "townmark_image_id", "source_filename",
                    "storage_filename", "display_order", "image_view",
                    "image_description", "is_tracing",
                ],
                [],
            )

            call_command(
                "apply_ascc2_overlay",
                base_dir=str(base_dir),
                overlay_dir=str(overlay_dir),
                overlay_map=str(overlay_map),
                v1_image_refs=str(image_refs),
                region_abbrev="VA",
                ascc1_code="ASCC1",
                ascc2_code="ASCC2",
                audit_user_id=self.actor.id,
            )

        self.assertTrue(MarkingRecycleBin.objects.filter(marking=marking).exists())
