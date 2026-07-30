"""Tests for ASCC munger PostOffice.code assignment.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover -s tools/tests \
        -p 'test_munger_post_office_codes.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd


TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ascc_data_munger import assign_post_office_codes, normalize_post_office_town


class PostOfficeCodeAssignmentTests(unittest.TestCase):
    def test_assigns_region_code_serials_in_existing_row_order(self):
        post_offices = pd.DataFrame(
            [
                {"post_office_id": 1, "name": "BOSTON", "state_code": "MA"},
                {"post_office_id": 2, "name": "SALEM", "state_code": "MA"},
                {"post_office_id": 3, "name": "SPRINGFIELD", "state_code": "MA"},
                {"post_office_id": 4, "name": "UNKNOWN", "state_code": "MA"},
            ]
        )

        coded = assign_post_office_codes(post_offices, "USA-MA1")

        self.assertEqual(
            list(coded["code"]),
            ["USA-MA1-1", "USA-MA1-2", "USA-MA1-3", "USA-MA1-4"],
        )

    def test_rejects_blank_region_code(self):
        post_offices = pd.DataFrame(
            [{"post_office_id": 1, "name": "BOSTON", "state_code": "MA"}]
        )

        with self.assertRaisesRegex(ValueError, "region code must be nonblank"):
            assign_post_office_codes(post_offices, " ")


class PostOfficeTownNormalizationTests(unittest.TestCase):
    def test_strips_spaced_descriptive_digit_tails(self):
        self.assertEqual(
            normalize_post_office_town("Newark 1854 with Newark"),
            "NEWARK",
        )
        self.assertEqual(
            normalize_post_office_town("White Oak c1862 on patriotic cover"),
            "WHITE OAK",
        )

    def test_strips_attached_year_tail_from_michigan_v1_row(self):
        self.assertEqual(normalize_post_office_town("Clay1842-43"), "CLAY")

    def test_keeps_existing_punctuation_normalization(self):
        self.assertEqual(normalize_post_office_town("Barnett's"), "BARNETTS")
        self.assertEqual(normalize_post_office_town("B&O"), "B AND O")

    def test_strips_quote_marks_from_florida_v1_rows(self):
        self.assertEqual(normalize_post_office_town('"Pensacola'), "PENSACOLA")
        self.assertEqual(
            normalize_post_office_town("\u201cEast Florida"),
            "EAST FLORIDA",
        )
        self.assertEqual(
            normalize_post_office_town("\u201cFlorida 12 April 1774"),
            "FLORIDA",
        )

    def test_auxmark_only_alabama_heads_route_to_unknown(self):
        for town in [
            "ADVERTISED",
            "ADV",
            "ADV.2",
            "REGISTERED",
        ]:
            with self.subTest(town=town):
                self.assertTrue(pd.isna(normalize_post_office_town(town)))

    def test_no_town_marking_routes_to_unknown(self):
        for town in [
            "(No town marking)",
            "No town marking",
            "No town mark",
        ]:
            with self.subTest(town=town):
                self.assertTrue(pd.isna(normalize_post_office_town(town)))

    def test_auxmark_prefix_inside_real_town_is_preserved(self):
        self.assertEqual(normalize_post_office_town("Advertised Creek"), "ADVERTISED CREEK")


if __name__ == "__main__":
    unittest.main()
