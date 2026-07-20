"""Tests for the trailing-date peel on town-table headings (munger.head).

Covers the decade-suffix range forms found in the MD manuscript table
("Harford 1780's-1832", "Fred(erick)Town 1780's-1820's"), which the
original MS_DATE_AT_END only allowed on the LAST element of a range.

Run from repo root:
    .venv/bin/python -m unittest discover -s tools -p 'test_munger_head_dates.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ascc_data_munger import format_dates_seen_desc
from munger.head import MS_DATE_AT_END, parse_head


def _head_row(seg_head):
    return pd.Series({'seg_head': seg_head, 's1_relationship': False})


def _parsed_name(seg_head):
    return parse_head(_head_row(seg_head))['head_name_body']


def _parsed_head_date(seg_head):
    return parse_head(_head_row(seg_head))['head_date_text']


def _has_leading_star(seg_head):
    return bool(parse_head(_head_row(seg_head))['head_has_leading_star'])


class TestMsDateAtEnd(unittest.TestCase):

    def test_decade_suffix_on_first_range_element(self):
        # MD manuscript-table forms that previously failed to peel.
        for text, date in [
            ("Harford 1780's-1832", "1780's-1832"),
            ("Queenstown 1780's-1850's", "1780's-1850's"),
            ("Fredtown 1780s-1820s", "1780s-1820s"),
        ]:
            m = MS_DATE_AT_END.search(text)
            self.assertIsNotNone(m, text)
            self.assertEqual(m.group(1), date)

    def test_existing_forms_still_match(self):
        # VA baseline forms (regression guard for the generalization).
        for text, date in [
            ("Accomack C.H 1835", "1835"),
            ("Aquia 1811,1849-55", "1811,1849-55"),
            ("Arbor Hill 1850's", "1850's"),
            ("Yorktown 1824,1830,1850", "1824,1830,1850"),
            ("Yorktown Mar. 1852", "Mar. 1852"),
            ("Yorktown 03 1852", "03 1852"),
        ]:
            m = MS_DATE_AT_END.search(text)
            self.assertIsNotNone(m, text)
            self.assertEqual(m.group(1), date)

    def test_embedded_year_not_peeled_without_separator(self):
        self.assertIsNone(MS_DATE_AT_END.search("Mills1835"))


class TestParseHeadPeel(unittest.TestCase):

    def test_peels_decade_range_from_town_heading(self):
        self.assertEqual(_parsed_name("Harford 1780's-1832"), "Harford")
        self.assertEqual(_parsed_name("Queenstown 1780's-1850's"), "Queenstown")

    def test_plain_heading_unchanged(self):
        self.assertEqual(_parsed_name("Accomack C.H 1835"), "Accomack C.H")

    def test_preserves_peeled_date_text_for_description(self):
        self.assertEqual(_parsed_name("Aquia(s) 1811,1849-55"), "Aquia")
        self.assertEqual(_parsed_head_date("Aquia(s) 1811,1849-55"),
                         "1811,1849-55")
        self.assertEqual(format_dates_seen_desc("1811,1849-55"),
                         "Dates Seen 1811, 1849-55")

    def test_peels_month_year_from_town_heading(self):
        self.assertEqual(_parsed_name("Yorktown Mar. 1852"), "Yorktown")
        self.assertEqual(_parsed_head_date("Yorktown Mar. 1852"), "Mar. 1852")
        self.assertEqual(_parsed_name("Yorktown 03 1852"), "Yorktown")
        self.assertEqual(_parsed_head_date("Yorktown 03 1852"), "03 1852")

    def test_earliest_marker_annotation_does_not_remain_in_heading(self):
        parsed = parse_head(_head_row("Yorktown(E) Mar. 1852"))
        self.assertEqual(parsed["head_name_body"], "Yorktown")
        self.assertEqual(parsed["head_date_text"], "Mar. 1852")
        self.assertEqual(parsed["head_annotations"], ["E"])

    def test_only_leading_star_sets_catalog_marker(self):
        self.assertTrue(_has_leading_star("*RICHMOND/VA."))
        self.assertTrue(_has_leading_star("  *RICHMOND/VA."))
        self.assertFalse(_has_leading_star("RICHMOND/*VA.*"))
        self.assertFalse(_has_leading_star("RICHMOND/VA.*"))
        self.assertEqual(_parsed_name("*RICHMOND/VA."), "RICHMOND/VA.")
        self.assertEqual(_parsed_name("RICHMOND/*VA.*"), "RICHMOND/*VA.*")


if __name__ == '__main__':
    unittest.main()
