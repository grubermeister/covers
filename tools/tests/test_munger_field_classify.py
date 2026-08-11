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

from munger.fields import classify_all_fields, classify_paren_field, subparse_fields
from munger.fields.sizes import parse_size_field
from munger.assembly import resolve_effective_shape, resolve_shape_name


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

    def test_arc_unknown_dimension_with_nor_is_size(self):
        self.assertEqual(classify_paren_field('arc--,NOR'), 'size')
        parsed = parse_size_field('arc--,NOR')
        self.assertEqual(parsed['size_shape_code'], 'ARC')
        self.assertIsNone(parsed['size_dim1'])
        self.assertEqual(parsed['size_qualifier'], 'NOR')

    def test_modifier_prefixed_shape_dimension_is_best_effort_size(self):
        self.assertEqual(classify_paren_field('framed arc-32x19'), 'size')

        parsed = parse_size_field('framed arc-32x19')
        self.assertEqual(parsed['size_shape_code'], 'ARC')
        self.assertEqual(parsed['size_dim1'], 32.0)
        self.assertEqual(parsed['size_dim2'], 19.0)
        self.assertEqual(parsed['size_desc_note'], 'framed arc')
        self.assertIsNone(parsed['size_error'])

        code, source = resolve_effective_shape({
            'parsed_sizes': [parsed],
            'Default Shape': None,
            'is_manuscript_section': False,
            'is_manuscript': False,
        })
        self.assertEqual(code, 'ARC')
        self.assertEqual(source, 'paren_body')
        self.assertEqual(resolve_shape_name(code)[0], 'ARC - Arc or Semi-circle')

    def test_comma_diameter_list_is_best_effort_circle_size(self):
        self.assertEqual(classify_paren_field('31,32'), 'size')

        parsed = parse_size_field('31,32')
        self.assertIsNone(parsed['size_shape_code'])
        self.assertEqual(parsed['size_dim1'], 31.0)
        self.assertIsNone(parsed['size_dim2'])
        self.assertEqual(parsed['size_desc_note'], 'Sizes: 31,32')
        self.assertIsNone(parsed['size_error'])

        code, source = resolve_effective_shape({
            'parsed_sizes': [parsed],
            'Default Shape': None,
            'is_manuscript_section': False,
            'is_manuscript': False,
        })
        self.assertEqual(code, 'C')
        self.assertEqual(source, 'bare_diameter')
        self.assertEqual(resolve_shape_name(code)[0], 'C - Circle')


class ExistingBehaviorPreserved(unittest.TestCase):
    def test_second_bare_number_is_rate(self):
        # Regression: a genuine second bare number (no dash) is still a rate.
        self.assertEqual(classify_all_fields(['28', '5']), ['size', 'rate'])

    def test_lone_diameter_is_size(self):
        self.assertEqual(classify_all_fields(['27']), ['size'])

    def test_small_comma_rate_list_is_not_size(self):
        self.assertNotEqual(classify_paren_field('5,10'), 'size')
        self.assertIsNotNone(parse_size_field('5,10')['size_error'])


class CompoundColorClassification(unittest.TestCase):
    def test_space_compound_colors_are_color_fields(self):
        examples = [
            ('Red brown', ['RED BROWN']),
            ('Black brown,Red brown', ['BLACK BROWN', 'RED BROWN']),
            ('Gray brown,Red brown', ['GRAY BROWN', 'RED BROWN']),
            ('Olive green,Pink,Red orange', ['OLIVE GREEN', 'PINK', 'RED ORANGE']),
            ('Bright green', ['BRIGHT GREEN']),
            ('Purplish,Red brown', ['PURPLISH', 'RED BROWN']),
        ]
        for field, expected in examples:
            with self.subTest(field=field):
                self.assertEqual(classify_paren_field(field), 'color')
                parsed = subparse_fields({
                    'paren_fields': [field],
                    'paren_field_types': ['color'],
                    'Manuscript': '',
                })
                self.assertEqual(parsed['parsed_colors'], expected)
                self.assertEqual(parsed['other_fields'], [])

    def test_dc_color_slot_does_not_leave_color_in_other_fields(self):
        fields = ['1802-04', '28', 'FREE,PAID', 'Red brown']
        parsed = subparse_fields({
            'paren_fields': fields,
            'paren_field_types': classify_all_fields(fields),
            'Manuscript': '',
        })
        self.assertEqual(parsed['parsed_colors'], ['RED BROWN'])
        self.assertEqual(parsed['other_fields'], [])


if __name__ == '__main__':
    unittest.main()
