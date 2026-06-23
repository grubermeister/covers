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

from munger.assembly import resolve_effective_shape


def _size(dim1=None, dim2=None, shape=None):
    return {'size_dim1': dim1, 'size_dim2': dim2, 'size_shape_code': shape}


def _row(sizes, default=None, ms_section=False):
    return {'parsed_sizes': sizes, 'Default Shape': default,
            'is_manuscript_section': ms_section}


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
    def test_wxh_falls_back_to_sl(self):
        # A two-dimension mark is not a circle; no default -> SL fallback.
        code, src = resolve_effective_shape(_row([_size(dim1=20, dim2=11)]))
        self.assertEqual(code, 'SL')
        self.assertEqual(src, 'catalog_fallback')

    def test_explicit_shape_code_wins(self):
        code, src = resolve_effective_shape(_row([_size(dim1=29, shape='DC')]))
        self.assertEqual(code, 'DC')
        self.assertEqual(src, 'paren_body')

    def test_manuscript_section_has_no_shape(self):
        code, _ = resolve_effective_shape(_row([_size(dim1=27)], ms_section=True))
        self.assertIsNone(code)

    def test_explicit_default_shape_beats_bare_when_no_diameter(self):
        # No bare diameter present -> section default still applies.
        code, src = resolve_effective_shape(_row([], default='Circle'))
        self.assertEqual(code, 'C')
        self.assertEqual(src, 'default_shape')


if __name__ == '__main__':
    unittest.main()
