"""Tests for tools/merge_ascc_bundles.py.

Run from repo root:
    .venv/bin/python -m unittest discover -s tools -p 'test_merge_ascc_bundles.py'

Expected exit code: 0.
"""

import csv
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from merge_ascc_bundles import BundleSpec, MergeError, check_bundle, merge_bundles


AUDIT = {
    "created_date": "2026-01-01T00:00:00+00:00",
    "modified_date": "2026-01-01T00:00:00+00:00",
    "created_by": "1",
    "modified_by": "1",
}


class MergeAsccBundlesTests(unittest.TestCase):
    def test_merges_two_bundles_and_rewrites_foreign_keys(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            va = root / "va"
            nc = root / "nc"
            out = root / "out"
            make_bundle(va, "VA", "Virginia", "Richmond", "ASCC1-VA-1", "va/va-1.png")
            make_bundle(nc, "NC", "North Carolina", "Raleigh", "ASCC1-NC-1", "nc/nc-1.png")

            counts = merge_bundles(
                [BundleSpec("VA", va), BundleSpec("NC", nc)],
                out,
            )

            self.assertEqual(counts["markings"], 2)
            self.assertEqual(check_bundle(out), [])

            colors = read_rows(out / "colors.csv")
            regions = read_rows(out / "regions.csv")
            post_offices = read_rows(out / "post_offices.csv")
            markings = read_rows(out / "markings.csv")
            dates_seen = read_rows(out / "dates_seen.csv")
            citations = read_rows(out / "citations.csv")
            images = read_rows(out / "images.csv")

            self.assertEqual([row["name"] for row in colors], ["Black"])
            self.assertEqual([row["name"] for row in regions], [
                "United States of America",
                "Virginia",
                "North Carolina",
            ])
            self.assertEqual(regions[1]["parent_region"], "1")
            self.assertEqual(regions[2]["parent_region"], "1")
            self.assertEqual([row["name"] for row in post_offices], ["Richmond", "Raleigh"])
            self.assertEqual([row["id"] for row in markings], ["1", "2"])
            self.assertEqual([row["post_office"] for row in markings], ["1", "2"])
            self.assertEqual([row["subject_id"] for row in dates_seen], ["1", "2"])
            self.assertEqual([row["subject_id"] for row in citations], ["1", "2"])
            self.assertEqual([row["subject_id"] for row in images], ["1", "2"])

    def test_duplicate_marking_code_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            va = root / "va"
            nc = root / "nc"
            out = root / "out"
            make_bundle(va, "VA", "Virginia", "Richmond", "ASCC1-DUP-1", "va/va-1.png")
            make_bundle(nc, "NC", "North Carolina", "Raleigh", "ASCC1-DUP-1", "nc/nc-1.png")

            with self.assertRaisesRegex(MergeError, "Duplicate Marking.code"):
                merge_bundles(
                    [BundleSpec("VA", va), BundleSpec("NC", nc)],
                    out,
                )

    def test_conflicting_seed_row_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            va = root / "va"
            nc = root / "nc"
            out = root / "out"
            make_bundle(va, "VA", "Virginia", "Richmond", "ASCC1-VA-1", "va/va-1.png")
            make_bundle(nc, "NC", "North Carolina", "Raleigh", "ASCC1-NC-1", "nc/nc-1.png")
            rows = read_rows(nc / "colors.csv")
            rows[0]["hex_val"] = "#111111"
            write_csv(nc / "colors.csv", list(rows[0]), rows)

            with self.assertRaisesRegex(MergeError, "duplicate name 'Black' conflicts"):
                merge_bundles(
                    [BundleSpec("VA", va), BundleSpec("NC", nc)],
                    out,
                )

    def test_duplicate_storage_filename_across_bundles_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            va = root / "va"
            nc = root / "nc"
            out = root / "out"
            make_bundle(va, "VA", "Virginia", "Richmond", "ASCC1-VA-1", "shared/1.png")
            make_bundle(nc, "NC", "North Carolina", "Raleigh", "ASCC1-NC-1", "shared/1.png")

            with self.assertRaisesRegex(MergeError, "Image storage_filename crosses bundles"):
                merge_bundles(
                    [BundleSpec("VA", va), BundleSpec("NC", nc)],
                    out,
                )

    def test_check_bundle_rejects_storage_filename_checksum_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            make_bundle(
                bundle,
                "VA",
                "Virginia",
                "Richmond",
                "ASCC1-VA-1",
                "va/va-1.png",
            )
            rows = read_rows(bundle / "images.csv")
            duplicate = dict(rows[0])
            duplicate["image_id"] = "2"
            duplicate["file_checksum"] = "def456"
            rows.append(duplicate)
            write_csv(bundle / "images.csv", list(rows[0]), rows)

            errors = check_bundle(bundle)

            self.assertTrue(
                any(
                    "storage_filename va/va-1.png has conflicting file_checksum"
                    in error
                    for error in errors
                )
            )

    def test_check_bundle_rejects_duplicate_seed_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            make_bundle(
                bundle,
                "VA",
                "Virginia",
                "Richmond",
                "ASCC1-VA-1",
                "va/va-1.png",
            )
            rows = read_rows(bundle / "colors.csv")
            duplicate = dict(rows[0])
            duplicate["id"] = "2"
            rows.append(duplicate)
            write_csv(bundle / "colors.csv", list(rows[0]), rows)

            errors = check_bundle(bundle)

            self.assertIn("colors: duplicate name Black", errors)

    def test_check_bundle_allows_storage_filename_fanout_same_checksum(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            make_bundle(
                bundle,
                "VA",
                "Virginia",
                "Richmond",
                "ASCC1-VA-1",
                "va/va-1.png",
            )
            rows = read_rows(bundle / "images.csv")
            duplicate = dict(rows[0])
            duplicate["image_id"] = "2"
            rows.append(duplicate)
            write_csv(bundle / "images.csv", list(rows[0]), rows)

            self.assertEqual(check_bundle(bundle), [])


def make_bundle(path, abbrev, region_name, post_office, marking_code, storage_filename):
    path.mkdir(parents=True)
    write_csv(path / "colors.csv", ["id", "name", "hex_val", "pantone_code"] + list(AUDIT), [
        row({"id": "1", "name": "Black", "hex_val": "#000000", "pantone_code": ""}),
    ])
    write_csv(path / "letterings.csv", ["id", "name"] + list(AUDIT), [
        row({"id": "1", "name": "Serif"}),
    ])
    write_csv(path / "shapes.csv", ["id", "name", "code"] + list(AUDIT), [
        row({"id": "1", "name": "SL - Straight Line", "code": ""}),
    ])
    write_csv(path / "regions.csv", [
        "id",
        "created_date",
        "modified_date",
        "created_by",
        "modified_by",
        "name",
        "abbrev",
        "region_tier",
        "parent_region",
        "established_date",
        "defunct_date",
    ], [
        row({
            "id": "1",
            "name": "United States of America",
            "abbrev": "USA",
            "region_tier": "COUNTRY",
            "parent_region": "",
            "established_date": "1776-07-04",
            "defunct_date": "",
        }),
        row({
            "id": "2",
            "name": region_name,
            "abbrev": abbrev,
            "region_tier": "STATE",
            "parent_region": "1",
            "established_date": "1788-01-01",
            "defunct_date": "",
        }),
    ])
    write_csv(path / "reference_works.csv", [
        "id",
        "created_date",
        "modified_date",
        "created_by",
        "modified_by",
        "code",
        "title",
        "authorship",
        "publisher",
        "publication_year",
        "edition",
        "volume",
        "isbn",
        "url",
    ], [
        row({
            "id": "1",
            "code": "ASCC1",
            "title": "American Stampless Cover Catalog",
            "authorship": "Author",
            "publisher": "Publisher",
            "publication_year": "1997",
            "edition": "5",
            "volume": "I",
            "isbn": "",
            "url": "",
        }),
    ])
    write_csv(path / "post_offices.csv", ["id", "name"] + list(AUDIT), [
        row({"id": "1", "name": post_office}),
    ])
    write_csv(path / "post_office_regions.csv", ["id", "post_office", "region"] + list(AUDIT), [
        row({"id": "1", "post_office": "1", "region": "2"}),
    ])
    write_csv(path / "markings.csv", [
        "id",
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
    ] + list(AUDIT), [
        row({
            "id": "1",
            "code": marking_code,
            "type": "TOWNMARK",
            "catalog_txt": post_office,
            "inscription_txt": post_office.upper(),
            "desc": "",
            "is_manuscript": "False",
            "shape": "1",
            "lettering": "",
            "color": "1",
            "is_irreg": "False",
            "width": "20.00",
            "height": "3.00",
            "date_fmt": "",
            "impression": "Normal",
            "rate_val": "",
            "post_office": "1",
        }),
    ])
    write_csv(path / "dates_seen.csv", ["id", "subject_type", "subject_id", "date", "granularity"] + list(AUDIT), [
        row({"id": "1", "subject_type": "MARKING", "subject_id": "1", "date": "1850-01-01", "granularity": "DAY"}),
    ])
    write_csv(path / "citations.csv", [
        "id",
        "reference_work",
        "subject_type",
        "subject_id",
        "citation_detail",
    ] + list(AUDIT), [
        row({"id": "1", "reference_work": "1", "subject_type": "MARKING", "subject_id": "1", "citation_detail": "1"}),
    ])
    write_csv(path / "images.csv", [
        "image_id",
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
    ] + list(AUDIT), [
        row({
            "image_id": "1",
            "subject_type": "MARKING",
            "subject_id": "1",
            "original_filename": storage_filename.rsplit("/", 1)[-1],
            "storage_filename": storage_filename,
            "file_checksum": "abc123",
            "mime_type": "image/png",
            "image_width": "20",
            "image_height": "10",
            "file_size_bytes": "100",
            "image_view": "FULL",
            "image_description": "",
            "is_tracing": "True",
            "display_order": "1",
            "uploaded_by": "1",
        }),
    ])


def row(values):
    out = dict(AUDIT)
    out.update(values)
    return out


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


if __name__ == "__main__":
    unittest.main()
