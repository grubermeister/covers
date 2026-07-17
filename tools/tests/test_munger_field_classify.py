"""Tests for paren-field classification disambiguation (munger.fields).

Issue #25B: an unknown-size placeholder ("--") in the size position must not
consume the single size slot, or a following bare-number diameter is wrongly
reclassified as a fabricated rate (Amelia: "(--;28;...)" -> 28 read as a rate).

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover -s tools/tests \
        -p 'test_munger_field_classify.py'
"""
import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.fields import classify_all_fields, classify_paren_field
from munger.fields.sizes import parse_size_field


class DashDoesNotConsumeSizeSlot(unittest.TestCase):
    def test_dash_then_diameter_stays_size(self):
        # The Amelia case: unknown year placeholder, then the real circle size.
        self.assertEqual(classify_all_fields(['--', '28']), ['size', 'size'])

    def test_dash_then_diameter_then_rate(self):
        # After the dash is skipped, 28 is the first real size and 5 is a rate.
        self.assertEqual(
            classify_all_fields(['--', '28', '5']), ['size', 'size', 'rate'])

    def test_single_dash_placeholder(self):
        self.assertEqual(classify_all_fields(['-', '28']), ['size', 'size'])

    def test_dash_then_decimal_diameter(self):
        self.assertEqual(classify_all_fields(['--', '32.5']), ['size', 'size'])

    def test_dash_then_rate_magnitude_is_rate(self):
        # PONTIAC "Same(--;2;Red) Drop rate": 2 is a drop rate, not a 2mm circle.
        self.assertEqual(classify_all_fields(['--', '2']), ['size', 'rate'])


class SizeWithAnnotationBracket(unittest.TestCase):
    def test_bare_c_shape_code_is_size_not_rate(self):
        self.assertEqual(classify_paren_field('C'), 'size')
        self.assertEqual(
            classify_all_fields(['C', 'PAID', 'RED']),
            ['size', 'rate', 'color'],
        )

    def test_size_with_trailing_note_bracket_is_size(self):
        # ANNAPOLIS "(Aug. 18, 1775;SL-42x5,MDD[separate hdstp];Red)": the
        # [separate hdstp] bracket tripped RATE_FIELD_RE and produced a bogus
        # 42c ratemark inscribed "SL-42x5" (woco record ASCC6-MD-M1005).
        self.assertEqual(
            classify_paren_field('SL-42x5,MDD[separate hdstp]'), 'size')

    def test_shape_dimension_without_bracket_still_size(self):
        self.assertEqual(classify_paren_field('SL-42x5,MDD'), 'size')

    def test_amount_with_shape_bracket_still_rate(self):
        # Brackets on a rate amount keep disambiguating toward rate.
        self.assertEqual(classify_paren_field('10[DC]'), 'rate')
        self.assertEqual(classify_paren_field('5[C['), 'rate')
        self.assertEqual(classify_paren_field('V[box]'), 'rate')

    def test_rate_keyword_outranks_size_signature(self):
        self.assertEqual(classify_paren_field('SL-30 PAID'), 'rate')

    def test_stencil_shape_dimension_is_townmark_size(self):
        self.assertEqual(classify_paren_field('stencil C-31'), 'size')
        parsed = parse_size_field('stencil C-31')
        self.assertEqual(parsed['size_shape_code'], 'C')
        self.assertEqual(parsed['size_dim1'], 31.0)
        self.assertEqual(parsed['size_impression'], 'Stencil')

    def test_stencil_amount_stays_rate(self):
        self.assertEqual(classify_paren_field('stencil 5'), 'rate')


class ExistingBehaviorPreserved(unittest.TestCase):
    def test_second_bare_number_is_rate(self):
        # Regression: a genuine second bare number (no dash) is still a rate.
        self.assertEqual(classify_all_fields(['28', '5']), ['size', 'rate'])

    def test_lone_diameter_is_size(self):
        self.assertEqual(classify_all_fields(['27']), ['size'])


if __name__ == '__main__':
    unittest.main()
