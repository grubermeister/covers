import sys
import unittest
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.classify import TRAILING_VALUE_PATTERN
from munger.head import parse_manuscript_row
from munger.segment import decompose_tail, segment_entry


class TrailingCatalogPeriodTests(unittest.TestCase):
    def test_no_paren_value_allows_catalog_period(self):
        text = "Ware 1827 15."
        self.assertIsNotNone(TRAILING_VALUE_PATTERN.search(text))

        parsed = segment_entry(pd.Series({
            "clean_text": text,
            "entry_form": "no_paren",
        }))

        self.assertTrue(pd.isna(parsed["seg_error"]))
        self.assertEqual(parsed["seg_head"], "Ware 1827")
        self.assertEqual(parsed["seg_tail"], "15")

        tail = decompose_tail(pd.Series({
            "seg_tail": parsed["seg_tail"],
            "entry_form": "no_paren",
        }))
        self.assertEqual(tail["tail_valuation"], "15")

    def test_manuscript_row_value_allows_catalog_period(self):
        parsed = parse_manuscript_row(pd.Series({
            "clean_text": "Ware 1827 15.",
        }))

        self.assertTrue(pd.isna(parsed["seg_error"]))
        self.assertEqual(parsed["seg_head"], "Ware")
        self.assertEqual(parsed["ms_date_text"], "1827")
        self.assertEqual(parsed["seg_tail"], "15")

    def test_decimal_value_keeps_decimal_point(self):
        for text in ["Ware 1827 15.00", "Ware 1827 15.00."]:
            with self.subTest(text=text):
                parsed = parse_manuscript_row(pd.Series({"clean_text": text}))
                self.assertEqual(parsed["seg_tail"], "15.00")

    def test_tail_annotation_with_dangling_dash_value(self):
        tail = decompose_tail(pd.Series({
            "seg_tail": "Used on Dead Letters 7-",
            "entry_form": "semicolon_paren",
        }))

        self.assertTrue(pd.isna(tail["tail_error"]))
        self.assertEqual(tail["tail_annotation"], "Used on Dead Letters")
        self.assertEqual(tail["tail_valuation"], "7-")

    def test_plus_marker_before_tail_value_is_ignored(self):
        tail = decompose_tail(pd.Series({
            "seg_tail": "+ 750",
            "entry_form": "simple_paren",
        }))

        self.assertTrue(pd.isna(tail["tail_error"]))
        self.assertTrue(pd.isna(tail["tail_annotation"]))
        self.assertEqual(tail["tail_valuation"], "750")


if __name__ == "__main__":
    unittest.main()
