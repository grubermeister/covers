"""Regression tests for deterministic ASCC image extraction.

Run from repo root:
    PYTHONPATH=tools python -m unittest tools.tests.test_ascc_image_extract

Expected exit code: 0.
"""

import unittest

from PIL import Image, ImageDraw

from ascc_image_extract import (
    block_has_listing_leader,
    split_chunk,
)


def _draw_listing_row(draw, y, width):
    """Draw one synthetic ASCC listing row with dot leaders and a value."""
    draw.rectangle((90, y, 350, y + 30), fill=0)
    for x in range(455, 855, 16):
        draw.rectangle((x, y + 15, x + 2, y + 17), fill=0)
    draw.rectangle((900, y, 960, y + 30), fill=0)
    draw.point((width - 1, y), fill=255)


def _synthetic_chunk():
    """Return a chunk whose largest gap is inside catalog text.

    Shape:
        block 1: marking illustration
        block 2: catalog listing row with dot leaders
        block 3: later catalog heading/text after a larger blank gap
    """
    width = 1000
    im = Image.new("L", (width, 330), 255)
    draw = ImageDraw.Draw(im)
    draw.rectangle((280, 10, 720, 54), fill=0)
    _draw_listing_row(draw, 104, width)
    draw.rectangle((320, 245, 680, 275), fill=0)
    return im


def _synthetic_wrapped_listing_chunk():
    """Return a chunk with a wrapped listing above its leader row."""
    width = 1000
    im = Image.new("L", (width, 330), 255)
    draw = ImageDraw.Draw(im)
    draw.rectangle((380, 10, 620, 180), fill=0)
    draw.rectangle((30, 220, 725, 248), fill=0)
    _draw_listing_row(draw, 255, width)
    return im


class TestAsccImageExtract(unittest.TestCase):

    def test_listing_leader_detector_ignores_plain_marking_block(self):
        im = _synthetic_chunk()
        self.assertFalse(block_has_listing_leader(im, 10, 54))
        self.assertTrue(block_has_listing_leader(im, 104, 134))

    def test_split_chunk_prefers_first_listing_over_largest_gap(self):
        im = _synthetic_chunk()
        cut_y, illus_count, status, notes = split_chunk(
            im, expected=1, verbose=False, label="synthetic",
        )
        self.assertEqual(status, "ok")
        self.assertEqual(illus_count, 1)
        self.assertLess(cut_y, 104)
        self.assertIn("first listing-leader text block", notes)

    def test_split_chunk_cuts_above_wrapped_listing_line(self):
        im = _synthetic_wrapped_listing_chunk()
        cut_y, illus_count, status, notes = split_chunk(
            im, expected=1, verbose=False, label="synthetic-wrap",
        )
        self.assertEqual(status, "ok")
        self.assertEqual(illus_count, 1)
        self.assertLess(cut_y, 220)
        self.assertIn("first listing-leader text block", notes)


if __name__ == "__main__":
    unittest.main()
