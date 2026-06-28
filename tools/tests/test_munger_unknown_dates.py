"""Tests for ASCC unknown-date parenthetical fields.

Run from repo root:
    .venv/bin/python -m unittest \
        tools.tests.test_munger_unknown_dates

Required env vars: none.
Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.fields import classify_all_fields, subparse_fields
from munger.fields.dates import parse_date_field


class TestUnknownDateFields(unittest.TestCase):

    def test_leading_dash_dash_is_unknown_date_not_size(self):
        fields = ['--', '29', '5[C]', 'Blue']
        types = classify_all_fields(fields)
        self.assertEqual(types, ['date', 'size', 'rate', 'color'])

        parsed = subparse_fields({
            'paren_fields': fields,
            'paren_field_types': types,
        })
        self.assertEqual(len(parsed['parsed_dates']), 1)
        self.assertEqual(parsed['parsed_dates'][0]['date_granularity'], 'UNKNOWN')
        self.assertEqual(parsed['parsed_dates'][0]['date_raw'], '--')
        self.assertEqual(parsed['parsed_sizes'][0]['size_dim1'], 29.0)
        self.assertEqual(
            [tok['rate_amount_raw']
             for group in parsed['parsed_rates']
             for tok in group],
            ['5'],
        )

    def test_unknown_dimension_with_date_format_stays_size(self):
        self.assertEqual(classify_all_fields(['1850', '--,YD', 'Black']),
                         ['date', 'size', 'color'])

    def test_parse_unknown_date_shape(self):
        parsed = parse_date_field('--')
        self.assertEqual(parsed, {
            'date_month': None,
            'date_day': None,
            'date_year_start': None,
            'date_year_end': None,
            'date_granularity': 'UNKNOWN',
            'date_is_circa': False,
            'date_raw': '--',
            'date_error': None,
        })

    def test_decade_with_apostrophe(self):
        # Catalog OCR form: "1860's"
        parsed = parse_date_field("1860's")
        self.assertEqual(parsed['date_granularity'], 'DECADE')
        self.assertEqual(parsed['date_year_start'], 1860)
        self.assertEqual(parsed['date_year_end'], 1869)
        self.assertIsNone(parsed['date_error'])

    def test_decade_without_apostrophe(self):
        # DB listing text form: "1860s" (no apostrophe)
        parsed = parse_date_field('1860s')
        self.assertEqual(parsed['date_granularity'], 'DECADE')
        self.assertEqual(parsed['date_year_start'], 1860)
        self.assertEqual(parsed['date_year_end'], 1869)
        self.assertIsNone(parsed['date_error'])

    def test_month_name_year_parses_as_month(self):
        for raw, month in [
            ('Mar. 1852', 3),
            ('March 1852', 3),
            ('Sep. 1852', 9),
            ('Sept. 1852', 9),
            ('September 1852', 9),
        ]:
            with self.subTest(raw=raw):
                parsed = parse_date_field(raw)
                self.assertEqual(parsed['date_granularity'], 'MONTH')
                self.assertEqual(parsed['date_month'], month)
                self.assertEqual(parsed['date_year_start'], 1852)
                self.assertIsNone(parsed['date_day'])

    def test_numeric_month_year_parses_as_month(self):
        parsed = parse_date_field('03 1852')
        self.assertEqual(parsed['date_granularity'], 'MONTH')
        self.assertEqual(parsed['date_month'], 3)
        self.assertEqual(parsed['date_year_start'], 1852)
        self.assertIsNone(parsed['date_day'])

    def test_invalid_numeric_month_year_does_not_fall_back_to_year(self):
        parsed = parse_date_field('13 1852')
        self.assertIsNone(parsed['date_granularity'])
        self.assertEqual(parsed['date_error'], "unparsed date: '13 1852'")

    def test_september_full_date_parses_as_day_not_year(self):
        parsed = parse_date_field('Sept. 17, 1776')
        self.assertEqual(parsed['date_granularity'], 'DAY')
        self.assertEqual(parsed['date_month'], 9)
        self.assertEqual(parsed['date_day'], 17)
        self.assertEqual(parsed['date_year_start'], 1776)


if __name__ == '__main__':
    unittest.main()
