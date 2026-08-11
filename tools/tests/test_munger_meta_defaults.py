"""Tests for META-driven listing defaults.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover -s tools/tests -p 'test_munger_*.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.io import apply_meta_listing_defaults


def _df(rows, columns=None):
    return pd.DataFrame(rows, columns=columns or ['Listing', 'Type'])


class TestApplyMetaListingDefaults(unittest.TestCase):

    def test_missing_optional_columns_and_ordered_switches(self):
        df = _df([
            ('INTRODUCTION', 'META'),
            ('Before defaults', 'LISTING'),
            ('(Circle handstamps unless otherwise noted: SL - straightline)', 'META'),
            ('Circle default row', 'LISTING'),
            ('Town Postmark Dates Seen Size Color Value', 'META'),
            ('Circle continues row', 'LISTING'),
            ('MANUSCRIPT TOWN MARKS', 'META'),
            ('Manuscript row', 'LISTING'),
            ('Circle handstamps unless otherwise noted', 'META'),
            ('Circle restored row', 'LISTING'),
        ])

        out = apply_meta_listing_defaults(df)

        self.assertIn('Manuscript', out.columns)
        self.assertIn('Default Shape', out.columns)
        self.assertEqual(out.loc[1, 'Default Shape'], '')
        self.assertEqual(out.loc[1, 'Manuscript'], '')
        self.assertEqual(out.loc[3, 'Default Shape'], 'C - Circle')
        self.assertEqual(out.loc[3, 'Manuscript'], '')
        self.assertEqual(out.loc[5, 'Default Shape'], 'C - Circle')
        self.assertEqual(out.loc[7, 'Default Shape'], '')
        self.assertEqual(out.loc[7, 'Manuscript'], 'Yes')
        self.assertEqual(out.loc[9, 'Default Shape'], 'C - Circle')
        self.assertEqual(out.loc[9, 'Manuscript'], '')

    def test_explicit_listing_values_are_preserved(self):
        df = _df([
            ('Circle handstamps unless otherwise noted', 'META', '', ''),
            ('Explicit oval row', 'LISTING', '', 'O - Oval'),
            ('Explicit manuscript row', 'LISTING', 'Yes', ''),
            ('MANUSCRIPT TOWN MARKS', 'META', '', ''),
            ('Explicit non-manuscript row', 'LISTING', 'No', ''),
        ], columns=['Listing', 'Type', 'Manuscript', 'Default Shape'])

        out = apply_meta_listing_defaults(df)

        self.assertEqual(out.loc[1, 'Default Shape'], 'O - Oval')
        self.assertEqual(out.loc[1, 'Manuscript'], '')
        self.assertEqual(out.loc[2, 'Manuscript'], 'Yes')
        self.assertEqual(out.loc[2, 'Default Shape'], '')
        self.assertEqual(out.loc[4, 'Manuscript'], 'No')
        self.assertEqual(out.loc[4, 'Default Shape'], '')


if __name__ == '__main__':
    unittest.main()
