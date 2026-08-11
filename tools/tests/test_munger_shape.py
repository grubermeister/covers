"""Tests for shape resolution (munger.assembly.resolve_effective_shape).

Issue #38: a bare single diameter ("27", no shape code, no second dimension)
is a circular datestamp and must resolve to Circle, not the SL catalog
fallback (ADA.MI "(1837-45;27;Red)" was defaulting to SL).

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover -s tools/tests \
        -p 'test_munger_shape.py'
"""
import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.assembly import promote_no_paren_to_manuscript, resolve_effective_shape


def _size(dim1=None, dim2=None, shape=None):
    return {'size_dim1': dim1, 'size_dim2': dim2, 'size_shape_code': shape}


def _row(sizes, default=None, ms_section=False, is_manuscript=False):
    return {'parsed_sizes': sizes, 'Default Shape': default,
            'is_manuscript_section': ms_section,
            'is_manuscript': is_manuscript}


class BareDiameterIsCircle(unittest.TestCase):
    def test_bare_diameter(self):
        code, src = resolve_effective_shape(_row([_size(dim1=27)]))
        self.assertEqual(code, 'C')
        self.assertEqual(src, 'bare_diameter')

    def test_bare_diameter_with_dash_placeholder(self):
        # "(--;27;...)": the empty placeholder is ignored, 27 -> circle.
        code, _ = resolve_effective_shape(_row([_size(), _size(dim1=27)]))
        self.assertEqual(code, 'C')


class NonBareUnaffected(unittest.TestCase):
    def test_wxh_without_shape_returns_no_shape(self):
        # A two-dimension mark is not a circle; no default -> no inferred shape.
        code, src = resolve_effective_shape(_row([_size(dim1=20, dim2=11)]))
        self.assertIsNone(code)
        self.assertEqual(src, 'no_shape')

    def test_explicit_shape_code_wins(self):
        code, src = resolve_effective_shape(_row([_size(dim1=29, shape='DC')]))
        self.assertEqual(code, 'DC')
        self.assertEqual(src, 'paren_body')

    def test_manuscript_section_has_no_shape(self):
        code, _ = resolve_effective_shape(_row([_size(dim1=27)], ms_section=True))
        self.assertIsNone(code)

    def test_promoted_manuscript_has_no_shape(self):
        code, src = resolve_effective_shape(_row([], is_manuscript=True))
        self.assertIsNone(code)
        self.assertEqual(src, 'manuscript_no_shape')

    def test_explicit_default_shape_beats_bare_when_no_diameter(self):
        # No bare diameter present -> section default still applies.
        code, src = resolve_effective_shape(_row([], default='Circle'))
        self.assertEqual(code, 'C')
        self.assertEqual(src, 'default_shape')


class NoParenManuscriptPromotion(unittest.TestCase):
    def test_short_no_paren_without_size_promotes(self):
        row = {
            'entry_form': 'no_paren',
            'seg_head': 'Paid',
            'clean_text': 'Paid 5',
            'parsed_sizes': [],
            'parsed_colors': ['BLACK'],
            'is_manuscript': False,
        }
        self.assertTrue(promote_no_paren_to_manuscript(row))
        row['is_manuscript'] = True
        row['parsed_colors'] = []
        code, src = resolve_effective_shape(row)
        self.assertIsNone(code)
        self.assertEqual(src, 'manuscript_no_shape')
        self.assertEqual(row['parsed_colors'], [])

    def test_long_no_paren_without_size_does_not_promote(self):
        row = {
            'entry_form': 'no_paren',
            'seg_head': 'New Martinsville West Virginia',
            'clean_text': 'New Martinsville West Virginia 10',
            'parsed_sizes': [],
            'parsed_colors': ['BLACK'],
            'is_manuscript': False,
        }
        self.assertFalse(promote_no_paren_to_manuscript(row))
        code, src = resolve_effective_shape(row)
        self.assertIsNone(code)
        self.assertEqual(src, 'no_shape')
        self.assertEqual(row['parsed_colors'], ['BLACK'])

    def test_inherited_size_prevents_short_no_paren_promotion(self):
        row = {
            'entry_form': 'no_paren',
            'seg_head': 'Paid',
            'clean_text': 'Paid 5',
            'parsed_sizes': [_size(dim1=27)],
            'is_manuscript': False,
        }
        self.assertFalse(promote_no_paren_to_manuscript(row))


if __name__ == '__main__':
    unittest.main()
