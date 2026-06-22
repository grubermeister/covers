"""Tests for the staged ASCC comparison harness.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_ascc_compare.py'

Expected exit code: 0.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from compare import stages


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


class AsccCompareTests(unittest.TestCase):
    def make_paths(self, root):
        compare_dir = root / "compare" / "VA"
        in_dir = root / "in"
        out_dir = root / "out"
        paths = {
            "state": "VA",
            "compare_dir": compare_dir,
            "manifest": compare_dir / "manifest.json",
            "states": in_dir / "tblStates.csv",
            "raw": in_dir / "tblRawStateData.csv",
            "images": in_dir / "tblTownmarkImages.csv",
            "catalog_rows": root / "catalog_rows.csv",
            "bundle_dir": out_dir,
        }
        write_csv(
            paths["states"],
            ["nStateID", "txtStateAbv"],
            [{"nStateID": "46", "txtStateAbv": "VA"}],
        )
        write_csv(
            paths["images"],
            ["nRawStateDataID", "ynDeleted", "nTownmarkImageID", "txtFilename", "nOrder", "txtView"],
            [{"nRawStateDataID": "1", "ynDeleted": "False", "nTownmarkImageID": "10", "txtFilename": "a.png", "nOrder": "1", "txtView": ""}],
        )
        return paths

    def test_stage0_filters_active_state_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            write_csv(
                paths["raw"],
                ["nRawStateDataID", "nStateID", "approve_status", "ynDeleted", "txtRawStateData"],
                [
                    {"nRawStateDataID": "1", "nStateID": "46", "approve_status": "Pending", "ynDeleted": "FALSE", "txtRawStateData": "RICHMOND (1850;Black) 10.00"},
                    {"nRawStateDataID": "2", "nStateID": "46", "approve_status": "Deleted", "ynDeleted": "FALSE", "txtRawStateData": "DROP (1850;Black) 10.00"},
                    {"nRawStateDataID": "3", "nStateID": "1", "approve_status": "Pending", "ynDeleted": "FALSE", "txtRawStateData": "OTHER (1850;Black) 10.00"},
                ],
            )

            out = stages.stage0_slice(paths)
            rows = read_csv(out)

        self.assertEqual([r["nRawStateDataID"] for r in rows], ["1"])
        self.assertEqual(rows[0]["images_count"], "1")

    def test_stage1_writes_layer_projection_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(
                paths["compare_dir"] / "v1_VA_slice.csv",
                ["nRawStateDataID", "txtRawStateData", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "txtValue", "txtRatesText", "txtTownmarkShape"],
                [{"nRawStateDataID": "1", "txtRawStateData": "RICHMOND  (1850;Black) 10.00", "txtTown": "Richmond", "txtDatesSeen": "1850", "txtColors": "Black", "txtSizes": "26", "txtValue": "10.00", "txtRatesText": "PAID 3", "txtTownmarkShape": "CDS"}],
            )

            outs = stages.stage1_project(paths)
            l0_exists = (paths["compare_dir"] / "v1_VA_L0_edition.csv").exists()
            l1_row = read_csv(paths["compare_dir"] / "v1_VA_L1_parsed.csv")[0]

        self.assertEqual(len(outs), 5)
        self.assertTrue(l0_exists)
        self.assertEqual(l1_row["txtTown"], "Richmond")

    def test_stage3_accounts_for_v1_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(
                paths["compare_dir"] / "v1_VA_L0_edition.csv",
                stages.V2_COLUMNS,
                [
                    {"listing_text": "RICHMOND (1850;Black) 10.00", "catalog_page": "", "chunk_number": "1", "image_count": "1", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "RICHMOND (1850;Black) 10.00", "catalog_page": "", "chunk_number": "2", "image_count": "1", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                ],
            )
            write_csv(
                paths["catalog_rows"],
                stages.V2_COLUMNS,
                [{"listing_text": "RICHMOND (1850;Black) 10.00", "catalog_page": "1", "chunk_number": "1", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""}],
            )

            out = stages.stage3_align(paths)
            rows = read_csv(out)

        self.assertEqual([r["disposition"] for r in rows], ["matched", "v1_duplicate"])
        self.assertEqual(rows[1]["representative_v1_key"], "1")

    def test_stage2_detects_same_child_family(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(
                paths["compare_dir"] / "v1_VA_slice.csv",
                ["nRawStateDataID", "nOrder", "txtRawStateData"],
                [
                    {"nRawStateDataID": "1", "nOrder": "1", "txtRawStateData": "RICHMOND (1850;Black) 10.00"},
                    {"nRawStateDataID": "2", "nOrder": "2", "txtRawStateData": "Same (1851;Blue) 12.00"},
                ],
            )
            write_csv(
                paths["compare_dir"] / "v1_VA_family_claimed.csv",
                ["nRawStateDataID", "nRawStateDataID_parent", "nGroupOrder"],
                [
                    {"nRawStateDataID": "1", "nRawStateDataID_parent": "", "nGroupOrder": "1"},
                    {"nRawStateDataID": "2", "nRawStateDataID_parent": "1", "nGroupOrder": "2"},
                ],
            )

            out = stages.stage2_family(paths)
            rows = read_csv(out)

        self.assertEqual(rows[1]["detected_family_id"], "1")
        self.assertEqual(rows[1]["detected_parent_key"], "1")

    def test_stage4_aggregates_townmark_and_ratemark(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(paths["compare_dir"] / "v1_VA_L1_parsed.csv", ["nRawStateDataID", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText"], [{"nRawStateDataID": "1", "txtTown": "Richmond", "txtDatesSeen": "1850", "txtColors": "1", "txtSizes": "", "width": "26", "height": "27", "txtValue": "", "txtRatesText": "3"}])
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "10:20", "disposition": "matched", "score": "1.000", "match_reason": "exact", "representative_v1_key": "1", "representative_v2_key": "10:20", "v1_duplicate_index": "1", "v2_duplicate_index": "1"}])
            write_csv(paths["bundle_dir"] / "post_offices.csv", ["id", "name"], [{"id": "5", "name": "RICHMOND"}])
            write_csv(paths["bundle_dir"] / "colors.csv", ["id", "name"], [{"id": "1", "name": "BLACK"}])
            write_csv(paths["bundle_dir"] / "markings.csv", ["id", "type", "post_office", "color", "width", "height", "rate_val"], [{"id": "100", "type": "TOWNMARK", "post_office": "5", "color": "1", "width": "26", "height": "27", "rate_val": ""}, {"id": "101", "type": "RATEMARK", "post_office": "5", "color": "", "width": "", "height": "", "rate_val": "3"}])
            write_csv(paths["bundle_dir"] / "dates_seen.csv", ["subject_id", "date"], [{"subject_id": "100", "date": "1850"}])
            write_csv(paths["bundle_dir"] / "marking_lineage.csv", ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"], [{"v2_key": "10:20", "source_listing_idx": "0", "marking_id": "100", "marking_type": "TOWNMARK", "page": "10", "chunk": "20", "catalog_txt": ""}, {"v2_key": "10:20", "source_listing_idx": "0", "marking_id": "101", "marking_type": "RATEMARK", "page": "10", "chunk": "20", "catalog_txt": ""}])

            out = stages.stage4_fields(paths)
            rows = read_csv(out)

        verdicts = {(r["field"], r["verdict"]) for r in rows}
        self.assertIn(("rate_val", "agree"), verdicts)
        self.assertIn(("width/height", "agree"), verdicts)

    def test_stage5_flags_orphaned_v1_images(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(paths["compare_dir"] / "family_VA.csv", ["key", "detected_family_id", "detected_parent_key", "group_order", "resolved_town", "rel_type", "claimed_family_matches_detected"], [{"key": "1", "detected_family_id": "1", "detected_parent_key": "", "group_order": "1", "resolved_town": "RICHMOND", "rel_type": "", "claimed_family_matches_detected": "true"}])
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "10:20", "disposition": "matched", "score": "1.000", "match_reason": "exact", "representative_v1_key": "1", "representative_v2_key": "10:20", "v1_duplicate_index": "1", "v2_duplicate_index": "1"}])
            write_csv(paths["compare_dir"] / "v1_VA_slice.csv", ["nRawStateDataID", "txtRawStateData", "images_count"], [{"nRawStateDataID": "1", "txtRawStateData": "RICHMOND (1850;Black) 10.00", "images_count": "1"}])
            write_csv(paths["catalog_rows"], stages.V2_COLUMNS, [{"listing_text": "RICHMOND (1850;Black) 10.00", "catalog_page": "10", "chunk_number": "20", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""}])
            write_csv(paths["bundle_dir"] / "marking_lineage.csv", ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"], [])
            write_csv(paths["bundle_dir"] / "images.csv", ["subject_id"], [])

            out = stages.stage5_preservation(paths)
            row = read_csv(out)[0]

        self.assertEqual(row["image_status"], "S5:orphaned_images")

    def test_stage6_prioritizes_s3_review_reason(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(paths["compare_dir"] / "v1_VA_slice.csv", ["nRawStateDataID", "txtRawStateData"], [{"nRawStateDataID": "1", "txtRawStateData": "A"}])
            write_csv(paths["catalog_rows"], stages.V2_COLUMNS, [])
            write_csv(paths["compare_dir"] / "family_VA.csv", ["key", "detected_family_id", "detected_parent_key", "group_order", "resolved_town", "rel_type", "claimed_family_matches_detected"], [{"key": "1", "detected_family_id": "1", "detected_parent_key": "", "group_order": "1", "resolved_town": "", "rel_type": "", "claimed_family_matches_detected": "true"}])
            write_csv(paths["compare_dir"] / "preservation_VA.csv", ["key", "family_ok", "family_note", "image_status"], [{"key": "1", "family_ok": "true", "family_note": "", "image_status": "no_v1_images"}])
            write_csv(paths["compare_dir"] / "fields_VA.csv", ["v1_key", "v2_key", "field", "v1_value", "v2_value", "verdict"], [{"v1_key": "1", "v2_key": "", "field": "dates_seen", "v1_value": "1850", "v2_value": "", "verdict": "v1_only"}])
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "", "disposition": "added", "score": "", "match_reason": "unmatched_v1", "representative_v1_key": "1", "representative_v2_key": "", "v1_duplicate_index": "1", "v2_duplicate_index": ""}])

            out = stages.stage6_ledger(paths)
            row = read_csv(out)[0]

        self.assertEqual(row["primary_review_reason"], "S3:added")
        self.assertEqual(row["review_reasons"], "S3:added;S4:dates_seen_v1_only")


if __name__ == "__main__":
    unittest.main()
