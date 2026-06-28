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

from ascc_data_munger import assign_post_office_codes


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


if __name__ == "__main__":
    unittest.main()
