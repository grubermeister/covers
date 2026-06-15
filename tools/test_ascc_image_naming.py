"""Tests for the ASCC extracted-image naming contract.

Run from repo root:
    .venv/bin/python -m unittest discover -s tools -p 'test_ascc_image_naming.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from munger.images import (
    catalog_image_slug,
    catalog_region_abbrev,
    image_filename,
    region_media_slug,
)


class TestAsccImageNaming(unittest.TestCase):

    def test_catalog_region_abbrev_uses_first_two_stem_chars(self):
        self.assertEqual(catalog_region_abbrev("VA_ASCC_CTLG"), "VA")
        self.assertEqual(catalog_region_abbrev("WV-ASCC-CTLG"), "WV")
        self.assertEqual(
            catalog_region_abbrev("tools/wip/out/WV-ASCC-CTLG.csv"),
            "WV",
        )

    def test_catalog_image_slug_matches_munger_media_subdir(self):
        self.assertEqual(catalog_image_slug("VA_ASCC_CTLG"), "va")
        self.assertEqual(catalog_image_slug("WV-ASCC-CTLG"), "wv")
        self.assertEqual(region_media_slug("wv"), "wv")

    def test_image_filename_uses_region_slug_not_full_catalog_stem(self):
        self.assertEqual(image_filename("wv", 438, 3, 1), "wv-438-3-1.png")
        self.assertEqual(
            image_filename("VA", "419", "22", "1"),
            "va-419-22-1.png",
        )

    def test_invalid_catalog_stem_fails_early(self):
        with self.assertRaises(ValueError):
            catalog_region_abbrev("1_ASCC_CTLG")


if __name__ == "__main__":
    unittest.main()
