"""Tests for the v1-only ASCC pipeline helpers.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_v1_pipeline.py'

Expected exit code: 0.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import v1_bundle_overlay
import v1_attach_images
import v1_catalog_rows


AUDIT = {
    "created_date": "2026-01-01T00:00:00+00:00",
    "modified_date": "2026-01-01T00:00:00+00:00",
    "created_by": "1",
    "modified_by": "1",
}


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def stamped(row):
    out = dict(row)
    out.update(AUDIT)
    return out


class V1PipelineTests(unittest.TestCase):
    def test_v1_overlay_replaces_same_with_parent_townmark_text(self):
        cases = [
            ("Same/Wis.", "WATERTOWN/Wis.", "WATERTOWN/Wis."),
            ("(1)Same/Wis.", "(1)WATERTOWN/Wis.", "WATERTOWN/Wis."),
            ("Same", "CABOTVILLE / Ms.", "CABOTVILLE / Ms."),
            ("The same", "DETROIT / Mich.", "DETROIT / Mich."),
            ("Same VA./5", "WINCHESTER.VA", "WINCHESTER VA./5"),
            ("*Same VA./5", "*WINCHESTER.VA", "WINCHESTER VA./5"),
            ("Same *VA./5", "WINCHESTER.VA", "WINCHESTER VA./5"),
            ("(1)BETHANY/Va.", "", "BETHANY/Va."),
            ("*RICHMOND/VA.", "", "RICHMOND/VA."),
        ]
        for inscription, parent_text, expected in cases:
            with self.subTest(inscription=inscription):
                self.assertEqual(
                    v1_bundle_overlay.resolve_same_inscription(
                        inscription,
                        parent_text,
                    ),
                    expected,
                )

    def test_catalog_rows_include_approve_deleted_when_not_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            states = root / "tblStates.csv"
            raw = root / "tblRawStateData.csv"
            images = root / "tblTownmarkImages.csv"
            slice_out = root / "slice.csv"
            catalog_out = root / "catalog_rows.csv"
            refs_out = root / "image_refs.csv"
            write_csv(states, ["nStateID", "txtStateAbv"], [{"nStateID": "46", "txtStateAbv": "VA"}])
            write_csv(
                raw,
                ["nRawStateDataID", "nStateID", "approve_status", "ynDeleted", "txtRawStateData"],
                [
                    {"nRawStateDataID": "1", "nStateID": "46", "approve_status": "Deleted", "ynDeleted": "FALSE", "txtRawStateData": "RICHMOND (1850;Black) 10"},
                    {"nRawStateDataID": "2", "nStateID": "46", "approve_status": "Approved", "ynDeleted": "TRUE", "txtRawStateData": "DROP (1850;Black) 10"},
                ],
            )
            write_csv(
                images,
                ["nRawStateDataID", "ynDeleted", "nTownmarkImageID", "txtFilename", "nOrder", "txtView"],
                [{"nRawStateDataID": "1", "ynDeleted": "False", "nTownmarkImageID": "9", "txtFilename": "a.png", "nOrder": "1", "txtView": "front"}],
            )

            rc = v1_catalog_rows.main([
                "VA",
                "--raw", str(raw),
                "--states", str(states),
                "--images", str(images),
                "--slice-out", str(slice_out),
                "--catalog-rows-out", str(catalog_out),
                "--image-refs-out", str(refs_out),
            ])
            catalog_rows = read_csv(catalog_out)
            refs = read_csv(refs_out)

        self.assertEqual(rc, 0)
        self.assertEqual([r["chunk_number"] for r in catalog_rows], ["1"])
        self.assertEqual(catalog_rows[0]["catalog_page"], "0")
        self.assertEqual(catalog_rows[0]["image_count"], "0")
        self.assertEqual(refs[0]["source_row_id"], "1")

    def test_catalog_rows_trim_glued_context_prefix_before_munger(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            slice_out = root / "slice.csv"
            catalog_out = root / "catalog_rows.csv"
            write_csv(
                slice_out,
                [
                    "nRawStateDataID",
                    "txtRawStateData",
                    "txtTown",
                    "txtTownPostmark",
                ],
                [
                    {
                        "nRawStateDataID": "23",
                        "txtRawStateData": (
                            "The British evacuated Norfolk in December "
                            "1775.Petersburg(Jan. 21, 1767;Ms;Black) 1,500"
                        ),
                        "txtTown": "Petersburg",
                        "txtTownPostmark": "Petersburg",
                    },
                    {
                        "nRawStateDataID": "82",
                        "txtRawStateData": (
                            "Richmond was occupied by the British on "
                            "January 5, 1781.Richmond(Aug. 23, 1782;Ms;Black) 600"
                        ),
                        "txtTown": "Richmond",
                        "txtTownPostmark": "",
                    },
                    {
                        "nRawStateDataID": "83",
                        "txtRawStateData": "Petersburg(Feb. 1, 1770;Ms;Black) 1,000",
                        "txtTown": "Petersburg",
                        "txtTownPostmark": "Petersburg",
                    },
                ],
            )

            rows_written, raw_ids = v1_catalog_rows.write_v1_catalog_rows(
                slice_out,
                catalog_out,
            )
            catalog_rows = read_csv(catalog_out)

        self.assertEqual(rows_written, 3)
        self.assertEqual(raw_ids, ["23", "82", "83"])
        self.assertEqual(
            catalog_rows[0]["listing_text"],
            "Petersburg(Jan. 21, 1767;Ms;Black) 1,500",
        )
        self.assertEqual(
            catalog_rows[1]["listing_text"],
            "Richmond(Aug. 23, 1782;Ms;Black) 600",
        )
        self.assertEqual(
            catalog_rows[2]["listing_text"],
            "Petersburg(Feb. 1, 1770;Ms;Black) 1,000",
        )

    def test_overlay_applies_v1_fields_and_attaches_images(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            image_root = root / "v1_images"
            media_dir = root / "media" / "va"
            slice_path = root / "slice.csv"
            refs_path = root / "image_refs.csv"
            report_path = bundle / "v1_reconciliation_report.csv"
            image_root.mkdir()
            Image.new("RGB", (2, 3), color=(0, 0, 0)).save(image_root / "marking.png")

            raw_fields = [
                "nRawStateDataID",
                "txtRawStateData",
                "txtTown",
                "txtPostmark",
                "txtTownPostmark",
                "txtDatesSeen",
                "txtColors",
                "nWidth",
                "nHeight",
                "txtSizes",
                "txtRatesText",
                "txtTownmarkShape",
                "txtTownmarkLettering",
                "txtTownmarkDateFormat",
                "ynManuscript",
                "ynManuscriptTownmarks",
                "txtOther",
                "memNotes",
                "txtTownmarkFraming",
            ]
            write_csv(
                slice_path,
                raw_fields,
                [{
                    "nRawStateDataID": "71",
                    "txtRawStateData": "RICHMOND (1850;Black) 10",
                    "txtTown": "Petersburg",
                    "txtPostmark": "PETERSBURG",
                    "txtTownPostmark": "",
                    "txtDatesSeen": "1850",
                    "txtColors": "Blue,Red",
                    "nWidth": "26",
                    "nHeight": "27",
                    "txtSizes": "",
                    "txtRatesText": "PAID/3[C]",
                    "txtTownmarkShape": "Circle",
                    "txtTownmarkLettering": "Italic",
                    "txtTownmarkDateFormat": "YD",
                    "ynManuscript": "0",
                    "ynManuscriptTownmarks": "",
                    "txtOther": "Stored other",
                    "memNotes": "Stored memo",
                    "txtTownmarkFraming": "Framed",
                }],
            )
            write_csv(
                refs_path,
                [
                    "source_row_id",
                    "townmark_image_id",
                    "source_filename",
                    "storage_filename",
                    "display_order",
                    "image_view",
                    "image_description",
                    "is_tracing",
                ],
                [{
                    "source_row_id": "71",
                    "townmark_image_id": "9",
                    "source_filename": "marking.png",
                    "storage_filename": "va/marking.png",
                    "display_order": "1",
                    "image_view": "FULL",
                    "image_description": "front",
                    "is_tracing": "False",
                }],
            )
            write_csv(bundle / "reference_works.csv", ["code"], [{"code": "ASCC1"}])
            write_csv(bundle / "regions.csv", ["code", "name"], [{"code": "USA-VA1", "name": "Virginia"}])
            write_csv(bundle / "post_offices.csv", ["name", "code", *AUDIT], [stamped({"name": "RICHMOND", "code": "USA-VA1-5"})])
            write_csv(bundle / "post_office_regions.csv", ["post_office", "region", *AUDIT], [stamped({"post_office": "USA-VA1-5", "region": "USA-VA1"})])
            write_csv(
                bundle / "colors.csv",
                ["name", "hex_val", "pantone_code", *AUDIT],
                [
                    stamped({"name": "BLACK", "hex_val": "#000000", "pantone_code": ""}),
                    stamped({"name": "BLUE", "hex_val": "#0000FF", "pantone_code": ""}),
                    stamped({"name": "RED", "hex_val": "#FF0000", "pantone_code": ""}),
                ],
            )
            write_csv(bundle / "letterings.csv", ["name", *AUDIT], [stamped({"name": "Italic"})])
            write_csv(bundle / "shapes.csv", ["name", "code", *AUDIT], [stamped({"name": "C - Circle", "code": "C"})])
            marking_fields = [
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
                *AUDIT,
            ]
            write_csv(
                bundle / "markings.csv",
                marking_fields,
                [
                    stamped({"code": "ASCC1-VA-M1100", "type": "TOWNMARK", "catalog_txt": "RICHMOND", "inscription_txt": "RICHMOND", "desc": "", "is_manuscript": "False", "shape": "", "lettering": "", "color": "BLACK", "is_irreg": "False", "width": "", "height": "", "date_fmt": "", "impression": "Normal", "rate_val": "", "post_office": "USA-VA1-5"}),
                    stamped({"code": "ASCC1-VA-M1101", "type": "RATEMARK", "catalog_txt": "RICHMOND", "inscription_txt": "PAID", "desc": "", "is_manuscript": "False", "shape": "C - Circle", "lettering": "", "color": "BLACK", "is_irreg": "False", "width": "", "height": "", "date_fmt": "", "impression": "Normal", "rate_val": "", "post_office": "USA-VA1-5"}),
                ],
            )
            write_csv(
                bundle / "marking_lineage.csv",
                ["v2_key", "source_listing_idx", "marking_code", "marking_type", "page", "chunk", "catalog_txt"],
                [
                    {"v2_key": "0:71", "source_listing_idx": "0", "marking_code": "ASCC1-VA-M1100", "marking_type": "TOWNMARK", "page": "0", "chunk": "71", "catalog_txt": "RICHMOND"},
                    {"v2_key": "0:71", "source_listing_idx": "0", "marking_code": "ASCC1-VA-M1101", "marking_type": "RATEMARK", "page": "0", "chunk": "71", "catalog_txt": "RICHMOND"},
                ],
            )
            write_csv(bundle / "dates_seen.csv", ["subject_type", "subject_id", "date", "granularity", *AUDIT], [])
            write_csv(bundle / "citations.csv", ["reference_work", "subject_type", "subject_id", "citation_detail", *AUDIT], [stamped({"reference_work": "ASCC1", "subject_type": "MARKING", "subject_id": "ASCC1-VA-M1100", "citation_detail": "0"})])
            write_csv(bundle / "images.csv", v1_bundle_overlay.IMAGE_COLUMNS, [])

            rc = v1_bundle_overlay.main([
                "--state", "VA",
                "--slice", str(slice_path),
                "--image-refs", str(refs_path),
                "--bundle-dir", str(bundle),
                "--v1-image-root", str(image_root),
                "--media-dir", str(media_dir),
                "--report", str(report_path),
            ])
            markings = read_csv(bundle / "markings.csv")
            dates = read_csv(bundle / "dates_seen.csv")
            citations = read_csv(bundle / "citations.csv")
            images = read_csv(bundle / "images.csv")
            report = read_csv(report_path)
            media_exists = (media_dir / "marking.png").exists()

        self.assertEqual(rc, 0)
        townmarks = [r for r in markings if r["type"] == "TOWNMARK"]
        ratemarks = [r for r in markings if r["type"] == "RATEMARK"]
        self.assertEqual(len(townmarks), 2)
        self.assertEqual({r["color"] for r in townmarks}, {"BLUE", "RED"})
        self.assertEqual({r["inscription_txt"] for r in townmarks}, {"PETERSBURG"})
        self.assertEqual({r["width"] for r in townmarks}, {"26"})
        self.assertEqual({r["height"] for r in townmarks}, {"27"})
        self.assertEqual({r["shape"] for r in townmarks}, {"C - Circle"})
        self.assertEqual({r["lettering"] for r in townmarks}, {"Italic"})
        self.assertEqual({r["date_fmt"] for r in townmarks}, {"YD"})
        self.assertEqual({r["rate_val"] for r in ratemarks}, {"3"})
        self.assertEqual(len(dates), 3)
        self.assertEqual({r["date"] for r in dates}, {"1850-01-01"})
        self.assertEqual({r["citation_detail"] for r in citations}, {""})
        self.assertEqual(len(images), 2)
        self.assertTrue(media_exists)
        self.assertIn("unsupported_column", {r["issue"] for r in report})

    def test_attach_images_maps_refs_to_townmark_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            image_root = root / "v1_images"
            media_dir = root / "media" / "va"
            refs_path = root / "image_refs.csv"
            report_path = bundle / "v1_image_report.csv"
            image_root.mkdir()
            Image.new("RGB", (4, 5), color=(255, 0, 0)).save(image_root / "marking.png")

            write_csv(
                refs_path,
                [
                    "source_row_id",
                    "townmark_image_id",
                    "source_filename",
                    "storage_filename",
                    "display_order",
                    "image_view",
                    "image_description",
                    "is_tracing",
                ],
                [{
                    "source_row_id": "71",
                    "townmark_image_id": "9",
                    "source_filename": "marking.png",
                    "storage_filename": "va/marking.png",
                    "display_order": "1",
                    "image_view": "FULL",
                    "image_description": "front",
                    "is_tracing": "False",
                }],
            )
            write_csv(
                bundle / "marking_lineage.csv",
                ["v2_key", "source_listing_idx", "marking_code", "marking_type", "page", "chunk", "catalog_txt"],
                [
                    {"v2_key": "0:71", "source_listing_idx": "0", "marking_code": "ASCC2-VA-M1100", "marking_type": "TOWNMARK", "page": "0", "chunk": "71", "catalog_txt": "RICHMOND"},
                    {"v2_key": "0:71", "source_listing_idx": "0", "marking_code": "ASCC2-VA-M1101", "marking_type": "TOWNMARK", "page": "0", "chunk": "71", "catalog_txt": "RICHMOND"},
                    {"v2_key": "0:71", "source_listing_idx": "0", "marking_code": "ASCC2-VA-M1102", "marking_type": "RATEMARK", "page": "0", "chunk": "71", "catalog_txt": "RICHMOND"},
                ],
            )
            write_csv(
                bundle / "markings.csv",
                ["code", "type", "is_manuscript", *AUDIT],
                [
                    stamped({"code": "ASCC2-VA-M1100", "type": "TOWNMARK", "is_manuscript": "False"}),
                    stamped({"code": "ASCC2-VA-M1101", "type": "TOWNMARK", "is_manuscript": "False"}),
                    stamped({"code": "ASCC2-VA-M1102", "type": "RATEMARK", "is_manuscript": "False"}),
                ],
            )
            write_csv(bundle / "images.csv", v1_attach_images.IMAGE_COLUMNS, [])

            rc = v1_attach_images.main([
                "--state", "VA",
                "--image-refs", str(refs_path),
                "--bundle-dir", str(bundle),
                "--v1-image-root", str(image_root),
                "--media-dir", str(media_dir),
                "--report", str(report_path),
            ])
            images = read_csv(bundle / "images.csv")
            report = read_csv(report_path)

        self.assertEqual(rc, 0)
        self.assertEqual([r["subject_id"] for r in images], ["ASCC2-VA-M1100", "ASCC2-VA-M1101"])
        self.assertEqual({r["image_width"] for r in images}, {"4"})
        self.assertEqual({r["image_height"] for r in images}, {"5"})
        self.assertEqual({r["storage_filename"] for r in images}, {"va/marking.png"})
        self.assertEqual(report, [])

    def test_overlay_preserve_images_keeps_existing_image_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            image_root = root / "missing-images"
            media_dir = root / "media" / "va"
            slice_path = root / "slice.csv"
            refs_path = root / "image_refs.csv"
            report_path = bundle / "v1_reconciliation_report.csv"

            write_csv(
                slice_path,
                ["nRawStateDataID", "txtRawStateData", "txtColors", "txtTownmarkFraming"],
                [{
                    "nRawStateDataID": "71",
                    "txtRawStateData": "RICHMOND (1850;Black) 10",
                    "txtColors": "Black,Blue",
                    "txtTownmarkFraming": "Framed",
                }],
            )
            write_csv(
                refs_path,
                [
                    "source_row_id",
                    "townmark_image_id",
                    "source_filename",
                    "storage_filename",
                    "display_order",
                    "image_view",
                    "image_description",
                    "is_tracing",
                ],
                [{
                    "source_row_id": "71",
                    "townmark_image_id": "9",
                    "source_filename": "missing.png",
                    "storage_filename": "va/missing.png",
                    "display_order": "1",
                    "image_view": "FULL",
                    "image_description": "",
                    "is_tracing": "False",
                }],
            )
            write_csv(bundle / "reference_works.csv", ["code"], [{"code": "ASCC2"}])
            write_csv(bundle / "regions.csv", ["code", "name"], [{"code": "USA-VA1", "name": "Virginia"}])
            write_csv(bundle / "post_offices.csv", ["name", "code", *AUDIT], [stamped({"name": "RICHMOND", "code": "USA-VA1-5"})])
            write_csv(bundle / "post_office_regions.csv", ["post_office", "region", *AUDIT], [stamped({"post_office": "USA-VA1-5", "region": "USA-VA1"})])
            write_csv(
                bundle / "colors.csv",
                ["name", "hex_val", "pantone_code", *AUDIT],
                [
                    stamped({"name": "BLACK", "hex_val": "#000000", "pantone_code": ""}),
                    stamped({"name": "BLUE", "hex_val": "#0000FF", "pantone_code": ""}),
                ],
            )
            write_csv(bundle / "letterings.csv", ["name", *AUDIT], [])
            write_csv(bundle / "shapes.csv", ["name", "code", *AUDIT], [])
            write_csv(
                bundle / "markings.csv",
                ["code", "type", "is_manuscript", "color", "post_office", *AUDIT],
                [stamped({"code": "ASCC2-VA-M1100", "type": "TOWNMARK", "is_manuscript": "False", "color": "BLACK", "post_office": "USA-VA1-5"})],
            )
            write_csv(
                bundle / "marking_lineage.csv",
                ["v2_key", "source_listing_idx", "marking_code", "marking_type", "page", "chunk", "catalog_txt"],
                [{"v2_key": "0:71", "source_listing_idx": "0", "marking_code": "ASCC2-VA-M1100", "marking_type": "TOWNMARK", "page": "0", "chunk": "71", "catalog_txt": "RICHMOND"}],
            )
            write_csv(bundle / "dates_seen.csv", ["subject_type", "subject_id", "date", "granularity", *AUDIT], [])
            write_csv(bundle / "citations.csv", ["reference_work", "subject_type", "subject_id", "citation_detail", *AUDIT], [])
            write_csv(
                bundle / "images.csv",
                v1_bundle_overlay.IMAGE_COLUMNS,
                [{
                    "subject_type": "MARKING",
                    "subject_id": "ASCC2-VA-M1100",
                    "original_filename": "kept.png",
                    "storage_filename": "va/kept.png",
                    "file_checksum": "abc",
                    "mime_type": "image/png",
                    "image_width": "4",
                    "image_height": "5",
                    "file_size_bytes": "6",
                    "image_view": "FULL",
                    "image_description": "",
                    "is_tracing": "False",
                    "display_order": "1",
                    "uploaded_by": "1",
                    **AUDIT,
                }],
            )
            write_csv(
                report_path,
                v1_bundle_overlay.REPORT_COLUMNS,
                [{"raw_id": "71", "issue": "missing_image_file", "detail": "missing.png"}],
            )

            rc = v1_bundle_overlay.main([
                "--state", "VA",
                "--slice", str(slice_path),
                "--image-refs", str(refs_path),
                "--bundle-dir", str(bundle),
                "--v1-image-root", str(image_root),
                "--media-dir", str(media_dir),
                "--report", str(report_path),
                "--preserve-images",
            ])
            images = read_csv(bundle / "images.csv")
            markings = read_csv(bundle / "markings.csv")
            report = read_csv(report_path)

        self.assertEqual(rc, 0)
        self.assertEqual({r["color"] for r in markings}, {"BLACK", "BLUE"})
        self.assertEqual({r["subject_id"] for r in images}, {"ASCC2-VA-M1100", "ASCC2-VA-M1100-C1"})
        self.assertEqual({r["storage_filename"] for r in images}, {"va/kept.png"})
        self.assertIn("missing_image_file", {r["issue"] for r in report})
        self.assertIn("unsupported_column", {r["issue"] for r in report})

    def test_overlay_repairs_inherited_manuscript_when_v1_false_has_no_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            slice_path = root / "slice.csv"
            refs_path = root / "image_refs.csv"
            report_path = bundle / "v1_reconciliation_report.csv"

            write_csv(
                slice_path,
                [
                    "nRawStateDataID",
                    "txtRawStateData",
                    "txtTownmarkShape",
                    "ynManuscript",
                    "ynManuscriptTownmarks",
                ],
                [{
                    "nRawStateDataID": "40",
                    "txtRawStateData": "(L)(Aug. 1776) 500",
                    "txtTownmarkShape": "",
                    "ynManuscript": "FALSE",
                    "ynManuscriptTownmarks": "FALSE",
                }],
            )
            write_csv(
                refs_path,
                [
                    "source_row_id",
                    "townmark_image_id",
                    "source_filename",
                    "storage_filename",
                    "display_order",
                    "image_view",
                    "image_description",
                    "is_tracing",
                ],
                [],
            )
            write_csv(bundle / "reference_works.csv", ["code"], [{"code": "ASCC2"}])
            write_csv(bundle / "regions.csv", ["code", "name"], [{"code": "USA-VA1", "name": "Virginia"}])
            write_csv(bundle / "post_offices.csv", ["name", "code", *AUDIT], [stamped({"name": "ALEXANDRIA", "code": "USA-VA1-5"})])
            write_csv(bundle / "post_office_regions.csv", ["post_office", "region", *AUDIT], [stamped({"post_office": "USA-VA1-5", "region": "USA-VA1"})])
            write_csv(bundle / "colors.csv", ["name", "hex_val", "pantone_code", *AUDIT], [stamped({"name": "BLACK", "hex_val": "#000000", "pantone_code": ""})])
            write_csv(bundle / "letterings.csv", ["name", *AUDIT], [])
            write_csv(bundle / "shapes.csv", ["name", "code", *AUDIT], [])
            write_csv(
                bundle / "markings.csv",
                ["code", "type", "catalog_txt", "inscription_txt", "desc", "is_manuscript", "shape", "lettering", "color", "is_irreg", "width", "height", "date_fmt", "impression", "rate_val", "post_office", *AUDIT],
                [stamped({
                    "code": "ASCC2-VA-M1042",
                    "type": "TOWNMARK",
                    "catalog_txt": "Alex=(Alexandria)(E)(July 19, 1776;Ms;Black) 500\n(L)(Aug. 1776) 500",
                    "inscription_txt": "(L)",
                    "desc": "",
                    "is_manuscript": "False",
                    "shape": "",
                    "lettering": "",
                    "color": "BLACK",
                    "is_irreg": "False",
                    "width": "",
                    "height": "",
                    "date_fmt": "",
                    "impression": "Normal",
                    "rate_val": "",
                    "post_office": "USA-VA1-5",
                })],
            )
            write_csv(
                bundle / "marking_lineage.csv",
                ["v2_key", "source_listing_idx", "marking_code", "marking_type", "page", "chunk", "catalog_txt"],
                [{"v2_key": "0:40", "source_listing_idx": "39", "marking_code": "ASCC2-VA-M1042", "marking_type": "TOWNMARK", "page": "0", "chunk": "40", "catalog_txt": "Alex=(Alexandria)"}],
            )
            write_csv(bundle / "dates_seen.csv", ["subject_type", "subject_id", "date", "granularity", *AUDIT], [])
            write_csv(bundle / "citations.csv", ["reference_work", "subject_type", "subject_id", "citation_detail", *AUDIT], [])
            write_csv(bundle / "images.csv", v1_bundle_overlay.IMAGE_COLUMNS, [])

            rc = v1_bundle_overlay.main([
                "--state", "VA",
                "--slice", str(slice_path),
                "--image-refs", str(refs_path),
                "--bundle-dir", str(bundle),
                "--v1-image-root", str(root / "images"),
                "--media-dir", str(root / "media" / "va"),
                "--report", str(report_path),
                "--preserve-images",
            ])
            markings = read_csv(bundle / "markings.csv")
            report = read_csv(report_path)

        self.assertEqual(rc, 0)
        self.assertEqual(markings[0]["is_manuscript"], "True")
        self.assertEqual(markings[0]["shape"], "")
        self.assertEqual(markings[0]["lettering"], "")
        self.assertEqual(markings[0]["is_irreg"], "")
        self.assertIn("manuscript_false_without_shape", {r["issue"] for r in report})


if __name__ == "__main__":
    unittest.main()
