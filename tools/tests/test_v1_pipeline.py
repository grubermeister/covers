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
import v1_massachusetts
import ascc_data_munger
from munger.relationships import roll_up_catalog_text
from v1_synthetic_listing import color_tokens, synthetic_desc_lines, synthetic_listing


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
    def test_overlay_reconciles_cover_markings_for_color_variant_changes(self):
        cover_markings = [
            stamped({
                "cover": "ASCC6-VA-C1001",
                "marking": "ASCC6-VA-M1001",
                "review_status": "approved",
            }),
            stamped({
                "cover": "ASCC6-VA-C1002",
                "marking": "ASCC6-VA-M1002",
                "review_status": "approved",
            }),
        ]

        rows = v1_bundle_overlay.reconcile_cover_markings(
            cover_markings,
            {"ASCC6-VA-M1001", "ASCC6-VA-M1002"},
            {
                "ASCC6-VA-M1001-C1": "ASCC6-VA-M1001",
                "ASCC6-VA-M1001-C2": "ASCC6-VA-M1001-C1",
            },
        )

        self.assertEqual(
            {(row["cover"], row["marking"]) for row in rows},
            {
                ("ASCC6-VA-C1001", "ASCC6-VA-M1001-C1"),
                ("ASCC6-VA-C1001", "ASCC6-VA-M1001-C2"),
            },
        )
        self.assertEqual({row["review_status"] for row in rows}, {"approved"})
        self.assertEqual({row["created_by"] for row in rows}, {"1"})
        self.assertEqual({row["modified_by"] for row in rows}, {"1"})

    def test_v1_overlay_replaces_same_with_parent_townmark_text(self):
        cases = [
            ("Same/Wis.", "WATERTOWN/Wis.", "WATERTOWN/Wis."),
            ("(1)Same/Wis.", "(1)WATERTOWN/Wis.", "WATERTOWN/Wis."),
            ("Same/ILL.", "CHICAGO/Ill.", "CHICAGO/ILL."),
            ("Same/ILL. and /Ill.", "ALTON/ILL.", "ALTON/ILL. and /Ill."),
            ("Same", "CABOTVILLE / Ms.", "CABOTVILLE / Ms."),
            ("The same", "DETROIT / Mich.", "DETROIT / Mich."),
            ("Same VA./5", "WINCHESTER.VA", "WINCHESTER VA."),
            ("*Same VA./5", "*WINCHESTER.VA", "WINCHESTER VA."),
            ("Same *VA./5", "WINCHESTER.VA", "WINCHESTER VA."),
            ("Same C.H. VA.", "KANAWHA CH. VA", "KANAWHA CH. VA."),
            ("Same C.H./Va.", "KANAWHA CH. VA.", "KANAWHA CH./Va."),
            ("(1)BETHANY/Va.", "", "BETHANY/Va."),
            ("*RICHMOND/VA.", "", "RICHMOND/VA."),
            ("Wyocena W.T.(E)", "", "Wyocena W.T."),
            ("(L)", "ALEXANDRIA", ""),
            ("Same(E)", "CABOTVILLE / Ms.", "CABOTVILLE / Ms."),
            ("The same(L)", "DETROIT / Mich.", "DETROIT / Mich."),
            ("Same/Ills.(thin letters)", "QUINCY/Ills.(thick letters)", "QUINCY/Ills."),
            ("Same(Green)", "QUINCY/Ills.", "QUINCY/Ills."),
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

    def test_v1_overlay_strips_town_head_notes_from_inscription_text(self):
        cases = [
            ("CHICAGO/Ills.(thick letters)", "CHICAGO/Ills."),
            ("CHICAGO/Ills(thin letters)", "CHICAGO/Ills"),
            ('COLUMBIA/Ten("COLUMBIA" italics)', "COLUMBIA/Ten"),
            ("Caho(Cahokia)", "Caho"),
            ("Charleston (Peoria)", "Charleston"),
            ("Warren(s)ville", "Warren(s)ville"),
            ("CHICAGO/PAID 6", "CHICAGO"),
            ("CHICAGO/6 PAID", "CHICAGO"),
            ("CHICAGO/5", "CHICAGO"),
            ("CHICAGO/ILL", "CHICAGO/ILL"),
            ("BOSTON(87)", "BOSTON(87)"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    v1_bundle_overlay.strip_inscription_markers(raw),
                    expected,
                )

    def test_v1_overlay_collects_removed_inscription_notes_for_desc(self):
        self.assertEqual(
            v1_bundle_overlay.inscription_note_lines({
                "txtTownPostmark": "Caho(Cahokia)",
                "txtPostmark": "",
            }),
            ["Cahokia"],
        )
        self.assertEqual(
            v1_bundle_overlay.inscription_note_lines({
                "txtTownPostmark": "QUINCY/Ills.",
                "txtPostmark": "Same(Green)",
            }),
            [],
        )
        self.assertEqual(
            v1_bundle_overlay.inscription_note_lines({
                "txtTownPostmark": "QUINCY/Ills.(sans serif letters)",
                "txtPostmark": "",
            }),
            [],
        )

    def test_v1_overlay_removes_note_when_writing_inscription_and_desc(self):
        markings_by_id = {
            "M1": {
                "code": "ASCC6-IL-M1001",
                "type": "TOWNMARK",
                "inscription_txt": "CHICAGO/Ills.",
                "desc": "Existing",
                "post_office": "USA-IL1-1",
                "is_manuscript": "False",
            },
        }
        raw_row = {
            "txtTownPostmark": "Caho(Cahokia)",
            "txtPostmark": "",
            "txtTown": "",
            "txtTownmarkShape": "",
            "txtTownmarkLettering": "",
            "txtTownmarkDateFormat": "",
        }

        v1_bundle_overlay.apply_row_fields(
            "159",
            raw_row,
            markings_by_id,
            ["M1"],
            ["M1"],
            [],
            {"shapes": {}, "letterings": {}},
            {},
            [],
        )

        self.assertEqual(markings_by_id["M1"]["inscription_txt"], "Caho")
        self.assertEqual(markings_by_id["M1"]["desc"], "Existing\nCahokia")

    def test_v1_overlay_uses_sans_serif_note_as_lettering(self):
        markings_by_id = {
            "M1": {
                "code": "ASCC6-IL-M1001",
                "type": "TOWNMARK",
                "inscription_txt": "QUINCY/Ills.",
                "desc": "Existing",
                "post_office": "USA-IL1-1",
                "is_manuscript": "False",
                "lettering": "",
            },
        }
        raw_row = {
            "txtTownPostmark": "QUINCY/Ills.(sans serif letters)",
            "txtPostmark": "",
            "txtTown": "",
            "txtTownmarkShape": "",
            "txtTownmarkLettering": "",
            "txtTownmarkDateFormat": "",
        }

        v1_bundle_overlay.apply_row_fields(
            "13420",
            raw_row,
            markings_by_id,
            ["M1"],
            ["M1"],
            [],
            {"shapes": {}, "letterings": {"SANS-SERIF": "Sans-serif"}},
            {},
            [],
        )

        self.assertEqual(markings_by_id["M1"]["inscription_txt"], "QUINCY/Ills.")
        self.assertEqual(markings_by_id["M1"]["lettering"], "Sans-serif")
        self.assertEqual(markings_by_id["M1"]["desc"], "Existing")

    def test_v1_overlay_applies_rate_note_to_split_ratemark_desc(self):
        markings_by_id = {
            "TM1": {
                "code": "ASCC6-IL-M1001",
                "type": "TOWNMARK",
                "inscription_txt": "CHICAGO/PAID 3",
                "desc": "",
                "post_office": "USA-IL1-1",
                "is_manuscript": "False",
            },
            "RM1": {
                "code": "ASCC6-IL-M1001/RM0",
                "type": "RATEMARK",
                "inscription_txt": "CHICAGO PAID 3",
                "desc": "Existing rate",
                "rate_val": "3.0",
            },
        }
        raw_row = {
            "txtTownPostmark": "CHICAGO/PAID 3",
            "txtPostmark": "",
            "txtTown": "",
            "txtRates": "Use on #U10 envelope",
            "txtOther": "Town note",
            "memNotes": "",
            "txtTownmarkShape": "",
            "txtTownmarkLettering": "",
            "txtTownmarkDateFormat": "",
        }

        v1_bundle_overlay.apply_row_fields(
            "13067",
            raw_row,
            markings_by_id,
            ["TM1", "RM1"],
            ["TM1"],
            ["RM1"],
            {"shapes": {}, "letterings": {}},
            {},
            [],
        )

        self.assertEqual(markings_by_id["TM1"]["inscription_txt"], "CHICAGO")
        self.assertEqual(markings_by_id["TM1"]["desc"], "Town note")
        self.assertEqual(
            markings_by_id["RM1"]["desc"],
            "Existing rate\nRate note: Use on #U10 envelope",
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

    def test_v1_overlay_keeps_unknown_post_office_for_no_town_text(self):
        cases = [
            (
                {
                    "txtTown": "(No Town Marking)",
                    "txtTownPostmark": "(No town marking)",
                    "txtPostmark": "(No town marking)",
                },
                "(No town marking)",
            ),
            (
                {
                    "txtTown": "Mobile",
                    "txtTownPostmark": "ADVERTISED",
                    "txtPostmark": "ADVERTISED",
                },
                "ADVERTISED",
            ),
            (
                {
                    "txtTown": "Mobile",
                    "txtTownPostmark": "REGISTERED/NO____",
                    "txtPostmark": "REGISTERED/NO____",
                },
                "REGISTERED/NO____",
            ),
        ]
        for raw_row, expected_inscription in cases:
            with self.subTest(raw_row=raw_row):
                markings_by_id = {
                    "M1": {
                        "code": "ASCC6-AL-M1051",
                        "type": "TOWNMARK",
                        "inscription_txt": "",
                        "post_office": "USA-AL1-999",
                        "is_manuscript": "False",
                    },
                }
                post_offices = [
                    stamped({"name": "UNKNOWN", "code": "USA-AL1-999"}),
                    stamped({"name": "MOBILE", "code": "USA-AL1-326"}),
                ]
                post_office_regions = [
                    stamped({"post_office": "USA-AL1-999", "region": "USA-AL1"}),
                    stamped({"post_office": "USA-AL1-326", "region": "USA-AL1"}),
                ]
                raw_row = {
                    **raw_row,
                    "txtTownmarkShape": "",
                    "txtTownmarkLettering": "",
                    "txtTownmarkDateFormat": "",
                }

                v1_bundle_overlay.apply_row_fields(
                    "10447",
                    raw_row,
                    markings_by_id,
                    ["M1"],
                    ["M1"],
                    [],
                    {"shapes": {}, "letterings": {}},
                    {
                        "post_offices": post_offices,
                        "post_office_fields": ["name", "code", *AUDIT],
                        "post_office_regions": post_office_regions,
                        "post_office_region_fields": [
                            "post_office",
                            "region",
                            *AUDIT,
                        ],
                        "regions": [{"code": "USA-AL1", "name": "Alabama"}],
                        "audit": AUDIT,
                    },
                    [],
                )

                self.assertEqual(markings_by_id["M1"]["post_office"], "USA-AL1-999")
                self.assertEqual(markings_by_id["M1"]["inscription_txt"], expected_inscription)
                self.assertNotIn(
                    "(NO TOWN MARKING)",
                    {row["name"] for row in post_offices},
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

    def test_split_glued_same_listings_handles_v1_variants(self):
        cases = [
            (
                "Same/GEO(1851;31.5;FREE,PAID,3,5;Black,Red) 25  "
                "Same/GA(1856-58;31.5;FREE,5;Black) 20",
                [
                    "Same/GEO(1851;31.5;FREE,PAID,3,5;Black,Red) 25",
                    "Same/GA(1856-58;31.5;FREE,5;Black) 20",
                ],
            ),
            (
                "MARYVILLE/Te.(1834-46;30;FREE,PAID;Black,Brown,Red) 65  "
                "Same(Green,Purple) 100",
                [
                    "MARYVILLE/Te.(1834-46;30;FREE,PAID;Black,Brown,Red) 65",
                    "Same(Green,Purple) 100",
                ],
            ),
            (
                "(L)(March 31, 1859) 200  "
                "Same/sans serif letters(Sept. 20, --;C-34;FREE;Black) 200",
                [
                    "(L)(March 31, 1859) 200",
                    "Same/sans serif letters(Sept. 20, --;C-34;FREE;Black) 200",
                ],
            ),
            (
                "BASE(1850;Black) 10 +Same(Blue) 20",
                ["BASE(1850;Black) 10", "+Same(Blue) 20"],
            ),
            (
                "Same VA.(1855-61;--;FREE;Black) 60 Same note only",
                ["Same VA.(1855-61;--;FREE;Black) 60 Same note only"],
            ),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    v1_catalog_rows.split_glued_same_listings(text),
                    expected,
                )

    def test_catalog_rows_split_glued_same_listing_after_value(self):
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
                [{
                    "nRawStateDataID": "109453",
                    "txtRawStateData": (
                        "Same VA.(1855-61;--;FREE;Black) 60 "
                        "(1)Same(PAID,3;Green) 50"
                    ),
                    "txtTown": "",
                    "txtTownPostmark": "",
                }],
            )

            rows_written, raw_ids = v1_catalog_rows.write_v1_catalog_rows(
                slice_out,
                catalog_out,
            )
            catalog_rows = read_csv(catalog_out)

        self.assertEqual(rows_written, 2)
        self.assertEqual(raw_ids, ["109453", "109453"])
        self.assertEqual(
            [row["listing_text"] for row in catalog_rows],
            [
                "Same VA.(1855-61;--;FREE;Black) 60",
                "(1)Same(PAID,3;Green) 50",
            ],
        )
        self.assertEqual(
            [row["chunk_number"] for row in catalog_rows],
            ["109453", "109453"],
        )

    def test_massachusetts_boston_catalog_rows_strip_bpm_markup(self):
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
                    "txtPostmark",
                    "txtTownPostmark",
                    "txtRatesText",
                ],
                [
                    {
                        "nRawStateDataID": "17621",
                        "txtRawStateData": (
                            "BOSTON(87)(1788-89;SL-28x5;FREE[BPM 103];Black,Brown) 100"
                        ),
                        "txtTown": "Boston",
                        "txtPostmark": "BOSTON(87)",
                        "txtTownPostmark": "BOSTON",
                        "txtRatesText": "FREE[BPM 103]",
                    },
                    {
                        "nRawStateDataID": "17625",
                        "txtRawStateData": (
                            "Same(125,132,136)(1789-91;SL-18.5x3,MDD;PAID[BPM 129];Black) 80"
                        ),
                        "txtTown": "Boston",
                        "txtPostmark": "Same(125,132,136)",
                        "txtTownPostmark": "BOSTON",
                        "txtRatesText": "PAID[BPM 129]",
                    },
                    {
                        "nRawStateDataID": "17634",
                        "txtRawStateData": (
                            "BOSTON Ms.(202)(5 stars at bottom)(1805-09;27.5;Black,Red) 30"
                        ),
                        "txtTown": "Boston",
                        "txtPostmark": "BOSTON Ms.(202)(5 stars at bottom)",
                        "txtTownPostmark": "BOSTON Ms.",
                        "txtRatesText": "",
                    },
                    {
                        "nRawStateDataID": "17702",
                        "txtRawStateData": (
                            "(493) 3 O'CLOCK/DELIVERY(1854-56;oval-34;Red) 15"
                        ),
                        "txtTown": "Boston",
                        "txtPostmark": "(493) 3 O'CLOCK/DELIVERY",
                        "txtTownPostmark": "3 O'CLOCK/DELIVERY",
                        "txtRatesText": "",
                    },
                    {
                        "nRawStateDataID": "17415",
                        "txtRawStateData": (
                            "B(1 and others)(E)(April 1703;Ms;Black) See Ocean Mail --"
                        ),
                        "txtTown": "Boston",
                        "txtPostmark": "B(1 and others)(E)",
                        "txtTownPostmark": "B",
                        "txtRatesText": "",
                    },
                    {
                        "nRawStateDataID": "90001",
                        "txtRawStateData": "BOSTON(67)(1850;Black) 10",
                        "txtTown": "East Boston",
                        "txtPostmark": "BOSTON(67)",
                        "txtTownPostmark": "BOSTON",
                        "txtRatesText": "",
                    },
                ],
            )

            rows_written, raw_ids = v1_catalog_rows.write_v1_catalog_rows(
                slice_out,
                catalog_out,
                state="MA",
            )
            catalog_rows = read_csv(catalog_out)

        self.assertEqual(rows_written, 6)
        self.assertEqual(
            [row["listing_text"] for row in catalog_rows],
            [
                "BOSTON(1788-89;SL-28x5;FREE;Black,Brown) 100",
                "BOSTON(1789-91;SL-18.5x3,MDD;PAID;Black) 80",
                "BOSTON Ms.(5 stars at bottom)(1805-09;27.5;Black,Red) 30",
                "3 O'CLOCK/DELIVERY(1854-56;oval-34;Red) 15",
                "B(E)(April 1703;Ms;Black) See Ocean Mail --",
                "BOSTON(67)(1850;Black) 10",
            ],
        )
        self.assertEqual(
            raw_ids,
            ["17621", "17625", "17634", "17702", "17415", "90001"],
        )
        self.assertEqual(
            v1_massachusetts.boston_head_description_lines({
                "txtRawStateData": "(L)(March 19, 1787) 300",
                "txtPostmark": "(L)(March 19, 1787)",
            }),
            [],
        )
        self.assertEqual(
            v1_massachusetts.boston_head_description_lines({
                "txtRawStateData": (
                    "BOSTON Ms.(202)(5 stars at bottom)"
                    "(1805-09;27.5;Black,Red) 30"
                ),
                "txtPostmark": "BOSTON Ms.(202)(5 stars at bottom)",
            }),
            ["5 stars at bottom"],
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
        source_map_rows = [
            {"chunk": "71", "marking_code": "ASCC1-VA-M1100"},
            {"chunk": "72", "marking_code": "ASCC1-VA-M1101"},
        ]

        rolled = roll_up_catalog_text(listings)

        self.assertEqual(len(rolled), 2)
        self.assertEqual(rolled.loc[0, "rolled_catalog_text"], "RICHMOND (1850;Black) 10")
        self.assertEqual(rolled.loc[1, "rolled_catalog_text"], "RICHMOND (1850;Black) 10")
        self.assertEqual(rolled["raw_id"].tolist(), ["71", "72"])
        self.assertEqual(rolled["parsed_colors"].tolist(), [["BLACK"], ["RED"]])
        self.assertEqual([row["chunk"] for row in source_map_rows], ["71", "72"])

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

    def test_color_stranded_in_rate_column_is_color_not_rate_note(self):
        # IA ATHENS (raw 10465): the catalog line has no rate field, so the
        # v1 positional split put the second color in txtRatesText. The
        # stranded color must join color_tokens (so ensure_townmark_colors
        # keeps both fan-out townmarks) and must NOT become a desc line.
        row = {
            "txtColors": "Red",
            "txtRatesText": "Blue",
            "txtTownmarkColor": "Blue,Red",
        }

        self.assertEqual(color_tokens(row), ["RED", "BLUE"])
        self.assertEqual(synthetic_desc_lines(row), [])

    def test_rate_fragment_in_color_column_falls_back_to_townmark_color(self):
        # VA RICHMOND (raw 569): the legacy split put "PAID,6" in txtColors
        # and "MDD" in txtRatesText. txtTownmarkColor is the authoritative
        # v1 color source for this malformed split.
        row = {
            "txtRawStateData": (
                "RICHMOND,(1788-89;SL-25x3;MDD;PAID,6;Black) 150"
            ),
            "txtColors": "PAID,6",
            "txtRatesText": "MDD",
            "txtTownmarkColor": "Black",
        }

        self.assertEqual(color_tokens(row), ["BLACK"])
        self.assertEqual(synthetic_desc_lines(row), [])

    def test_v1_color_source_accepts_compound_color_phrases(self):
        row = {"txtColors": "Black brown,Blue/Green,Bright red"}

        self.assertEqual(
            color_tokens(row),
            ["BLACK BROWN", "BLUE/GREEN", "BRIGHT RED"],
        )

    def test_multi_color_rate_cell_joins_color_tokens(self):
        row = {"txtRates": "Blue,Black"}

        self.assertEqual(color_tokens(row), ["BLUE", "BLACK"])
        self.assertEqual(synthetic_desc_lines(row), [])

    def test_non_color_rate_note_still_preserved_as_desc(self):
        row = {
            "txtColors": "Black",
            "txtRates": "Use on #U10 envelope",
        }

        self.assertEqual(color_tokens(row), ["BLACK"])
        self.assertEqual(
            synthetic_desc_lines(row),
            ["Rate note: Use on #U10 envelope"],
        )

    def test_parsed_rate_values_understands_roman_numerals(self):
        # IA DAVENPORT (raw txtRatesText='X,PAID,PAID 3'): X is a 10 cent
        # ratemark. Dropping it collapsed the list to ['3'] and the overlay
        # then stamped 3 onto every ratemark of the listing.
        self.assertEqual(
            v1_bundle_overlay.parsed_rate_values("X,PAID,PAID 3"),
            ["10", "3"],
        )
        self.assertEqual(v1_bundle_overlay.parsed_rate_values("V"), ["5"])

    def test_parsed_rate_values_understands_fractions(self):
        self.assertEqual(
            v1_bundle_overlay.parsed_rate_values("12-1/2"),
            ["12.5"],
        )

    def test_append_desc_dedupes_rate_note_against_bare_errata_line(self):
        self.assertEqual(
            v1_bundle_overlay.append_desc("day in ms", ["Rate note: day in ms"]),
            "day in ms",
        )
        self.assertEqual(
            v1_bundle_overlay.append_desc("Rate note: day in ms", ["day in ms"]),
            "Rate note: day in ms",
        )
        self.assertEqual(
            v1_bundle_overlay.append_desc("Backstamp", ["Rate note: Double rate"]),
            "Backstamp\nRate note: Double rate",
        )

    def test_overlay_skips_approximate_dates_seen_tokens(self):
        rows = v1_bundle_overlay.parsed_date_rows(
            "1857, 1859, 1850s, c1850, 1850c",
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
            ],
        )

    def test_overlay_skips_v1_sentinel_dates_seen_years(self):
        rows = v1_bundle_overlay.parsed_date_rows(
            "1700 - 1900",
            ["ASCC6-WV-M1834", "ASCC6-WV-M1835"],
            AUDIT,
        )

        self.assertEqual(rows, [])

    def test_overlay_keeps_real_dates_when_sentinel_years_are_mixed_in(self):
        rows = v1_bundle_overlay.parsed_date_rows(
            "1700, 1854, 1900",
            ["ASCC6-WV-M1834"],
            AUDIT,
        )
        observed = [
            (row["subject_id"], row["date"], row["granularity"])
            for row in rows
        ]

        self.assertEqual(
            observed,
            [("ASCC6-WV-M1834", "1854-01-01", "YEAR")],
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
                {
                    "nRawStateDataID": "5",
                    "txtTownPostmark": "Franklin",
                    "nEarliestUseDay": "1",
                    "txtEarliestUseMonth": "5",
                    "txtEarliestUseYear": "1855",
                    "txtRates": "PM frank",
                    "txtTownmarkColor": "Black",
                },
                {
                    "nRawStateDataID": "6",
                    "txtTownPostmark": "Freeport",
                    "nEarliestUseDay": "2",
                    "txtEarliestUseMonth": "5",
                    "txtEarliestUseYear": "1855",
                    "txtRates": "free frank[ms]",
                    "txtTownmarkColor": "Black",
                },
                {
                    "nRawStateDataID": "7",
                    "txtTownPostmark": "Bare Circle",
                    "txtRates": "PAID",
                    "txtTownmarkShape": "Circle",
                    "txtTownmarkColor": "Red",
                },
                {
                    "nRawStateDataID": "8",
                    "txtTownPostmark": "WEBSTER/Va",
                    "txtDatesSeen": "1859-61",
                    "txtSizes": "32",
                    "txtRates": "PD 3[neg]",
                    "txtTownmarkColor": "Black",
                },
                {
                    "nRawStateDataID": "9",
                    "txtTownPostmark": "Stencilville",
                    "txtRates": "stencil 5",
                    "txtTownmarkColor": "Black",
                },
                {
                    "nRawStateDataID": "10",
                    "txtTownPostmark": "MARTINSBURGVA.",
                    "txtRates": "DUE 3",
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
            catalog_rows.append({
                "listing_text": "(1)PARKERSBURG/VA.(1832-36;stencil C-31;Black) 250",
                "catalog_page": "0",
                "chunk_number": "11",
                "image_count": "0",
                "row_type": "LISTING",
                "is_manuscript": "",
                "default_shape": "",
                "institutional_owner": "",
            })
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
            covers = read_csv(out_dir / "covers.csv")
            cover_markings = read_csv(out_dir / "cover_markings.csv")
            covers_header = (out_dir / "covers.csv").read_text().splitlines()[0]
            cover_markings_header = (
                out_dir / "cover_markings.csv"
            ).read_text().splitlines()[0]

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
        franklin_townmarks = [
            row for row in markings
            if row["type"] == "TOWNMARK"
            and row["catalog_txt"].startswith("Franklin")
        ]
        franklin_children = [
            row for row in markings
            if row["type"] in {"RATEMARK", "AUXMARK"}
            and row["catalog_txt"].startswith("Franklin")
        ]
        freeport_townmarks = [
            row for row in markings
            if row["type"] == "TOWNMARK"
            and row["catalog_txt"].startswith("Freeport")
        ]
        freeport_children = [
            row for row in markings
            if row["type"] in {"RATEMARK", "AUXMARK"}
            and row["catalog_txt"].startswith("Freeport")
        ]
        bare_circle_townmarks = [
            row for row in markings
            if row["type"] == "TOWNMARK"
            and row["catalog_txt"].startswith("Bare Circle")
        ]
        bare_circle_auxmarks = [
            row for row in markings
            if row["type"] == "AUXMARK"
            and row["catalog_txt"].startswith("Bare Circle")
        ]
        bare_circle_ratemarks = [
            row for row in markings
            if row["type"] == "RATEMARK"
            and row["catalog_txt"].startswith("Bare Circle")
        ]
        webster_ratemarks = [
            row for row in markings
            if row["type"] == "RATEMARK"
            and row["catalog_txt"].startswith("WEBSTER")
        ]
        stencil_ratemarks = [
            row for row in markings
            if row["type"] == "RATEMARK"
            and row["catalog_txt"].startswith("Stencilville")
        ]
        martinsburg_ratemarks = [
            row for row in markings
            if row["type"] == "RATEMARK"
            and row["catalog_txt"].startswith("MARTINSBURGVA.")
        ]
        parkersburg_townmarks = [
            row for row in markings
            if row["type"] == "TOWNMARK"
            and "PARKERSBURG/VA." in row["catalog_txt"]
        ]
        parkersburg_children = [
            row for row in markings
            if row["type"] in {"RATEMARK", "AUXMARK"}
            and "PARKERSBURG/VA." in row["catalog_txt"]
        ]
        self.assertEqual(len(falls_ratemarks), 1)
        self.assertEqual(falls_ratemarks[0]["inscription_txt"], "Falls Church Paid 3")
        self.assertEqual(falls_ratemarks[0]["is_manuscript"], "True")
        self.assertEqual(falls_ratemarks[0]["rate_val"], "3.0")
        self.assertEqual(len(lewisburg_auxmarks), 1)
        self.assertEqual(lewisburg_auxmarks[0]["inscription_txt"], "LEWISBURG/Va. PAID")
        self.assertEqual(locust_children, [])
        self.assertEqual(len(franklin_townmarks), 1)
        self.assertEqual(franklin_townmarks[0]["desc"], "PM frank")
        self.assertEqual(franklin_children, [])
        self.assertEqual(len(freeport_townmarks), 1)
        self.assertEqual(freeport_townmarks[0]["desc"], "free frank")
        self.assertEqual(freeport_children, [])
        self.assertEqual(len(bare_circle_townmarks), 1)
        self.assertEqual(bare_circle_townmarks[0]["shape"], "C - Circle")
        self.assertEqual([row["inscription_txt"] for row in bare_circle_auxmarks], ["Bare Circle PAID"])
        self.assertEqual(bare_circle_ratemarks, [])
        self.assertEqual(len(webster_ratemarks), 1)
        self.assertEqual(webster_ratemarks[0]["inscription_txt"], "WEBSTER/Va PD 3")
        self.assertEqual(webster_ratemarks[0]["impression"], "Negative")
        self.assertEqual(len(stencil_ratemarks), 1)
        self.assertEqual(stencil_ratemarks[0]["inscription_txt"], "Stencilville 5")
        self.assertEqual(stencil_ratemarks[0]["impression"], "Stencil")
        self.assertEqual(len(martinsburg_ratemarks), 1)
        self.assertEqual(
            martinsburg_ratemarks[0]["inscription_txt"],
            "MARTINSBURGVA. DUE 3",
        )
        self.assertEqual(len(parkersburg_townmarks), 1)
        self.assertEqual(parkersburg_townmarks[0]["shape"], "C - Circle")
        self.assertEqual(parkersburg_townmarks[0]["width"], "31.0")
        self.assertEqual(parkersburg_townmarks[0]["height"], "31.0")
        self.assertEqual(parkersburg_townmarks[0]["impression"], "Stencil")
        self.assertEqual(parkersburg_children, [])
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
            [],
        )
        self.assertEqual(forks[0]["desc"], "Date(s) seen: c1860")
        self.assertNotIn("1700-01-01", {row["date"] for row in dates})
        self.assertNotIn("1900-01-01", {row["date"] for row in dates})
        self.assertIn("1861-04-16", {row["date"] for row in dates})
        self.assertEqual(covers, [])
        self.assertEqual(cover_markings, [])
        self.assertTrue(covers_header.startswith("code,color,type,"))
        self.assertTrue(
            cover_markings_header.startswith("cover,marking,is_backstamp,")
        )

    def test_munger_emits_institutional_covers_for_starred_townmark_variants(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            headers = [
                "listing_text",
                "catalog_page",
                "chunk_number",
                "image_count",
                "row_type",
                "is_manuscript",
                "default_shape",
                "institutional_owner",
            ]
            base = {
                "catalog_page": "0",
                "image_count": "0",
                "row_type": "LISTING",
                "is_manuscript": "",
                "default_shape": "",
                "institutional_owner": "",
            }
            write_csv(
                catalog_rows_path,
                headers,
                [
                    {
                        **base,
                        "listing_text": "PLAIN/W.VA.(1850;C-30;Black) 10",
                        "chunk_number": "1",
                    },
                    {
                        **base,
                        "listing_text": (
                            "*STARRED/W.VA.(1851;C-31;PAID,5;Blue,Red) 20"
                        ),
                        "chunk_number": "2",
                    },
                    {
                        **base,
                        "listing_text": (
                            "PARENT/W.VA.(1852;C-32;Black,Green) 30"
                        ),
                        "chunk_number": "3",
                    },
                    {
                        **base,
                        "listing_text": "*(L)(1853) 40",
                        "chunk_number": "4",
                    },
                ],
            )

            ascc_data_munger.main([
                "--input", str(catalog_rows_path),
                "--input-dir", str(input_dir),
                "--out-dir", str(out_dir),
                "--reference-work-code", "ASCC6",
                "--region-abbrev", "WV",
            ])
            markings = read_csv(out_dir / "markings.csv")
            source_map = read_csv(out_dir / "source_marking_map.csv")
            covers = read_csv(out_dir / "covers.csv")
            cover_markings = read_csv(out_dir / "cover_markings.csv")

        townmark_codes_by_chunk = {}
        for row in source_map:
            if row["marking_type"] != "TOWNMARK":
                continue
            townmark_codes_by_chunk.setdefault(row["chunk"], []).append(
                row["marking_code"]
            )
        expected_linked_codes = set(townmark_codes_by_chunk["2"])
        expected_linked_codes.update(townmark_codes_by_chunk["3"])
        non_townmark_codes = {
            row["code"] for row in markings if row["type"] != "TOWNMARK"
        }

        self.assertEqual([row["code"] for row in covers], [
            "ASCC6-WV-C1001",
            "ASCC6-WV-C1002",
        ])
        self.assertEqual({row["is_institutional"] for row in covers}, {"True"})
        self.assertEqual({row["has_adhesive"] for row in covers}, {"False"})
        self.assertEqual(
            {row["display_submitter_name"] for row in covers},
            {"False"},
        )
        for row in covers:
            self.assertEqual(row["color"], "")
            self.assertEqual(row["type"], "")
            self.assertEqual(row["height"], "")
            self.assertEqual(row["width"], "")
            self.assertEqual(row["description"], "")
            self.assertEqual(row["created_by"], "1")
            self.assertEqual(row["modified_by"], "1")

        self.assertEqual(len(townmark_codes_by_chunk["2"]), 2)
        self.assertEqual(len(townmark_codes_by_chunk["3"]), 2)
        self.assertEqual(len(cover_markings), 4)
        self.assertEqual(
            {row["marking"] for row in cover_markings},
            expected_linked_codes,
        )
        self.assertTrue(
            {row["marking"] for row in cover_markings}.isdisjoint(
                non_townmark_codes
            )
        )
        self.assertEqual(
            {
                row["marking"]
                for row in cover_markings
                if row["cover"] == "ASCC6-WV-C1001"
            },
            set(townmark_codes_by_chunk["2"]),
        )
        self.assertEqual(
            {
                row["marking"]
                for row in cover_markings
                if row["cover"] == "ASCC6-WV-C1002"
            },
            set(townmark_codes_by_chunk["3"]),
        )
        for row in cover_markings:
            self.assertEqual(row["is_backstamp"], "False")
            self.assertEqual(row["placement"], "")
            self.assertEqual(row["contributor_comment"], "")
            self.assertEqual(row["review_status"], "approved")
            self.assertEqual(row["reviewer"], "")
            self.assertEqual(row["review_notes"], "")
            self.assertEqual(row["reviewed_at"], "")
            self.assertEqual(row["created_by"], "1")
            self.assertEqual(row["modified_by"], "1")

    def test_munger_does_not_copy_images_to_color_fanout_siblings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            media_root = root / "media"
            image_path = media_root / "wv" / "wv-12-34-1.png"
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (4, 3)).save(image_path)
            write_csv(
                catalog_rows_path,
                [
                    "listing_text",
                    "catalog_page",
                    "chunk_number",
                    "image_count",
                    "row_type",
                    "is_manuscript",
                    "default_shape",
                    "institutional_owner",
                ],
                [{
                    "listing_text": "WHEELING/VA.(1840;C-30;Blue,Red) 50",
                    "catalog_page": "12",
                    "chunk_number": "34",
                    "image_count": "1",
                    "row_type": "LISTING",
                    "is_manuscript": "",
                    "default_shape": "",
                    "institutional_owner": "",
                }],
            )

            original_media_root = ascc_data_munger.MEDIA_ROOT
            try:
                ascc_data_munger.MEDIA_ROOT = media_root
                ascc_data_munger.main([
                    "--input", str(catalog_rows_path),
                    "--input-dir", str(input_dir),
                    "--out-dir", str(out_dir),
                    "--reference-work-code", "ASCC6",
                    "--region-abbrev", "WV",
                ])
            finally:
                ascc_data_munger.MEDIA_ROOT = original_media_root

            markings = [
                row for row in read_csv(out_dir / "markings.csv")
                if row["type"] == "TOWNMARK"
            ]
            images = read_csv(out_dir / "images.csv")

        self.assertEqual([row["color"] for row in markings], ["BLUE", "RED"])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["subject_id"], markings[0]["code"])

    def test_munger_keeps_decade_text_in_desc_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            write_csv(
                catalog_rows_path,
                [
                    "listing_text",
                    "catalog_page",
                    "chunk_number",
                    "image_count",
                    "row_type",
                    "is_manuscript",
                    "default_shape",
                    "institutional_owner",
                ],
                [{
                    "listing_text": "STEILACOOM CITY/W.T.(1850s;C-37;FREE;Black) 750",
                    "catalog_page": "0",
                    "chunk_number": "99",
                    "image_count": "0",
                    "row_type": "LISTING",
                    "is_manuscript": "",
                    "default_shape": "",
                    "institutional_owner": "",
                }],
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

        townmark = next(row for row in markings if row["type"] == "TOWNMARK")
        self.assertEqual(townmark["desc"], "Date(s) seen: 1850s")
        self.assertEqual(
            [row for row in dates if row["subject_id"] == townmark["code"]],
            [],
        )

    def test_munger_parses_arc_unknown_size_with_nor_qualifier(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            catalog_text = (
                "ADAMSVILLE/MICH."
                "(1850s;arc--,NOR;PAID/3[arc];Black) 500"
            )
            write_csv(
                catalog_rows_path,
                [
                    "listing_text",
                    "catalog_page",
                    "chunk_number",
                    "image_count",
                    "row_type",
                    "is_manuscript",
                    "default_shape",
                    "institutional_owner",
                ],
                [{
                    "listing_text": catalog_text,
                    "catalog_page": "0",
                    "chunk_number": "21114",
                    "image_count": "0",
                    "row_type": "LISTING",
                    "is_manuscript": "",
                    "default_shape": "",
                    "institutional_owner": "",
                }],
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

        townmark = next(row for row in markings if row["type"] == "TOWNMARK")
        self.assertEqual(townmark["shape"], "ARC - Arc or Semi-circle")
        self.assertEqual(townmark["desc"], "Date(s) seen: 1850s\nNOR")
        self.assertEqual(
            [row for row in dates if row["subject_id"] == townmark["code"]],
            [],
        )

    def test_munger_splits_fancy_double_circle_rate_bracket(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            write_csv(
                catalog_rows_path,
                [
                    "listing_text",
                    "catalog_page",
                    "chunk_number",
                    "image_count",
                    "row_type",
                    "is_manuscript",
                    "default_shape",
                    "institutional_owner",
                ],
                [{
                    "listing_text": (
                        "WELLSBURG/VA.(1832-52;30;5[F,DC];Blue) 25"
                    ),
                    "catalog_page": "0",
                    "chunk_number": "99",
                    "image_count": "0",
                    "row_type": "LISTING",
                    "is_manuscript": "",
                    "default_shape": "",
                    "institutional_owner": "",
                }],
            )

            ascc_data_munger.main([
                "--input", str(catalog_rows_path),
                "--input-dir", str(input_dir),
                "--out-dir", str(out_dir),
                "--reference-work-code", "ASCC6",
                "--region-abbrev", "WV",
            ])
            markings = read_csv(out_dir / "markings.csv")

        ratemark = next(row for row in markings if row["type"] == "RATEMARK")
        self.assertEqual(ratemark["shape"], "DC - Double Circle")
        self.assertEqual(ratemark["desc"], "Fancy")
        self.assertEqual(ratemark["rate_val"], "5.0")

    def test_munger_emits_fractional_rate_from_same_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            headers = [
                "listing_text",
                "catalog_page",
                "chunk_number",
                "image_count",
                "row_type",
                "is_manuscript",
                "default_shape",
                "institutional_owner",
            ]
            base = {
                "catalog_page": "0",
                "image_count": "0",
                "row_type": "LISTING",
                "is_manuscript": "",
                "default_shape": "",
                "institutional_owner": "",
            }
            write_csv(
                catalog_rows_path,
                headers,
                [
                    {
                        **base,
                        "listing_text": (
                            "(1)WHITE SULr.SPRs.VA."
                            "(1834-51;30;PAID,5,10;Brown,Red) 35"
                        ),
                        "chunk_number": "1",
                    },
                    {
                        **base,
                        "listing_text": (
                            "Same(1838-44;30;18-3/4,25;Red) 50"
                        ),
                        "chunk_number": "2",
                    },
                ],
            )

            ascc_data_munger.main([
                "--input", str(catalog_rows_path),
                "--input-dir", str(input_dir),
                "--out-dir", str(out_dir),
                "--reference-work-code", "ASCC6",
                "--region-abbrev", "WV",
            ])
            markings = read_csv(out_dir / "markings.csv")

        same_ratemarks = [
            row for row in markings
            if row["type"] == "RATEMARK"
            and "18-3/4,25" in row["catalog_txt"]
        ]
        observed = {
            (row["inscription_txt"], float(row["rate_val"]))
            for row in same_ratemarks
        }

        self.assertIn(("WHITE SULr.SPRs.VA. 18-3/4", 18.75), observed)
        self.assertIn(("WHITE SULr.SPRs.VA. 25", 25.0), observed)

    def test_overlay_uses_townmark_color_for_blank_text_fanout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            slice_path = root / "slice.csv"
            refs_path = root / "image_refs.csv"
            report_path = bundle / "v1_pipeline_warnings.csv"

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
                bundle / "source_marking_map.csv",
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
                "--warnings", str(report_path),
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
            report_path = bundle / "v1_pipeline_warnings.csv"
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
                bundle / "source_marking_map.csv",
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
                "--warnings", str(report_path),
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
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["subject_id"], townmarks[0]["code"])
        self.assertTrue(media_exists)
        self.assertIn("unsupported_column", {r["issue"] for r in report})

    def test_overlay_adds_boston_bpm2_citations_and_description(self):
        raw_rows = {
            "17621": {
                "nRawStateDataID": "17621",
                "txtRawStateData": (
                    "BOSTON(87)(1788-89;SL-28x5;FREE[BPM 103];Black,Brown) 100"
                ),
                "txtTown": "Boston",
                "txtPostmark": "BOSTON(87)",
                "txtTownPostmark": "BOSTON",
                "txtRatesText": "FREE[BPM 103]",
            },
            "17706": {
                "nRawStateDataID": "17706",
                "txtRawStateData": (
                    "BOSTON./52(652,3,4)(Also 55,58 & 59)"
                    "(1852-59;C-32.5;Black) On reverse 100"
                ),
                "txtTown": "Boston",
                "txtPostmark": "BOSTON./52(652,3,4)(Also 55,58 & 59)",
                "txtTownPostmark": "BOSTON./52",
                "txtRatesText": "",
            },
            "17634": {
                "nRawStateDataID": "17634",
                "txtRawStateData": (
                    "BOSTON Ms.(202)(5 stars at bottom)(1805-09;27.5;Black,Red) 30"
                ),
                "txtTown": "Boston",
                "txtPostmark": "BOSTON Ms.(202)(5 stars at bottom)",
                "txtTownPostmark": "BOSTON Ms.",
                "txtRatesText": "",
            },
            "17710": {
                "nRawStateDataID": "17710",
                "txtRawStateData": "BOSTON(1837;Black) 10",
                "txtTown": "Boston",
                "txtPostmark": "BOSTON",
                "txtTownPostmark": "BOSTON",
                "txtRatesText": "",
            },
            "17415": {
                "nRawStateDataID": "17415",
                "txtRawStateData": (
                    "B(1 and others)(E)(April 1703;Ms;Black) See Ocean Mail --"
                ),
                "txtTown": "Boston",
                "txtPostmark": "B(1 and others)(E)",
                "txtTownPostmark": "B",
                "txtRatesText": "",
            },
        }
        source_map_rows = [
            {
                "chunk": "17621",
                "marking_code": "TM1",
                "marking_type": "TOWNMARK",
            },
            {
                "chunk": "17621",
                "marking_code": "AX1",
                "marking_type": "AUXMARK",
            },
            {
                "chunk": "17706",
                "marking_code": "TM2",
                "marking_type": "TOWNMARK",
            },
            {
                "chunk": "17634",
                "marking_code": "TM5",
                "marking_type": "TOWNMARK",
            },
            {
                "chunk": "17710",
                "marking_code": "TM3",
                "marking_type": "TOWNMARK",
            },
            {
                "chunk": "17415",
                "marking_code": "TM4",
                "marking_type": "TOWNMARK",
            },
        ]
        markings_by_id = {
            "TM1": {"code": "TM1", "type": "TOWNMARK", "inscription_txt": "BOSTON(87)", "desc": ""},
            "AX1": {"code": "AX1", "type": "AUXMARK", "inscription_txt": "BOSTON FREE", "desc": ""},
            "TM2": {"code": "TM2", "type": "TOWNMARK", "inscription_txt": "BOSTON./52", "desc": "On reverse"},
            "TM5": {"code": "TM5", "type": "TOWNMARK", "inscription_txt": "BOSTON Ms.", "desc": ""},
            "TM3": {"code": "TM3", "type": "TOWNMARK", "inscription_txt": "BOSTON", "desc": ""},
            "TM4": {"code": "TM4", "type": "TOWNMARK", "inscription_txt": "B", "desc": ""},
        }
        citations = [
            stamped({
                "reference_work": "ASCC6",
                "subject_type": "MARKING",
                "subject_id": "TM1",
                "citation_detail": "",
            }),
            stamped({
                "reference_work": "ASCC6",
                "subject_type": "MARKING",
                "subject_id": "AX1",
                "citation_detail": "",
            }),
        ]
        warnings = []

        out = v1_bundle_overlay.append_massachusetts_bpm_metadata(
            "MA",
            raw_rows,
            source_map_rows,
            markings_by_id,
            citations,
            [{"code": "ASCC6"}, {"code": "BPM2"}],
            AUDIT,
            warnings,
        )

        self.assertEqual(v1_massachusetts.boston_catalog_head(raw_rows["17621"]), "BOSTON")
        self.assertEqual(v1_massachusetts.boston_catalog_head(raw_rows["17706"]), "BOSTON./52")
        self.assertEqual(v1_massachusetts.boston_catalog_head(raw_rows["17415"]), "B")
        self.assertEqual(markings_by_id["TM1"]["desc"], "BPM illustration: 87")
        self.assertEqual(markings_by_id["AX1"]["desc"], "BPM illustration: 103")
        self.assertEqual(
            markings_by_id["TM2"]["desc"],
            "On reverse\nBPM illustrations: 652,3,4; Also 55,58 & 59",
        )
        self.assertEqual(
            markings_by_id["TM5"]["desc"],
            "5 stars at bottom\nBPM illustration: 202",
        )
        observed = {
            (
                row["reference_work"],
                row["subject_id"],
                row["citation_detail"],
            )
            for row in out
        }
        self.assertIn(("ASCC6", "TM1", ""), observed)
        self.assertIn(("ASCC6", "AX1", ""), observed)
        self.assertIn(("BPM2", "TM1", "illustration 87"), observed)
        self.assertIn(("BPM2", "AX1", "illustration 103"), observed)
        self.assertIn(
            ("BPM2", "TM2", "illustrations 652,3,4; Also 55,58 & 59"),
            observed,
        )
        self.assertIn(("BPM2", "TM5", "illustration 202"), observed)
        self.assertIn(("BPM2", "TM3", ""), observed)
        self.assertIn(("BPM2", "TM4", "illustrations 1 and others"), observed)
        self.assertEqual(warnings, [])

    def test_attach_images_does_not_copy_refs_to_color_fanout_townmarks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            image_root = root / "v1_images"
            media_dir = root / "media" / "va"
            refs_path = root / "image_refs.csv"
            report_path = bundle / "v1_pipeline_warnings.csv"
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
                bundle / "source_marking_map.csv",
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
                "--warnings", str(report_path),
            ])
            images = read_csv(bundle / "images.csv")
            report = read_csv(report_path)

        self.assertEqual(rc, 0)
        self.assertEqual([r["subject_id"] for r in images], ["ASCC2-VA-M1100"])
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
            report_path = bundle / "v1_pipeline_warnings.csv"

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
                bundle / "source_marking_map.csv",
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
                v1_bundle_overlay.WARNING_COLUMNS,
                [{"raw_id": "71", "issue": "missing_image_file", "detail": "missing.png"}],
            )

            rc = v1_bundle_overlay.main([
                "--state", "VA",
                "--slice", str(slice_path),
                "--image-refs", str(refs_path),
                "--bundle-dir", str(bundle),
                "--v1-image-root", str(image_root),
                "--media-dir", str(media_dir),
                "--warnings", str(report_path),
                "--preserve-images",
            ])
            images = read_csv(bundle / "images.csv")
            markings = read_csv(bundle / "markings.csv")
            report = read_csv(report_path)

        self.assertEqual(rc, 0)
        self.assertEqual({r["color"] for r in markings}, {"BLACK", "BLUE"})
        self.assertEqual({r["subject_id"] for r in images}, {"ASCC2-VA-M1100"})
        self.assertEqual({r["storage_filename"] for r in images}, {"va/kept.png"})
        self.assertIn("missing_image_file", {r["issue"] for r in report})
        self.assertIn("unsupported_column", {r["issue"] for r in report})

    def test_overlay_repairs_inherited_manuscript_when_v1_false_has_no_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            slice_path = root / "slice.csv"
            refs_path = root / "image_refs.csv"
            report_path = bundle / "v1_pipeline_warnings.csv"

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
                bundle / "source_marking_map.csv",
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
                "--warnings", str(report_path),
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
