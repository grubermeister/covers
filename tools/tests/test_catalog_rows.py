"""Tests for the ASCC catalog-row schema bridge.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_catalog_rows.py'

Expected exit code: 0.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from catalog_rows import (
    CANONICAL_COLUMNS,
    canonicalize_row,
    read_legacy_dataframe,
    write_catalog_rows,
)


class CatalogRowsTests(unittest.TestCase):
    def test_canonicalize_row_maps_legacy_names(self):
        row = canonicalize_row({
            "Listing": "RICHMOND (1850;Black) 10.00",
            "Page": "419",
            "Chunk": "2",
            "Images Above": "1",
            "Type": "LISTING",
        })

        self.assertEqual(row["listing_text"], "RICHMOND (1850;Black) 10.00")
        self.assertEqual(row["catalog_page"], "419")
        self.assertEqual(row["chunk_number"], "2")
        self.assertEqual(row["image_count"], "1")
        self.assertEqual(row["row_type"], "LISTING")

    def test_write_catalog_rows_uses_public_header(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "VA_catalog_rows.csv"
            write_catalog_rows(path, [{
                "listing_text": "A",
                "catalog_page": "1",
                "chunk_number": "2",
                "image_count": "0",
                "row_type": "META",
            }])
            with path.open(newline="") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)

        self.assertEqual(reader.fieldnames, CANONICAL_COLUMNS)
        self.assertEqual(rows[0]["listing_text"], "A")

    def test_read_legacy_dataframe_translates_canonical_input(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "VA_catalog_rows.csv"
            write_catalog_rows(path, [{
                "listing_text": "RICHMOND",
                "catalog_page": "419",
                "chunk_number": "2",
                "image_count": "1",
                "row_type": "LISTING",
            }])

            df = read_legacy_dataframe(path)

        self.assertIn("Listing", df.columns)
        self.assertIn("Images Above", df.columns)
        self.assertEqual(df.loc[0, "Listing"], "RICHMOND")
        self.assertEqual(int(df.loc[0, "Images Above"]), 1)

    def test_read_legacy_dataframe_keeps_blank_optional_columns_assignable(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "WV_catalog_rows.csv"
            path.write_text(
                "Listing,Page,Chunk,Images Above,Type,Manuscript,Default Shape,Institutional Ownership\n"
                "Circle handstamps unless otherwise noted,438,1,0,META,,,\n"
                "WHEELING (1850;Black) 10.00,438,2,0,LISTING,,,\n",
                encoding="utf-8",
            )

            df = read_legacy_dataframe(path)
            df.at[1, "Default Shape"] = "C - Circle"

        self.assertEqual(df.loc[1, "Default Shape"], "C - Circle")


if __name__ == "__main__":
    unittest.main()
