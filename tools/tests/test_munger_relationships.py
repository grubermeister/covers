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

from munger.relationships import (
    resolve_relationships,
    roll_up_catalog_text,
    strip_inscription_markers,
)


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

    def test_strip_inscription_preserves_multi_star_device_text(self):
        self.assertEqual(
            strip_inscription_markers("ABINGDON/*VA.*"),
            "ABINGDON/*VA.*",
        )
        self.assertEqual(strip_inscription_markers("*BETHANY/Va."), "BETHANY/Va.")
        self.assertEqual(strip_inscription_markers("BETHANY/Va.*"), "BETHANY/Va.")

    def test_independent_inscription_preserves_internal_stars(self):
        listings = pd.DataFrame(
            [
                {
                    "head_rel_type": None,
                    "head_name_body": "ABINGDON/*VA.*",
                    "Default Shape": "C",
                },
            ]
        )

        resolved = resolve_relationships(listings)

        self.assertEqual(resolved.loc[0, "resolved_inscription"], "ABINGDON/*VA.*")
        self.assertEqual(resolved.loc[0, "resolved_town"], "ABINGDON")

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

    def test_bare_same_inherits_immediate_same_suffix_sibling(self):
        listings = pd.DataFrame(
            [
                {
                    "head_rel_type": None,
                    "head_name_body": "ABINGDON/*VA.*",
                    "Default Shape": "C",
                },
                {
                    "head_rel_type": "Same",
                    "head_name_body": "/VA.",
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

        self.assertEqual(resolved.loc[0, "resolved_inscription"], "ABINGDON/*VA.*")
        self.assertEqual(resolved.loc[1, "resolved_inscription"], "ABINGDON/VA.")
        self.assertEqual(resolved.loc[2, "resolved_inscription"], "ABINGDON/VA.")
        self.assertEqual(resolved.loc[2, "prev_sibling_idx"], 1)

    def test_same_suffix_uses_immediate_same_suffix_sibling_stem(self):
        listings = pd.DataFrame(
            [
                {
                    "head_rel_type": None,
                    "head_name_body": "ALEXANDRIA/Va.",
                    "Default Shape": "C",
                },
                {
                    "head_rel_type": "Same",
                    "head_name_body": "VA./5",
                    "Default Shape": "C",
                },
                {
                    "head_rel_type": "Same",
                    "head_name_body": "/VA.",
                    "Default Shape": "C",
                },
            ]
        )

        resolved = resolve_relationships(listings)

        self.assertEqual(resolved.loc[1, "resolved_inscription"], "ALEXANDRIA VA./5")
        self.assertEqual(resolved.loc[2, "resolved_inscription"], "ALEXANDRIA VA./VA.")

    def test_rollup_dedupes_identical_parent_and_child_text(self):
        listings = pd.DataFrame(
            [
                {"clean_text": "RICHMOND (1850;Black) 10", "parent_idx": None},
                {"clean_text": "RICHMOND (1850;Black) 10", "parent_idx": 0},
            ]
        )

        rolled = roll_up_catalog_text(listings)

        self.assertEqual(
            rolled.loc[0, "rolled_catalog_text"],
            "RICHMOND (1850;Black) 10",
        )
        self.assertEqual(
            rolled.loc[1, "rolled_catalog_text"],
            "RICHMOND (1850;Black) 10",
        )

    def test_rollup_dedupes_identical_sibling_text(self):
        listings = pd.DataFrame(
            [
                {"clean_text": "RICHMOND (1850;Black) 10", "parent_idx": None},
                {"clean_text": "Same (1851;Blue) 12", "parent_idx": 0},
                {"clean_text": "Same (1851;Blue) 12", "parent_idx": 0},
            ]
        )

        rolled = roll_up_catalog_text(listings)

        self.assertEqual(
            rolled.loc[0, "rolled_catalog_text"],
            "RICHMOND (1850;Black) 10\nSame (1851;Blue) 12",
        )
        self.assertEqual(
            rolled.loc[1, "rolled_catalog_text"],
            "RICHMOND (1850;Black) 10\nSame (1851;Blue) 12",
        )
        self.assertEqual(
            rolled.loc[2, "rolled_catalog_text"],
            "RICHMOND (1850;Black) 10\nSame (1851;Blue) 12",
        )

    def test_rollup_preserves_distinct_family_lines_in_order(self):
        listings = pd.DataFrame(
            [
                {"clean_text": "RICHMOND (1850;Black) 10", "parent_idx": None},
                {"clean_text": "Same (1851;Blue) 12", "parent_idx": 0},
                {"clean_text": "Same (1852;Red) 15", "parent_idx": 0},
            ]
        )

        rolled = roll_up_catalog_text(listings)

        self.assertEqual(
            rolled.loc[0, "rolled_catalog_text"],
            "RICHMOND (1850;Black) 10\nSame (1851;Blue) 12\nSame (1852;Red) 15",
        )

    def test_rollup_preserves_first_source_formatting(self):
        listings = pd.DataFrame(
            [
                {"clean_text": "RICHMOND   (1850;Black) 10", "parent_idx": None},
                {"clean_text": " RICHMOND (1850;Black)   10 ", "parent_idx": 0},
            ]
        )

        rolled = roll_up_catalog_text(listings)

        self.assertEqual(
            rolled.loc[0, "rolled_catalog_text"],
            "RICHMOND   (1850;Black) 10",
        )


if __name__ == "__main__":
    unittest.main()
