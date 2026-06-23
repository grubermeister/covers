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

    def write_stage4_text_fields(self, paths, rows):
        state = paths["state"]
        write_csv(
            paths["compare_dir"] / f"v1_{state}_L1_text_interpreted.csv",
            stages.TEXT_FIELD_COLUMNS,
            rows,
        )

    def write_shape_seeds(self, paths):
        write_csv(
            paths["bundle_dir"] / "shapes.csv",
            ["id", "name", "code"],
            [
                {"id": "1", "name": "C - Circle", "code": "C"},
                {"id": "2", "name": "O - Oval", "code": "O"},
                {"id": "3", "name": "DC - Double Circle", "code": "DC"},
            ],
        )
        write_csv(
            paths["bundle_dir"] / "letterings.csv",
            ["id", "name"],
            [
                {"id": "1", "name": "Italic"},
                {"id": "2", "name": "Serif"},
                {"id": "3", "name": "Sans-serif"},
            ],
        )

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

        self.assertEqual(len(outs), 6)
        self.assertTrue(l0_exists)
        self.assertEqual(l1_row["txtTown"], "Richmond")

    def test_stage1_interprets_text_shape_separately_from_stored_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(
                paths["compare_dir"] / "v1_VA_slice.csv",
                ["nRawStateDataID", "txtRawStateData", "txtTownmarkShape"],
                [{"nRawStateDataID": "1", "txtRawStateData": "NEW MARTINSVILLE/W.V.(--;C--;PAID/3[C];Black) 40", "txtTownmarkShape": "Oval"}],
            )

            outs = stages.stage1_project(paths)
            text_row = read_csv(paths["compare_dir"] / "v1_VA_L1_text_interpreted.csv")[0]
            stored_row = read_csv(paths["compare_dir"] / "v1_VA_L1_parsed.csv")[0]

        self.assertEqual(len(outs), 6)
        self.assertEqual(text_row["shape"], "C")
        self.assertEqual(stored_row["txtTownmarkShape"], "Oval")

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

    def test_stage3_ignores_v2_meta_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(
                paths["compare_dir"] / "v1_VA_L0_edition.csv",
                stages.V2_COLUMNS,
                [
                    {"listing_text": "RICHMOND (1850;Black) 10.00", "catalog_page": "", "chunk_number": "1", "image_count": "1", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                ],
            )
            write_csv(
                paths["catalog_rows"],
                stages.V2_COLUMNS,
                [
                    {"listing_text": "VIRGINIA", "catalog_page": "1", "chunk_number": "1", "image_count": "0", "row_type": "META", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "RICHMOND (1850;Black) 10.00", "catalog_page": "1", "chunk_number": "2", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                ],
            )

            out = stages.stage3_align(paths)
            rows = read_csv(out)

        self.assertEqual([r["disposition"] for r in rows], ["matched"])
        self.assertEqual(rows[0]["v2_key"], "1:2")

    def test_v2_text_fields_ignore_meta_rows_before_assigning_keys(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            write_csv(
                paths["catalog_rows"],
                stages.V2_COLUMNS,
                [
                    {"listing_text": "VIRGINIA", "catalog_page": "1", "chunk_number": "1", "image_count": "0", "row_type": "META", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "RICHMOND (1850;Black) 10.00", "catalog_page": "1", "chunk_number": "2", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                ],
            )

            fields = stages._catalog_text_fields_by_key(paths["catalog_rows"])

        self.assertEqual(set(fields), {"1:2"})
        self.assertEqual(fields["1:2"]["post_office/town"], "RICHMOND")

    def test_stage3_family_aware_prevents_cross_family_child_match(self):
        # Two v1 families each have a root and a "Same(DUE/6[C]) 60" child.
        # Two v2 families mirror the structure.
        # Without family-aware alignment the identical "Same" text would cause
        # ALBANY's child to match BOSTON's child or vice versa.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            paths["bundle_dir"].mkdir(parents=True, exist_ok=True)
            write_csv(
                paths["compare_dir"] / "v1_VA_L0_edition.csv",
                stages.V2_COLUMNS,
                [
                    {"listing_text": "ALBANY/VA.(1860;30;Black) 50", "catalog_page": "", "chunk_number": "1", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "Same(DUE/6[C]) 60", "catalog_page": "", "chunk_number": "2", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "BOSTON/VA.(1861;28;Blue) 40", "catalog_page": "", "chunk_number": "3", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "Same(DUE/6[C]) 60", "catalog_page": "", "chunk_number": "4", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                ],
            )
            write_csv(
                paths["catalog_rows"],
                stages.V2_COLUMNS,
                [
                    {"listing_text": "ALBANY/VA.(1860;30;Black) 50", "catalog_page": "10", "chunk_number": "1", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "Same(DUE/6[C]) 60", "catalog_page": "10", "chunk_number": "1", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "BOSTON/VA.(1861;28;Blue) 40", "catalog_page": "10", "chunk_number": "2", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                    {"listing_text": "Same(DUE/6[C]) 60", "catalog_page": "10", "chunk_number": "2", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""},
                ],
            )
            # family_VA.csv as stage2 would produce it.
            write_csv(
                paths["compare_dir"] / "family_VA.csv",
                ["key", "detected_family_id", "detected_parent_key", "group_order", "resolved_town", "rel_type", "claimed_family_matches_detected"],
                [
                    {"key": "1", "detected_family_id": "1", "detected_parent_key": "", "group_order": "1", "resolved_town": "ALBANY", "rel_type": "", "claimed_family_matches_detected": "true"},
                    {"key": "2", "detected_family_id": "1", "detected_parent_key": "1", "group_order": "2", "resolved_town": "ALBANY", "rel_type": "Same", "claimed_family_matches_detected": "true"},
                    {"key": "3", "detected_family_id": "3", "detected_parent_key": "", "group_order": "1", "resolved_town": "BOSTON", "rel_type": "", "claimed_family_matches_detected": "true"},
                    {"key": "4", "detected_family_id": "3", "detected_parent_key": "3", "group_order": "2", "resolved_town": "BOSTON", "rel_type": "Same", "claimed_family_matches_detected": "true"},
                ],
            )

            out = stages.stage3_align(paths)
            rows = read_csv(out)

        by_v1 = {r["v1_key"]: r for r in rows if r["v1_key"]}
        # ALBANY's child (v1 key 2) must match within the ALBANY v2 family (10:1*)
        self.assertTrue(by_v1["2"]["v2_key"].startswith("10:1"), by_v1["2"]["v2_key"])
        # BOSTON's child (v1 key 4) must match within the BOSTON v2 family (10:2*)
        self.assertTrue(by_v1["4"]["v2_key"].startswith("10:2"), by_v1["4"]["v2_key"])

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
            self.write_stage4_text_fields(paths, [{"nRawStateDataID": "1", "post_office/town": "RICHMOND", "dates_seen": "1850", "colors": "BLACK", "width/height": "26 X 27", "rate_val": "3", "shape": "C", "shape_source": "paren_body"}])
            write_csv(paths["catalog_rows"], stages.V2_COLUMNS, [{"listing_text": "RICHMOND (1850;C-26x27;3;Black) 10.00", "catalog_page": "10", "chunk_number": "20", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""}])
            write_csv(paths["compare_dir"] / "v1_VA_L1_parsed.csv", ["nRawStateDataID", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText", "txtTownmarkShape"], [{"nRawStateDataID": "1", "txtTown": "Richmond", "txtDatesSeen": "1850", "txtColors": "1", "txtSizes": "", "width": "26", "height": "27", "txtValue": "", "txtRatesText": "3", "txtTownmarkShape": "Circle"}])
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "10:20", "disposition": "matched", "score": "1.000", "match_reason": "exact", "representative_v1_key": "1", "representative_v2_key": "10:20", "v1_duplicate_index": "1", "v2_duplicate_index": "1"}])
            write_csv(paths["bundle_dir"] / "post_offices.csv", ["id", "name"], [{"id": "5", "name": "RICHMOND"}])
            write_csv(paths["bundle_dir"] / "colors.csv", ["id", "name"], [{"id": "1", "name": "BLACK"}])
            self.write_shape_seeds(paths)
            write_csv(paths["bundle_dir"] / "markings.csv", ["id", "type", "post_office", "color", "width", "height", "rate_val", "shape"], [{"id": "100", "type": "TOWNMARK", "post_office": "5", "color": "1", "width": "26", "height": "27", "rate_val": "", "shape": "1"}, {"id": "101", "type": "RATEMARK", "post_office": "5", "color": "", "width": "", "height": "", "rate_val": "3", "shape": ""}])
            write_csv(paths["bundle_dir"] / "dates_seen.csv", ["subject_id", "date"], [{"subject_id": "100", "date": "1850"}])
            write_csv(paths["bundle_dir"] / "marking_lineage.csv", ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"], [{"v2_key": "10:20", "source_listing_idx": "0", "marking_id": "100", "marking_type": "TOWNMARK", "page": "10", "chunk": "20", "catalog_txt": ""}, {"v2_key": "10:20", "source_listing_idx": "0", "marking_id": "101", "marking_type": "RATEMARK", "page": "10", "chunk": "20", "catalog_txt": ""}])

            out = stages.stage4_fields(paths)
            rows = read_csv(out)

        verdicts = {(r["field"], r["verdict"]) for r in rows}
        self.assertIn(("rate_val", "agree"), verdicts)
        self.assertIn(("width/height", "agree"), verdicts)

    def test_stage4_normalizes_wv_style_field_equivalents(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(paths["compare_dir"] / "v1_VA_slice.csv", ["nRawStateDataID", "txtRawStateData"], [{"nRawStateDataID": "1", "txtRawStateData": "BEVERLY/WEST VA.(Aug. 25, 1864;30;Due 3[oval];Black) 100"}])
            self.write_stage4_text_fields(paths, [{"nRawStateDataID": "1", "post_office/town": "BEVERLY", "dates_seen": "1864", "colors": "BLACK", "width/height": "30", "rate_val": "3", "shape": "", "shape_source": "catalog_fallback"}])
            write_csv(paths["catalog_rows"], stages.V2_COLUMNS, [{"listing_text": "BEVERLY/WEST VA.(Aug. 25, 1864;30;Due 3[oval];Black) 100", "catalog_page": "10", "chunk_number": "20", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""}])
            write_csv(paths["compare_dir"] / "v1_VA_L1_parsed.csv", ["nRawStateDataID", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText", "txtTownmarkShape"], [{"nRawStateDataID": "1", "txtTown": "Beverly", "txtDatesSeen": "Aug. 25, 1864", "txtColors": "Black", "txtSizes": "30", "width": "", "height": "", "txtValue": "100", "txtRatesText": "Due 3[oval]", "txtTownmarkShape": ""}])
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "10:20", "disposition": "matched", "score": "0.972", "match_reason": "fuzzy", "representative_v1_key": "1", "representative_v2_key": "10:20", "v1_duplicate_index": "1", "v2_duplicate_index": "1"}])
            write_csv(paths["bundle_dir"] / "post_offices.csv", ["id", "name"], [{"id": "5", "name": "BEVERLY"}])
            write_csv(paths["bundle_dir"] / "colors.csv", ["id", "name"], [{"id": "1", "name": "BLACK"}])
            self.write_shape_seeds(paths)
            write_csv(paths["bundle_dir"] / "markings.csv", ["id", "type", "post_office", "color", "width", "height", "rate_val", "shape"], [{"id": "100", "type": "TOWNMARK", "post_office": "5", "color": "1", "width": "30.0", "height": "30.0", "rate_val": "", "shape": ""}, {"id": "101", "type": "RATEMARK", "post_office": "5", "color": "", "width": "", "height": "", "rate_val": "3.0", "shape": ""}])
            write_csv(paths["bundle_dir"] / "dates_seen.csv", ["subject_id", "date"], [{"subject_id": "100", "date": "1864-01-01"}])
            write_csv(paths["bundle_dir"] / "marking_lineage.csv", ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"], [{"v2_key": "10:20", "source_listing_idx": "0", "marking_id": "100", "marking_type": "TOWNMARK", "page": "10", "chunk": "20", "catalog_txt": ""}, {"v2_key": "10:20", "source_listing_idx": "0", "marking_id": "101", "marking_type": "RATEMARK", "page": "10", "chunk": "20", "catalog_txt": ""}])

            out = stages.stage4_fields(paths)
            rows = read_csv(out)

        self.assertTrue(rows)
        self.assertEqual({r["verdict"] for r in rows}, {"agree"})

    def test_stage4_does_not_flag_inherited_same_row_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            write_csv(paths["compare_dir"] / "v1_VA_slice.csv", ["nRawStateDataID", "txtRawStateData"], [{"nRawStateDataID": "1", "txtRawStateData": "Same(DUE/6[C]) 60"}])
            self.write_stage4_text_fields(paths, [{"nRawStateDataID": "1", "post_office/town": "", "dates_seen": "", "colors": "", "width/height": "", "rate_val": "6", "shape": "", "shape_source": ""}])
            write_csv(paths["catalog_rows"], stages.V2_COLUMNS, [{"listing_text": "Same(DUE/6[C]) 60", "catalog_page": "10", "chunk_number": "21", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""}])
            write_csv(paths["compare_dir"] / "v1_VA_L1_parsed.csv", ["nRawStateDataID", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText", "txtTownmarkShape"], [{"nRawStateDataID": "1", "txtTown": "", "txtDatesSeen": "", "txtColors": "", "txtSizes": "", "width": "", "height": "", "txtValue": "60", "txtRatesText": "DUE/6[C]", "txtTownmarkShape": ""}])
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "10:21", "disposition": "matched", "score": "1.000", "match_reason": "exact", "representative_v1_key": "1", "representative_v2_key": "10:21", "v1_duplicate_index": "1", "v2_duplicate_index": "1"}])
            write_csv(paths["bundle_dir"] / "post_offices.csv", ["id", "name"], [{"id": "5", "name": "MARTINSBURG"}])
            write_csv(paths["bundle_dir"] / "colors.csv", ["id", "name"], [{"id": "1", "name": "BLUE"}])
            self.write_shape_seeds(paths)
            write_csv(paths["bundle_dir"] / "markings.csv", ["id", "type", "post_office", "color", "width", "height", "rate_val", "shape"], [{"id": "100", "type": "TOWNMARK", "post_office": "5", "color": "1", "width": "26.0", "height": "26.0", "rate_val": "", "shape": ""}, {"id": "101", "type": "RATEMARK", "post_office": "5", "color": "", "width": "", "height": "", "rate_val": "6.0", "shape": ""}])
            write_csv(paths["bundle_dir"] / "dates_seen.csv", ["subject_id", "date"], [{"subject_id": "100", "date": "1864-01-01"}])
            write_csv(paths["bundle_dir"] / "marking_lineage.csv", ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"], [{"v2_key": "10:21", "source_listing_idx": "0", "marking_id": "100", "marking_type": "TOWNMARK", "page": "10", "chunk": "21", "catalog_txt": ""}, {"v2_key": "10:21", "source_listing_idx": "0", "marking_id": "101", "marking_type": "RATEMARK", "page": "10", "chunk": "21", "catalog_txt": ""}])

            out = stages.stage4_fields(paths)
            rows = read_csv(out)

        self.assertTrue(rows)
        self.assertEqual({r["verdict"] for r in rows}, {"agree"})

    def test_stage4_flags_stored_shape_against_text_shape_layer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            self.write_stage4_text_fields(paths, [{"nRawStateDataID": "1", "post_office/town": "NEW MARTINSVILLE", "dates_seen": "", "colors": "BLACK", "width/height": "", "rate_val": "3", "shape": "C", "shape_source": "paren_body"}])
            write_csv(paths["catalog_rows"], stages.V2_COLUMNS, [{"listing_text": "NEW MARTINSVILLE/W.V.(--;C--;PAID/3[C];Black) 40", "catalog_page": "10", "chunk_number": "22", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""}])
            write_csv(paths["compare_dir"] / "v1_VA_slice.csv", ["nRawStateDataID", "txtRawStateData"], [{"nRawStateDataID": "1", "txtRawStateData": "NEW MARTINSVILLE/W.V.(--;C--;PAID/3[C];Black) 40"}])
            write_csv(paths["compare_dir"] / "v1_VA_L1_parsed.csv", ["nRawStateDataID", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText", "txtTownmarkShape"], [{"nRawStateDataID": "1", "txtTown": "New Martinsville", "txtDatesSeen": "", "txtColors": "Black", "txtSizes": "", "width": "", "height": "", "txtValue": "40", "txtRatesText": "PAID/3[C]", "txtTownmarkShape": "Oval"}])
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "10:22", "disposition": "matched", "score": "1.000", "match_reason": "exact", "representative_v1_key": "1", "representative_v2_key": "10:22", "v1_duplicate_index": "1", "v2_duplicate_index": "1"}])
            write_csv(paths["bundle_dir"] / "post_offices.csv", ["id", "name"], [{"id": "5", "name": "NEW MARTINSVILLE"}])
            write_csv(paths["bundle_dir"] / "colors.csv", ["id", "name"], [{"id": "1", "name": "BLACK"}])
            self.write_shape_seeds(paths)
            write_csv(paths["bundle_dir"] / "markings.csv", ["id", "type", "post_office", "color", "width", "height", "rate_val", "shape"], [{"id": "100", "type": "TOWNMARK", "post_office": "5", "color": "1", "width": "", "height": "", "rate_val": "", "shape": "1"}, {"id": "101", "type": "RATEMARK", "post_office": "5", "color": "", "width": "", "height": "", "rate_val": "3", "shape": ""}])
            write_csv(paths["bundle_dir"] / "dates_seen.csv", ["subject_id", "date"], [])
            write_csv(paths["bundle_dir"] / "marking_lineage.csv", ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"], [{"v2_key": "10:22", "source_listing_idx": "0", "marking_id": "100", "marking_type": "TOWNMARK", "page": "10", "chunk": "22", "catalog_txt": ""}, {"v2_key": "10:22", "source_listing_idx": "0", "marking_id": "101", "marking_type": "RATEMARK", "page": "10", "chunk": "22", "catalog_txt": ""}])

            out = stages.stage4_fields(paths)
            rows = read_csv(out)
            reasons = stages._field_reasons(out)

        shape_row = [r for r in rows if r["field"] == "shape"][0]
        self.assertEqual(shape_row["v1_catalog_value"], "C")
        self.assertEqual(shape_row["v1_user_value"], "O")
        self.assertEqual(shape_row["user_vs_catalog_verdict"], "differ")
        self.assertEqual(shape_row["catalog_vs_v2_verdict"], "agree")
        self.assertIn("S4:user_catalog_shape_differ", reasons["1"])

    def test_stage4_checks_lettering_datefmt_manuscript_and_description(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            self.write_stage4_text_fields(
                paths,
                [{
                    "nRawStateDataID": "1",
                    "post_office/town": "RICHMOND",
                    "dates_seen": "1862",
                    "colors": "BLACK",
                    "width/height": "30",
                    "rate_val": "3",
                    "shape": "C",
                    "lettering": "ITALIC",
                    "date_fmt": "YD",
                    "is_manuscript": "FALSE",
                    "description": "Soldiers mail",
                    "shape_source": "paren_body",
                }],
            )
            write_csv(paths["catalog_rows"], stages.V2_COLUMNS, [{"listing_text": "RICHMOND (1862;C-30,YD;PAID/3[italic];Black) Soldiers mail 40", "catalog_page": "10", "chunk_number": "30", "image_count": "0", "row_type": "LISTING", "is_manuscript": "", "default_shape": "", "institutional_owner": ""}])
            write_csv(paths["compare_dir"] / "v1_VA_slice.csv", ["nRawStateDataID", "txtRawStateData"], [{"nRawStateDataID": "1", "txtRawStateData": "RICHMOND (1862;C-30,YD;PAID/3[italic];Black) Soldiers mail 40"}])
            write_csv(
                paths["compare_dir"] / "v1_VA_L1_parsed.csv",
                ["nRawStateDataID", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText", "txtTownmarkShape", "txtTownmarkLettering", "txtTownmarkDateFormat", "ynManuscript", "ynManuscriptTownmarks", "txtOther", "memNotes"],
                [{"nRawStateDataID": "1", "txtTown": "Richmond", "txtDatesSeen": "1862", "txtColors": "Black", "txtSizes": "C-30,YD", "width": "30", "height": "", "txtValue": "40", "txtRatesText": "PAID/3[italic]", "txtTownmarkShape": "Circle", "txtTownmarkLettering": "Italic", "txtTownmarkDateFormat": "YD", "ynManuscript": "TRUE", "ynManuscriptTownmarks": "", "txtOther": "Stored other", "memNotes": "Stored memo"}],
            )
            write_csv(paths["compare_dir"] / "align_VA.csv", ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"], [{"v1_key": "1", "v2_key": "10:30", "disposition": "matched", "score": "1.000", "match_reason": "exact", "representative_v1_key": "1", "representative_v2_key": "10:30", "v1_duplicate_index": "1", "v2_duplicate_index": "1"}])
            write_csv(paths["bundle_dir"] / "post_offices.csv", ["id", "name"], [{"id": "5", "name": "RICHMOND"}])
            write_csv(paths["bundle_dir"] / "colors.csv", ["id", "name"], [{"id": "1", "name": "BLACK"}])
            self.write_shape_seeds(paths)
            write_csv(
                paths["bundle_dir"] / "markings.csv",
                ["id", "type", "post_office", "color", "width", "height", "rate_val", "shape", "lettering", "date_fmt", "is_manuscript", "desc"],
                [
                    {"id": "100", "type": "TOWNMARK", "post_office": "5", "color": "1", "width": "30", "height": "30", "rate_val": "", "shape": "1", "lettering": "", "date_fmt": "YD", "is_manuscript": "False", "desc": "Soldiers mail"},
                    {"id": "101", "type": "RATEMARK", "post_office": "5", "color": "", "width": "", "height": "", "rate_val": "3", "shape": "", "lettering": "1", "date_fmt": "", "is_manuscript": "False", "desc": ""},
                ],
            )
            write_csv(paths["bundle_dir"] / "dates_seen.csv", ["subject_id", "date"], [{"subject_id": "100", "date": "1862"}])
            write_csv(paths["bundle_dir"] / "marking_lineage.csv", ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"], [{"v2_key": "10:30", "source_listing_idx": "0", "marking_id": "100", "marking_type": "TOWNMARK", "page": "10", "chunk": "30", "catalog_txt": ""}, {"v2_key": "10:30", "source_listing_idx": "0", "marking_id": "101", "marking_type": "RATEMARK", "page": "10", "chunk": "30", "catalog_txt": ""}])

            out = stages.stage4_fields(paths)
            rows = {r["field"]: r for r in read_csv(out)}
            reasons = stages._field_reasons(out)

        self.assertEqual(rows["lettering"]["catalog_vs_v2_verdict"], "agree")
        self.assertEqual(rows["date_fmt"]["catalog_vs_v2_verdict"], "agree")
        self.assertEqual(rows["is_manuscript"]["user_vs_catalog_verdict"], "differ")
        self.assertEqual(rows["description"]["v1_user_value"], "Soldiers mail\nStored other\nStored memo")
        self.assertEqual(rows["description"]["user_vs_catalog_verdict"], "differ")
        self.assertIn("S4:user_catalog_is_manuscript_differ", reasons["1"])
        self.assertIn("S4:user_catalog_description_differ", reasons["1"])

    def test_stage4_ignores_non_enum_date_format_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = self.make_paths(root)
            paths["compare_dir"].mkdir(parents=True)
            self.write_stage4_text_fields(
                paths,
                [{
                    "nRawStateDataID": "1",
                    "post_office/town": "PETERSBURG",
                    "dates_seen": "1778",
                    "colors": "BLACK",
                    "width/height": "",
                    "rate_val": "",
                    "shape": "",
                    "lettering": "",
                    "date_fmt": "",
                    "is_manuscript": "TRUE",
                    "description": "",
                    "shape_source": "",
                }],
            )
            write_csv(
                paths["catalog_rows"],
                stages.V2_COLUMNS,
                [{
                    "listing_text": "PETERSBURG(Sept. 21, 1778;Ms;Black) 500",
                    "catalog_page": "10",
                    "chunk_number": "31",
                    "image_count": "0",
                    "row_type": "LISTING",
                    "is_manuscript": "",
                    "default_shape": "",
                    "institutional_owner": "",
                }],
            )
            write_csv(
                paths["compare_dir"] / "v1_VA_slice.csv",
                ["nRawStateDataID", "txtRawStateData"],
                [{
                    "nRawStateDataID": "1",
                    "txtRawStateData": "PETERSBURG(Sept. 21, 1778;Ms;Black) 500",
                }],
            )
            write_csv(
                paths["compare_dir"] / "v1_VA_L1_parsed.csv",
                ["nRawStateDataID", "txtTown", "txtDatesSeen", "txtColors", "txtSizes", "width", "height", "txtValue", "txtRatesText", "txtTownmarkShape", "txtTownmarkLettering", "txtTownmarkDateFormat", "ynManuscript", "ynManuscriptTownmarks", "txtOther", "memNotes"],
                [{
                    "nRawStateDataID": "1",
                    "txtTown": "Petersburg",
                    "txtDatesSeen": "Sept. 21, 1778",
                    "txtColors": "Black",
                    "txtSizes": "",
                    "width": "",
                    "height": "",
                    "txtValue": "500",
                    "txtRatesText": "",
                    "txtTownmarkShape": "",
                    "txtTownmarkLettering": "",
                    "txtTownmarkDateFormat": "Manuscript",
                    "ynManuscript": "TRUE",
                    "ynManuscriptTownmarks": "",
                    "txtOther": "",
                    "memNotes": "",
                }],
            )
            write_csv(
                paths["compare_dir"] / "align_VA.csv",
                ["v1_key", "v2_key", "disposition", "score", "match_reason", "representative_v1_key", "representative_v2_key", "v1_duplicate_index", "v2_duplicate_index"],
                [{
                    "v1_key": "1",
                    "v2_key": "10:31",
                    "disposition": "matched",
                    "score": "1.000",
                    "match_reason": "exact",
                    "representative_v1_key": "1",
                    "representative_v2_key": "10:31",
                    "v1_duplicate_index": "1",
                    "v2_duplicate_index": "1",
                }],
            )
            write_csv(
                paths["bundle_dir"] / "post_offices.csv",
                ["id", "name"],
                [{"id": "5", "name": "PETERSBURG"}],
            )
            write_csv(
                paths["bundle_dir"] / "colors.csv",
                ["id", "name"],
                [{"id": "1", "name": "BLACK"}],
            )
            self.write_shape_seeds(paths)
            write_csv(
                paths["bundle_dir"] / "markings.csv",
                ["id", "type", "post_office", "color", "width", "height", "rate_val", "shape", "lettering", "date_fmt", "is_manuscript", "desc"],
                [{
                    "id": "100",
                    "type": "TOWNMARK",
                    "post_office": "5",
                    "color": "1",
                    "width": "",
                    "height": "",
                    "rate_val": "",
                    "shape": "",
                    "lettering": "",
                    "date_fmt": "",
                    "is_manuscript": "True",
                    "desc": "",
                }],
            )
            write_csv(
                paths["bundle_dir"] / "dates_seen.csv",
                ["subject_id", "date"],
                [{"subject_id": "100", "date": "1778-01-01"}],
            )
            write_csv(
                paths["bundle_dir"] / "marking_lineage.csv",
                ["v2_key", "source_listing_idx", "marking_id", "marking_type", "page", "chunk", "catalog_txt"],
                [{
                    "v2_key": "10:31",
                    "source_listing_idx": "0",
                    "marking_id": "100",
                    "marking_type": "TOWNMARK",
                    "page": "10",
                    "chunk": "31",
                    "catalog_txt": "",
                }],
            )

            out = stages.stage4_fields(paths)
            rows = {r["field"]: r for r in read_csv(out)}

        self.assertEqual(rows["date_fmt"]["v1_user_value"], "")
        self.assertEqual(rows["date_fmt"]["verdict"], "agree")
        self.assertEqual(rows["is_manuscript"]["v1_user_value"], "TRUE")

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
        self.assertEqual(row["listing_check"], "v1 only")
        self.assertEqual(row["field_issues"], "date missing from v2")
        self.assertEqual(row["main_review_issue"], "v1 only")
        self.assertEqual(row["review_issues"], "v1 only; date missing from v2")


if __name__ == "__main__":
    unittest.main()
