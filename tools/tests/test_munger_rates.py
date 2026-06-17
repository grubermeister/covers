"""Tests for ASCC rate field parsing.

Run from repo root:
    .venv/bin/python -m unittest discover -s tools -p 'test_munger_rates.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.fields import classify_paren_field, subparse_fields
from munger.fields.rates import RATE_BRACKET_RE, parse_rate_token, split_rate_tokens


class TestRateParsing(unittest.TestCase):

    def test_rate_bracket_ocr_variants_parse_shape_hint(self):
        for raw in ['DUE/4[C]', 'DUE/4[C}', 'DUE/4{C]', 'DUE/4|C]',
                    'DUE/4[C|', 'DUE/4|C|']:
            with self.subTest(raw=raw):
                parsed = parse_rate_token(raw)
                self.assertEqual(parsed['rate_keyword'], 'DUE')
                self.assertEqual(parsed['rate_amount_raw'], '4')
                self.assertEqual(parsed['rate_bracket'], 'C')
                self.assertEqual(RATE_BRACKET_RE.sub('', raw), 'DUE/4')

    def test_split_rate_tokens_respects_ocr_brackets(self):
        self.assertEqual(split_rate_tokens('PAID/3|arc|,FREE'),
                         ['PAID/3|arc|', 'FREE'])

    def test_opening_square_bracket_can_close_rate_hint(self):
        parsed = parse_rate_token('PAID/5[C[')

        self.assertEqual(parsed['rate_keyword'], 'PAID')
        self.assertEqual(parsed['rate_amount_raw'], '5')
        self.assertEqual(parsed['rate_bracket'], 'C')
        self.assertEqual(RATE_BRACKET_RE.sub('', 'PAID/5[C['), 'PAID/5')

    def test_split_rate_tokens_respects_opening_bracket_closer(self):
        self.assertEqual(split_rate_tokens('PAID/3[C],5,10,PAID/5[C['),
                         ['PAID/3[C]', '5', '10', 'PAID/5[C['])

    def test_bare_rate_with_opening_bracket_closer_is_rate_field(self):
        self.assertEqual(classify_paren_field('5[C['), 'rate')

        row = {
            'paren_fields': ['5[C['],
            'paren_field_types': ['rate'],
        }
        parsed = subparse_fields(row)
        self.assertEqual(parsed['parsed_rates'][0][0]['rate_amount_raw'], '5')
        self.assertEqual(parsed['parsed_rates'][0][0]['rate_bracket'], 'C')


if __name__ == '__main__':
    unittest.main()
