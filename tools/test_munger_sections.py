"""Tests for section-driven region assignment (munger.io).

Run from repo root:
    .venv/bin/python -m unittest discover -s tools -p 'test_munger_sections.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.io import assign_section_regions

REGION_SEED = pd.DataFrame([
    {'id': 1,  'name': 'United States of America', 'region_tier': 'COUNTRY'},
    {'id': 25, 'name': 'Michigan',                 'region_tier': 'STATE'},
    {'id': 36, 'name': 'Northwest Territory',      'region_tier': 'TERRITORY'},
    {'id': 51, 'name': 'Virginia',                 'region_tier': 'STATE'},
    {'id': 59, 'name': 'Michigan Territory',       'region_tier': 'TERRITORY'},
    {'id': 60, 'name': 'Indiana Territory',        'region_tier': 'TERRITORY'},
])


def _df(rows):
    return pd.DataFrame(rows, columns=['Listing', 'Type'])


class TestAssignSectionRegions(unittest.TestCase):

    def test_michigan_catalog_sections(self):
        df = _df([
            ('MICHIGAN', 'META'),                       # state heading
            ('BRITISH COLONIAL PERIOD', 'META'),        # no region -> default
            ('"Detroit 30 May 1769" dateline', 'LISTING'),
            ('AS NORTHWEST TERRITORY', 'META'),
            ('*Detroit*(Feb. 14, 1792;SL-21x3,MDD;Black)', 'LISTING'),
            ('AS INDIANA TERRITORY', 'META'),
            ('Detroit(E)(June 1, 1803;Ms;Black) 1750', 'LISTING'),
            ('MICHIGAN TERRITORY', 'META'),
            ('Ann Arbor M.T.(E)(1825;Ms;Black)', 'LISTING'),
            ('MICHIGAN', 'META'),                       # running head mid-section
            ('Green Bay M.T.(1825;Ms;Black)', 'LISTING'),
            ('STATEHOOD PERIOD', 'META'),
            ('ADRIAN/MIC.(1838;C-30;Black)', 'LISTING'),
            ('MANUSCRIPT TOWN MARKS', 'META'),
            ('SCHOOLCRAFT/Mich.(1838-52;30;Black)', 'LISTING'),
        ])
        out = assign_section_regions(df, REGION_SEED, default_region_id=25)
        listing_regions = out[df['Type'] == 'LISTING'].tolist()
        self.assertEqual(listing_regions, [25, 36, 60, 59, 59, 25, 25])

    def test_running_head_does_not_reset_territory_section(self):
        # Load-bearing: the bare state name survives extraction as META on
        # every page; matching it would silently end the territory section.
        df = _df([
            ('MICHIGAN TERRITORY', 'META'),
            ('Pontiac M.T.(1830;Ms;Black)', 'LISTING'),
            ('MICHIGAN', 'META'),
            ('Saginaw M.T.(1835;Ms;Black)', 'LISTING'),
        ])
        out = assign_section_regions(df, REGION_SEED, default_region_id=25)
        self.assertEqual(out[df['Type'] == 'LISTING'].tolist(), [59, 59])

    def test_catalog_without_territory_banners_is_all_default(self):
        # VA-shaped input: nothing matches, every listing keeps the
        # filename-derived region (regression guard for existing catalogs).
        df = _df([
            ('VIRGINIA', 'META'),
            ('Town Postmark Dates Seen Size Color Value', 'META'),
            ('Accomack C.H(1835;Ms;Black)', 'LISTING'),
            ('MANUSCRIPT TOWN MARKS', 'META'),
            ('AQUIA/Va.(1849-55;30;Black)', 'LISTING'),
        ])
        out = assign_section_regions(df, REGION_SEED, default_region_id=51)
        self.assertEqual(out[df['Type'] == 'LISTING'].tolist(), [51, 51])


if __name__ == '__main__':
    unittest.main()
