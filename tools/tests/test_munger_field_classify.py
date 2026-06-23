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

from munger.fields import classify_all_fields


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


class ExistingBehaviorPreserved(unittest.TestCase):
    def test_second_bare_number_is_rate(self):
        # Regression: a genuine second bare number (no dash) is still a rate.
        self.assertEqual(classify_all_fields(['28', '5']), ['size', 'rate'])

    def test_lone_diameter_is_size(self):
        self.assertEqual(classify_all_fields(['27']), ['size'])


if __name__ == '__main__':
    unittest.main()
