"""Tests for catalog relationship resolution.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_munger_relationships.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.relationships import resolve_relationships


class ResolveRelationshipsTests(unittest.TestCase):
    def test_independent_inscription_drops_catalog_markers(self):
        listings = pd.DataFrame(
            [
                {
                    "head_rel_type": None,
                    "head_name_body": "(1)*BETHANY/Va.",
                    "Default Shape": "C",
                },
            ]
        )

        resolved = resolve_relationships(listings)

        self.assertEqual(resolved.loc[0, "resolved_inscription"], "BETHANY/Va.")

    def test_same_suffix_uses_parent_townmark_text(self):
        cases = [
            ("WHITE-WATER/Wis.", "/Wis.", "WHITE-WATER/Wis."),
            ("(1)WHITE-WATER/Wis.", "/Wis.", "WHITE-WATER/Wis."),
            ("WINCHESTER.VA", "VA./5", "WINCHESTER VA./5"),
            ("*WINCHESTER.VA", "VA./5", "WINCHESTER VA./5"),
            ("WINCHESTER.VA", "*VA./5", "WINCHESTER VA./5"),
        ]
        for parent_text, suffix, expected in cases:
            with self.subTest(parent_text=parent_text, suffix=suffix):
                listings = pd.DataFrame(
                    [
                        {
                            "head_rel_type": None,
                            "head_name_body": parent_text,
                            "Default Shape": "C",
                        },
                        {
                            "head_rel_type": "Same",
                            "head_name_body": suffix,
                            "Default Shape": "C",
                        },
                    ]
                )

                resolved = resolve_relationships(listings)

                self.assertEqual(resolved.loc[1, "resolved_inscription"], expected)
                self.assertNotIn("Same", resolved.loc[1, "resolved_inscription"])

    def test_bare_same_inherits_parent_townmark_text(self):
        listings = pd.DataFrame(
            [
                {
                    "head_rel_type": None,
                    "head_name_body": "CABOTVILLE / Ms.",
                    "Default Shape": "C",
                },
                {
                    "head_rel_type": "Same",
                    "head_name_body": None,
                    "Default Shape": "C",
                },
            ]
        )

        resolved = resolve_relationships(listings)

        self.assertEqual(resolved.loc[1, "resolved_inscription"], "CABOTVILLE / Ms.")
        self.assertNotIn("Same", resolved.loc[1, "resolved_inscription"])


if __name__ == "__main__":
    unittest.main()
