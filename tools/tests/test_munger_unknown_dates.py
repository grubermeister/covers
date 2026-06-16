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


if __name__ == '__main__':
    unittest.main()
