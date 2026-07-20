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
from munger.rate_assembly import parse_rate_amount


class TestRateParsing(unittest.TestCase):

    def test_c_is_not_a_roman_rate_amount(self):
        self.assertIsNone(parse_rate_token('C')['rate_amount_raw'])
        self.assertEqual(parse_rate_amount('C'), (None, False))

    def test_neg_bracket_sets_negative_impression(self):
        parsed = parse_rate_token('PD 3[neg]')

        self.assertEqual(parsed['rate_amount_raw'], '3')
        self.assertIsNone(parsed['rate_bracket'])
        self.assertEqual(parsed['rate_impression'], 'Negative')

    def test_stencil_prefix_sets_stencil_impression(self):
        parsed = parse_rate_token('stencil 5')

        self.assertEqual(parsed['rate_amount_raw'], '5')
        self.assertEqual(parsed['rate_inscription_raw'], '5')
        self.assertEqual(parsed['rate_impression'], 'Stencil')
        self.assertEqual(classify_paren_field('stencil 5'), 'rate')

    def test_stencil_bracket_sets_stencil_impression(self):
        parsed = parse_rate_token('PD 5[stencil]')

        self.assertEqual(parsed['rate_amount_raw'], '5')
        self.assertIsNone(parsed['rate_bracket'])
        self.assertEqual(parsed['rate_impression'], 'Stencil')

    def test_fractional_rate_token_keeps_fraction_slash(self):
        parsed = parse_rate_token('18-3/4')

        self.assertEqual(parsed['rate_amount_raw'], '18-3/4')
        self.assertEqual(parse_rate_amount(parsed['rate_amount_raw']), (18.75, False))

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
