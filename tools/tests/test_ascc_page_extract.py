"""Tests for ASCC OCR CSV assembly.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_ascc_page_extract.py'

Expected exit code: 0.
"""

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import ascc_page_extract


class AssembleRowsTests(unittest.TestCase):
    def test_load_region_map_includes_territory_rows(self):
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "regions.csv"
            csv_path.write_text(
                "name,abbrev,region_tier\n"
                "United States of America,USA,COUNTRY\n"
                "Michigan Territory,MIT,TERRITORY\n"
                "West Virginia,WV,STATE\n"
            )

            region_map = ascc_page_extract.load_region_map(csv_path)

        self.assertNotIn("USA", region_map)
        self.assertEqual(region_map["MIT"], "MICHIGAN TERRITORY")
        self.assertEqual(region_map["WV"], "WEST VIRGINIA")

    def test_load_region_name_by_id_uses_exact_region_id(self):
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "regions.csv"
            csv_path.write_text(
                "id,name,abbrev,region_tier\n"
                "25,Michigan,MI,STATE\n"
                "58,Michigan Territory,MI,TERRITORY\n"
            )

            name = ascc_page_extract.load_region_name_by_id(csv_path, "58")

        self.assertEqual(name, "MICHIGAN TERRITORY")

    def test_load_region_name_by_id_fails_for_missing_id(self):
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "regions.csv"
            csv_path.write_text(
                "id,name,abbrev,region_tier\n"
                "56,West Virginia,WV,STATE\n"
            )

            with self.assertRaisesRegex(ValueError, "region id 57 not found"):
                ascc_page_extract.load_region_name_by_id(csv_path, "57")

    def test_derive_state_header_accepts_hyphenated_basename(self):
        region_map = {
            "VA": "VIRGINIA",
            "WV": "WEST VIRGINIA",
        }

        self.assertEqual(
            ascc_page_extract.basename_region_prefix(
                "tools/wip/cache/WV-ASCC-CTLG",
            ),
            "WV",
        )
        self.assertEqual(
            ascc_page_extract.derive_state_header(
                "tools/wip/cache/WV-ASCC-CTLG",
                region_map,
            ),
            "WEST VIRGINIA",
        )
        self.assertEqual(
            ascc_page_extract.derive_state_header(
                "VA_ASCC_CTLG",
                region_map,
            ),
            "VIRGINIA",
        )

    def test_skips_cached_rows_before_expected_state_heading(self):
        chunks = [
            (438, 1, Path("page-0438-0001.png")),
            (438, 2, Path("page-0438-0002.png")),
            (438, 3, Path("page-0438-0003.png")),
        ]
        responses = {
            "page-0438-0001": {
                "images_above": 0,
                "entries": [
                    {"text": "WASHINGTON", "type": "META"},
                    {
                        "text": "Town Postmark Dates Seen Size Color Value",
                        "type": "META",
                    },
                ],
            },
            "page-0438-0002": {
                "images_above": 1,
                "entries": [
                    {
                        "text": "STEILACOOM CITY/W.T.(1850's;C-37;FREE;Black) ... 750.00",
                        "type": "LISTING",
                    },
                ],
            },
            "page-0438-0003": {
                "images_above": 0,
                "entries": [
                    {"text": "WEST\nVIRGINIA", "type": "META"},
                    {"text": "Statehood: June 20, 1863", "type": "META"},
                    {
                        "text": "MARTINSBURG,/W.VA.(1864;26;DUE/3[C];Blue) ... 40.00",
                        "type": "LISTING",
                    },
                ],
            },
        }

        with redirect_stdout(StringIO()):
            rows, dropped_meta, skipped = ascc_page_extract.assemble_rows(
                chunks,
                responses,
                "WEST VIRGINIA",
            )

        self.assertEqual(skipped, 3)
        self.assertEqual(dropped_meta, [])
        self.assertEqual([r["listing_text"] for r in rows], [
            "WEST\nVIRGINIA",
            "Statehood: June 20, 1863",
            "MARTINSBURG,/W.VA.(1864;26;DUE/3[C];Blue) ... 40.00",
        ])

    def test_keeps_all_rows_when_expected_state_heading_is_missing(self):
        chunks = [
            (438, 1, Path("page-0438-0001.png")),
        ]
        responses = {
            "page-0438-0001": {
                "images_above": 0,
                "entries": [
                    {"text": "WASHINGTON", "type": "META"},
                    {"text": "STATEHOOD PERIOD", "type": "META"},
                ],
            },
        }

        with redirect_stdout(StringIO()):
            rows, dropped_meta, skipped = ascc_page_extract.assemble_rows(
                chunks,
                responses,
                "WEST VIRGINIA",
            )

        self.assertEqual(skipped, 0)
        self.assertEqual(dropped_meta, [])
        self.assertEqual([r["listing_text"] for r in rows], [
            "WASHINGTON",
            "STATEHOOD PERIOD",
        ])


if __name__ == "__main__":
    unittest.main()
