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


if __name__ == '__main__':
    unittest.main()
