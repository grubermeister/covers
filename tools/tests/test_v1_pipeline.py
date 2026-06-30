"""Tests for the v1-only ASCC pipeline helpers.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_v1_pipeline.py'

Expected exit code: 0.
"""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import v1_bundle_overlay
import v1_attach_images
import v1_catalog_rows
import ascc_data_munger
from munger.relationships import roll_up_catalog_text
from v1_synthetic_listing import synthetic_desc_lines, synthetic_listing


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


def write_munger_seeds(root):
    input_dir = root / "in"
    input_dir.mkdir()
    write_csv(
        input_dir / "reference_works.csv",
        [
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
        ],
        [{
            "id": "1",
            "created_date": AUDIT["created_date"],
            "modified_date": AUDIT["modified_date"],
            "created_by": "1",
            "modified_by": "1",
            "code": "ASCC6",
            "title": "American Stampless Cover Catalog",
            "authorship": "",
            "publisher": "",
            "publication_year": "2026",
            "edition": "6",
            "volume": "",
            "isbn": "",
            "url": "",
        }],
    )
    write_csv(
        input_dir / "regions.csv",
        [
            "id",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
            "code",
            "name",
            "abbrev",
            "region_tier",
            "parent_region",
            "established_date",
            "defunct_date",
        ],
        [{
            "id": "48",
            "created_date": AUDIT["created_date"],
            "modified_date": AUDIT["modified_date"],
            "created_by": "1",
            "modified_by": "1",
            "code": "USA-WV1",
            "name": "West Virginia",
            "abbrev": "WV",
            "region_tier": "STATE",
            "parent_region": "",
            "established_date": "1863-06-20",
            "defunct_date": "",
        }],
    )
    return input_dir


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
            ("Wyocena W.T.(E)", "", "Wyocena W.T."),
            ("(L)", "ALEXANDRIA", ""),
            ("Same(E)", "CABOTVILLE / Ms.", "CABOTVILLE / Ms."),
            ("The same(L)", "DETROIT / Mich.", "DETROIT / Mich."),
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

    def test_v1_overlay_preserves_multi_star_inscription_text(self):
        self.assertEqual(
            v1_bundle_overlay.strip_inscription_markers("ABINGDON/*VA.*"),
            "ABINGDON/*VA.*",
        )
        self.assertEqual(
            v1_bundle_overlay.strip_inscription_markers("*BETHANY/Va."),
            "BETHANY/Va.",
        )
        self.assertEqual(
            v1_bundle_overlay.strip_inscription_markers("BETHANY/Va.*"),
            "BETHANY/Va.",
        )

    def test_v1_overlay_same_uses_immediate_previous_row_text(self):
        carry_state = {}
        rows = [
            {
                "txtTown": "ABINGDON",
                "txtTownPostmark": "ABINGDON/*VA.*",
                "txtPostmark": "ABINGDON/*VA.*",
            },
            {
                "txtTown": "ABINGDON",
                "txtTownPostmark": "ABINGDON/*VA.*",
                "txtPostmark": "Same/VA.",
            },
            {
                "txtTown": "ABINGDON",
                "txtTownPostmark": "ABINGDON/*VA.*",
                "txtPostmark": "Same",
            },
        ]

        resolved = [
            v1_bundle_overlay.overlay_row_inscription(row, carry_state)
            for row in rows
        ]

        self.assertEqual(
            resolved,
            ["ABINGDON/*VA.*", "ABINGDON/VA.", "ABINGDON/VA."],
        )

    def test_v1_overlay_applies_multi_star_townmark_text(self):
        markings_by_id = {
            "M1": {
                "code": "ASCC6-VA-M1076",
                "type": "TOWNMARK",
                "inscription_txt": "ABINGDON/VA.",
                "post_office": "USA-VA1-1",
                "is_manuscript": "False",
            },
        }
        raw_row = {
            "txtTownPostmark": "ABINGDON/*VA.*",
            "txtPostmark": "",
            "txtTown": "",
            "txtTownmarkShape": "",
            "txtTownmarkLettering": "",
            "txtTownmarkDateFormat": "",
        }

        v1_bundle_overlay.apply_row_fields(
            "104",
            raw_row,
            markings_by_id,
            ["M1"],
            ["M1"],
            [],
            {"shapes": {}, "letterings": {}},
            {},
            [],
        )

        self.assertEqual(
            markings_by_id["M1"]["inscription_txt"],
            "ABINGDON/*VA.*",
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

    def test_blank_raw_rows_are_synthesized_and_image_refs_included(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            states = root / "tblStates.csv"
            raw = root / "tblRawStateData.csv"
            images = root / "tblTownmarkImages.csv"
            slice_out = root / "slice.csv"
            catalog_out = root / "catalog_rows.csv"
            refs_out = root / "image_refs.csv"
            raw_fields = [
                "nRawStateDataID",
                "nStateID",
                "approve_status",
                "ynDeleted",
                "txtRawStateData",
                "txtTown",
                "txtPostmark",
                "txtTownPostmark",
                "nEarliestUseDay",
                "txtEarliestUseMonth",
                "txtEarliestUseYear",
                "ynManuscript",
                "ynManuscriptTownmarks",
                "txtRates",
                "txtTownmarkColor",
            ]
            write_csv(states, ["nStateID", "txtStateAbv"], [{"nStateID": "48", "txtStateAbv": "WV"}])
            write_csv(
                raw,
                raw_fields,
                [
                    {
                        "nRawStateDataID": "10",
                        "nStateID": "48",
                        "approve_status": "Approved",
                        "ynDeleted": "FALSE",
                        "txtRawStateData": "",
                        "txtTown": "Falls Church",
                        "txtPostmark": "",
                        "txtTownPostmark": "Falls Church",
                        "nEarliestUseDay": "15",
                        "txtEarliestUseMonth": "3",
                        "txtEarliestUseYear": "1854",
                        "ynManuscript": "True",
                        "ynManuscriptTownmarks": "",
                        "txtRates": "Paid 3 [ms.]",
                        "txtTownmarkColor": "Black",
                    },
                    {
                        "nRawStateDataID": "11",
                        "nStateID": "48",
                        "approve_status": "Approved",
                        "ynDeleted": "FALSE",
                        "txtRawStateData": "RICHMOND (1850;Black) 10",
                        "txtTown": "Richmond",
                        "txtPostmark": "",
                        "txtTownPostmark": "Richmond",
                        "nEarliestUseDay": "",
                        "txtEarliestUseMonth": "",
                        "txtEarliestUseYear": "",
                        "ynManuscript": "",
                        "ynManuscriptTownmarks": "",
                        "txtRates": "",
                        "txtTownmarkColor": "",
                    },
                    {
                        "nRawStateDataID": "12",
                        "nStateID": "48",
                        "approve_status": "Approved",
                        "ynDeleted": "FALSE",
                        "txtRawStateData": "",
                        "txtTown": "",
                        "txtPostmark": "",
                        "txtTownPostmark": "",
                        "nEarliestUseDay": "",
                        "txtEarliestUseMonth": "",
                        "txtEarliestUseYear": "",
                        "ynManuscript": "",
                        "ynManuscriptTownmarks": "",
                        "txtRates": "Use on #U10 envelope",
                        "txtTownmarkColor": "Black",
                    },
                ],
            )
            write_csv(
                images,
                ["nRawStateDataID", "ynDeleted", "nTownmarkImageID", "txtFilename", "nOrder", "txtView"],
                [
                    {"nRawStateDataID": "10", "ynDeleted": "False", "nTownmarkImageID": "100", "txtFilename": "a.png", "nOrder": "1", "txtView": "front"},
                    {"nRawStateDataID": "12", "ynDeleted": "False", "nTownmarkImageID": "101", "txtFilename": "b.png", "nOrder": "1", "txtView": "front"},
                ],
            )

            rc = v1_catalog_rows.main([
                "WV",
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
        self.assertEqual([r["chunk_number"] for r in catalog_rows], ["10", "11"])
        self.assertEqual(
            catalog_rows[0]["listing_text"],
            "Falls Church(Mar. 15, 1854;Ms;Paid 3 [ms];BLACK) --",
        )
        self.assertEqual([r["source_row_id"] for r in refs], ["10"])

    def test_v1_manual_color_split_rollup_dedupes_catalog_text_only(self):
        listings = pd.DataFrame(
            [
                {
                    "source_listing_idx": 0,
                    "raw_id": "71",
                    "clean_text": "RICHMOND (1850;Black) 10",
                    "parent_idx": None,
                    "parsed_colors": ["BLACK"],
                },
                {
                    "source_listing_idx": 1,
                    "raw_id": "72",
                    "clean_text": " RICHMOND   (1850;Black)   10 ",
                    "parent_idx": 0,
                    "parsed_colors": ["RED"],
                },
            ]
        )
        lineage_rows = [
            {"chunk": "71", "marking_code": "ASCC1-VA-M1100"},
            {"chunk": "72", "marking_code": "ASCC1-VA-M1101"},
        ]

        rolled = roll_up_catalog_text(listings)

        self.assertEqual(len(rolled), 2)
        self.assertEqual(rolled.loc[0, "rolled_catalog_text"], "RICHMOND (1850;Black) 10")
        self.assertEqual(rolled.loc[1, "rolled_catalog_text"], "RICHMOND (1850;Black) 10")
        self.assertEqual(rolled["raw_id"].tolist(), ["71", "72"])
        self.assertEqual(rolled["parsed_colors"].tolist(), [["BLACK"], ["RED"]])
        self.assertEqual([row["chunk"] for row in lineage_rows], ["71", "72"])

    def test_v1_catalog_rows_drop_manual_color_split_duplicates(self):
        fields = [
            "nRawStateDataID",
            "txtRawStateData",
            "txtTown",
            "txtTownPostmark",
            "txtRates",
            "txtColors",
            "txtTownmarkColor",
            "dtUpdated",
            "nImageCount",
        ]
        base = {
            "txtRawStateData": "Same/Va.(1834-51;30;FREE,PAID;Black,Blue,Red) 20",
            "txtTown": "ABINGDON",
            "txtTownPostmark": "ABINGDON/Va.",
            "txtRates": "FREE,PAID,5,10",
            "txtColors": "Black,Blue,Red",
            "dtUpdated": "2024-01-25 05:51:45",
            "nImageCount": "0",
        }
        rows = [
            {
                **base,
                "nRawStateDataID": "108",
                "txtTownmarkColor": "Black",
            },
            {
                **base,
                "nRawStateDataID": "774",
                "txtTownmarkColor": "Blue",
                "dtUpdated": "2024-01-26 00:00:00",
            },
            {
                **base,
                "nRawStateDataID": "775",
                "txtTownmarkColor": "Red",
            },
            {
                **base,
                "nRawStateDataID": "776",
                "txtTownmarkColor": "Red",
                "txtRates": "PAID",
            },
            {
                **base,
                "nRawStateDataID": "777",
                "txtTownmarkColor": "Blue",
            },
        ]

        kept, dropped = v1_catalog_rows.dedupe_v1_color_split_rows(
            rows,
            fields,
            image_counts={"777": 1},
        )

        self.assertEqual([row["nRawStateDataID"] for row in kept], ["108", "776", "777"])
        self.assertEqual(dropped, ["774", "775"])

    def test_synthetic_listing_preserves_note_only_rate_text_as_desc(self):
        row = {
            "txtTownPostmark": "Locust Level",
            "nEarliestUseDay": "16",
            "txtEarliestUseMonth": "4",
            "txtEarliestUseYear": "1861",
            "txtRates": "Use on #U10 envelope",
            "txtTownmarkColor": "Black",
        }

        self.assertEqual(
            synthetic_listing(row),
            "Locust Level(Apr. 16, 1861;BLACK) --",
        )
        self.assertEqual(
            synthetic_desc_lines(row),
            ["Rate note: Use on #U10 envelope"],
        )

    def test_overlay_dedupes_decade_endpoint_dates(self):
        rows = v1_bundle_overlay.parsed_date_rows(
            "1857, 1859, 1850s",
            ["ASCC6-WV-M2283"],
            AUDIT,
        )
        observed = [
            (row["subject_id"], row["date"], row["granularity"])
            for row in rows
        ]

        self.assertEqual(
            observed,
            [
                ("ASCC6-WV-M2283", "1857-01-01", "YEAR"),
                ("ASCC6-WV-M2283", "1859-01-01", "YEAR"),
                ("ASCC6-WV-M2283", "1850-01-01", "YEAR"),
            ],
        )

    def test_overlay_parses_numeric_month_year_as_month(self):
        rows = v1_bundle_overlay.parsed_date_rows(
            "03 1852",
            ["ASCC6-WV-M2283"],
            AUDIT,
        )
        observed = [
            (row["subject_id"], row["date"], row["granularity"])
            for row in rows
        ]

        self.assertEqual(
            observed,
            [("ASCC6-WV-M2283", "1852-03-01", "MONTH")],
        )

    def test_overlay_skips_invalid_calendar_day(self):
        rows = v1_bundle_overlay.parsed_date_rows(
            "Sept. 31, 1813",
            ["ASCC6-MI-M1106"],
            AUDIT,
        )

        self.assertEqual(rows, [])

    def test_munger_handles_synthetic_rates_auxmarks_and_sentinel_dates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            source_rows = [
                {
                    "nRawStateDataID": "1",
                    "txtTownPostmark": "Falls Church",
                    "nEarliestUseDay": "15",
                    "txtEarliestUseMonth": "3",
                    "txtEarliestUseYear": "1854",
                    "ynManuscript": "True",
                    "txtRates": "Paid 3 [ms]",
                    "txtTownmarkColor": "Black",
                },
                {
                    "nRawStateDataID": "2",
                    "txtTownPostmark": "LEWISBURG/Va.",
                    "txtDatesSeen": "1700 - 1900",
                    "txtRates": "PAID",
                    "txtTownmarkShape": "Circle",
                    "nWidth": "31",
                    "txtTownmarkColor": "Red",
                },
                {
                    "nRawStateDataID": "3",
                    "txtTownPostmark": "Locust Level",
                    "nEarliestUseDay": "16",
                    "txtEarliestUseMonth": "4",
                    "txtEarliestUseYear": "1861",
                    "txtRates": "Use on #U10 envelope",
                    "txtTownmarkColor": "Black",
                },
                {
                    "nRawStateDataID": "4",
                    "txtTownPostmark": "Forks of Potomac",
                    "nEarliestUseDay": "18",
                    "txtEarliestUseMonth": "10",
                    "txtEarliestUseYear": "c1860",
                    "nLatestUseYear": "1860",
                    "txtLatestUseYear": "c1860",
                    "ynManuscript": "True",
                    "txtTownmarkColor": "Black",
                },
            ]
            catalog_rows = [
                {
                    "listing_text": synthetic_listing(row),
                    "catalog_page": "0",
                    "chunk_number": row["nRawStateDataID"],
                    "image_count": "0",
                    "row_type": "LISTING",
                    "is_manuscript": "",
                    "default_shape": "",
                    "institutional_owner": "",
                }
                for row in source_rows
            ]
            write_csv(
                catalog_rows_path,
                list(catalog_rows[0].keys()),
                catalog_rows,
            )

            ascc_data_munger.main([
                "--input", str(catalog_rows_path),
                "--input-dir", str(input_dir),
                "--out-dir", str(out_dir),
                "--reference-work-code", "ASCC6",
                "--region-abbrev", "WV",
            ])
            markings = read_csv(out_dir / "markings.csv")
            dates = read_csv(out_dir / "dates_seen.csv")

        falls_ratemarks = [
            row for row in markings
            if row["type"] == "RATEMARK" and row["catalog_txt"].startswith("Falls Church")
        ]
        lewisburg_auxmarks = [
            row for row in markings
            if row["type"] == "AUXMARK" and row["catalog_txt"].startswith("LEWISBURG")
        ]
        locust_children = [
            row for row in markings
            if row["type"] in {"RATEMARK", "AUXMARK"}
            and row["catalog_txt"].startswith("Locust Level")
        ]
        self.assertEqual(len(falls_ratemarks), 1)
        self.assertEqual(falls_ratemarks[0]["inscription_txt"], "Paid 3")
        self.assertEqual(falls_ratemarks[0]["is_manuscript"], "True")
        self.assertEqual(falls_ratemarks[0]["rate_val"], "3.0")
        self.assertEqual(len(lewisburg_auxmarks), 1)
        self.assertEqual(lewisburg_auxmarks[0]["inscription_txt"], "PAID")
        self.assertEqual(locust_children, [])
        forks = [
            row for row in markings
            if row["type"] == "TOWNMARK"
            and row["catalog_txt"].startswith("Forks of Potomac")
        ]
        self.assertEqual(len(forks), 1)
        fork_dates = [
            row for row in dates
            if row["subject_id"] == forks[0]["code"]
        ]
        self.assertEqual(
            [(row["date"], row["granularity"]) for row in fork_dates],
            [("1860-01-01", "YEAR")],
        )
        self.assertNotIn("1700-01-01", {row["date"] for row in dates})
        self.assertNotIn("1900-01-01", {row["date"] for row in dates})
        self.assertIn("1861-04-16", {row["date"] for row in dates})

    def test_overlay_uses_townmark_color_for_blank_text_fanout(self):
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
                    "txtTownPostmark",
                    "txtTownmarkColor",
                ],
                [{
                    "nRawStateDataID": "183756",
                    "txtRawStateData": "",
                    "txtTownPostmark": "BERKELEY SPRINGS, Va.",
                    "txtTownmarkColor": "Black,Red",
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
            write_csv(bundle / "regions.csv", ["code", "name"], [{"code": "USA-WV1", "name": "West Virginia"}])
            write_csv(bundle / "post_offices.csv", ["name", "code", *AUDIT], [stamped({"name": "BERKELEY SPRINGS", "code": "USA-WV1-5"})])
            write_csv(bundle / "post_office_regions.csv", ["post_office", "region", *AUDIT], [stamped({"post_office": "USA-WV1-5", "region": "USA-WV1"})])
            write_csv(
                bundle / "colors.csv",
                ["name", "hex_val", "pantone_code", *AUDIT],
                [
                    stamped({"name": "BLACK", "hex_val": "#000000", "pantone_code": ""}),
                    stamped({"name": "RED", "hex_val": "#FF0000", "pantone_code": ""}),
                ],
            )
            write_csv(bundle / "letterings.csv", ["name", *AUDIT], [])
            write_csv(bundle / "shapes.csv", ["name", "code", *AUDIT], [])
            write_csv(
                bundle / "markings.csv",
                ["code", "type", "is_manuscript", "color", "post_office", *AUDIT],
                [stamped({"code": "ASCC2-WV-M1100", "type": "TOWNMARK", "is_manuscript": "False", "color": "BLACK", "post_office": "USA-WV1-5"})],
            )
            write_csv(
                bundle / "marking_lineage.csv",
                ["v2_key", "source_listing_idx", "marking_code", "marking_type", "page", "chunk", "catalog_txt"],
                [{"v2_key": "0:183756", "source_listing_idx": "0", "marking_code": "ASCC2-WV-M1100", "marking_type": "TOWNMARK", "page": "0", "chunk": "183756", "catalog_txt": "BERKELEY SPRINGS"}],
            )
            write_csv(bundle / "dates_seen.csv", ["subject_type", "subject_id", "date", "granularity", *AUDIT], [])
            write_csv(bundle / "citations.csv", ["reference_work", "subject_type", "subject_id", "citation_detail", *AUDIT], [])
            write_csv(bundle / "images.csv", v1_bundle_overlay.IMAGE_COLUMNS, [])

            rc = v1_bundle_overlay.main([
                "--state", "WV",
                "--slice", str(slice_path),
                "--image-refs", str(refs_path),
                "--bundle-dir", str(bundle),
                "--v1-image-root", str(root / "images"),
                "--media-dir", str(root / "media" / "wv"),
                "--report", str(report_path),
                "--preserve-images",
            ])
            markings = read_csv(bundle / "markings.csv")

        self.assertEqual(rc, 0)
        self.assertEqual(len(markings), 2)
        self.assertEqual({row["color"] for row in markings}, {"BLACK", "RED"})

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
