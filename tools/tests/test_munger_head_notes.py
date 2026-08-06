"""Integration tests for town-name parenthetical notes in the ASCC munger.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover -s tools/tests \
        -p 'test_munger_head_notes.py'
"""

import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import ascc_data_munger


AUDIT = {
    "created_date": "2026-01-01T00:00:00+00:00",
    "modified_date": "2026-01-01T00:00:00+00:00",
    "created_by": "1",
    "modified_by": "1",
}


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_munger_seeds(root):
    input_dir = root / "in"
    input_dir.mkdir()
    write_csv(
        input_dir / "reference_works.csv",
        [
            "id",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
            "code",
            "title",
            "authorship",
            "publisher",
            "publication_year",
            "edition",
            "volume",
            "isbn",
            "url",
        ],
        [
            {
                "id": "1",
                **AUDIT,
                "code": "ASCC6",
                "title": "American Stampless Cover Catalog",
                "authorship": "",
                "publisher": "",
                "publication_year": "2026",
                "edition": "6",
                "volume": "",
                "isbn": "",
                "url": "",
            }
        ],
    )
    write_csv(
        input_dir / "regions.csv",
        [
            "id",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
            "code",
            "name",
            "abbrev",
            "region_tier",
            "parent_region",
            "established_date",
            "defunct_date",
        ],
        [
            {
                "id": "43",
                **AUDIT,
                "code": "USA-TN1",
                "name": "Tennessee",
                "abbrev": "TN",
                "region_tier": "STATE",
                "parent_region": "",
                "established_date": "1796-06-01",
                "defunct_date": "",
            }
        ],
    )
    return input_dir


class MungerHeadNotesIntegrationTests(unittest.TestCase):
    def test_head_notes_emit_desc_and_italic_lettering(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            base = {
                "catalog_page": "0",
                "image_count": "0",
                "row_type": "LISTING",
                "is_manuscript": "",
                "default_shape": "",
                "institutional_owner": "",
            }
            write_csv(
                catalog_rows_path,
                [
                    "listing_text",
                    "catalog_page",
                    "chunk_number",
                    "image_count",
                    "row_type",
                    "is_manuscript",
                    "default_shape",
                    "institutional_owner",
                ],
                [
                    {
                        **base,
                        "chunk_number": "1",
                        "listing_text": (
                            'COLUMBIA/Ten("COLUMBIA" italics)'
                            "(1821-22;DC-30;Black) 150"
                        ),
                    },
                    {
                        **base,
                        "chunk_number": "2",
                        "listing_text": (
                            'Same("Ten" not in italics)'
                            "(1822;DC-30;Black) 125"
                        ),
                    },
                    {
                        **base,
                        "chunk_number": "3",
                        "listing_text": (
                            "Same(sans serif letters)(1823;DC-30;Black) 125"
                        ),
                    },
                    {
                        **base,
                        "chunk_number": "4",
                        "listing_text": (
                            "CHICAGO/Ills.(thick letters)(1834-38;30;Black) 40"
                        ),
                    },
                    {
                        **base,
                        "chunk_number": "5",
                        "listing_text": (
                            "CHICAGO/Ills(thin letters)(1840-42;30;Black) 40"
                        ),
                    },
                ],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                ascc_data_munger.main(
                    [
                        "--input",
                        str(catalog_rows_path),
                        "--input-dir",
                        str(input_dir),
                        "--out-dir",
                        str(out_dir),
                        "--reference-work-code",
                        "ASCC6",
                        "--region-abbrev",
                        "TN",
                    ]
                )
            markings = [
                row for row in read_csv(out_dir / "markings.csv")
                if row["type"] == "TOWNMARK"
            ]
            lettering_names = {
                row["name"] for row in read_csv(out_dir / "letterings.csv")
            }

        self.assertEqual(len(markings), 5)
        self.assertIn("Sans-serif", lettering_names)
        self.assertIn("Thick", lettering_names)
        self.assertIn("Thin", lettering_names)
        parent, negated_child, sans_child, thick_mark, thin_mark = markings
        self.assertEqual(parent["inscription_txt"], "COLUMBIA/Ten")
        self.assertEqual(parent["desc"], '"COLUMBIA" italics')
        self.assertEqual(parent["lettering"], "Italic")

        self.assertEqual(negated_child["inscription_txt"], "COLUMBIA/Ten")
        self.assertEqual(negated_child["desc"], '"Ten" not in italics')
        self.assertEqual(negated_child["lettering"], "")

        self.assertEqual(sans_child["inscription_txt"], "COLUMBIA/Ten")
        self.assertEqual(sans_child["desc"], "")
        self.assertEqual(sans_child["lettering"], "Sans-serif")

        self.assertEqual(thick_mark["inscription_txt"], "CHICAGO/Ills.")
        self.assertEqual(thick_mark["desc"], "")
        self.assertEqual(thick_mark["lettering"], "Thick")

        self.assertEqual(thin_mark["inscription_txt"], "CHICAGO/Ills")
        self.assertEqual(thin_mark["desc"], "")
        self.assertEqual(thin_mark["lettering"], "Thin")

    def test_inline_head_rates_emit_ratemarks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = write_munger_seeds(root)
            catalog_rows_path = root / "catalog_rows.csv"
            out_dir = root / "out"
            base = {
                "catalog_page": "0",
                "image_count": "0",
                "row_type": "LISTING",
                "is_manuscript": "",
                "default_shape": "",
                "institutional_owner": "",
            }
            write_csv(
                catalog_rows_path,
                [
                    "listing_text",
                    "catalog_page",
                    "chunk_number",
                    "image_count",
                    "row_type",
                    "is_manuscript",
                    "default_shape",
                    "institutional_owner",
                ],
                [
                    {
                        **base,
                        "chunk_number": "1",
                        "listing_text": "CHICAGO/PAID 6(1852;C-30;Black) 25",
                    },
                    {
                        **base,
                        "chunk_number": "2",
                        "listing_text": "CHICAGO/6 PAID(1853;C-30;Black) 25",
                    },
                    {
                        **base,
                        "chunk_number": "3",
                        "listing_text": "CHICAGO/5(1854;C-30;Black) 25",
                    },
                    {
                        **base,
                        "chunk_number": "4",
                        "listing_text": "CHICAGO.lll./PAID/3Cts(1855;C-30;Black) 25",
                    },
                ],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                ascc_data_munger.main(
                    [
                        "--input",
                        str(catalog_rows_path),
                        "--input-dir",
                        str(input_dir),
                        "--out-dir",
                        str(out_dir),
                        "--reference-work-code",
                        "ASCC6",
                        "--region-abbrev",
                        "TN",
                    ]
                )
            markings = read_csv(out_dir / "markings.csv")

        townmarks = [row for row in markings if row["type"] == "TOWNMARK"]
        ratemarks = [row for row in markings if row["type"] == "RATEMARK"]

        self.assertEqual(
            [row["inscription_txt"] for row in townmarks],
            ["CHICAGO", "CHICAGO", "CHICAGO", "CHICAGO.lll."],
        )
        self.assertEqual(
            [row["inscription_txt"] for row in ratemarks],
            [
                "CHICAGO PAID 6",
                "CHICAGO 6 PAID",
                "CHICAGO 5",
                "CHICAGO.lll. PAID/3Cts",
            ],
        )
        self.assertEqual(
            [row["rate_val"] for row in ratemarks],
            ["6.0", "6.0", "5.0", "3.0"],
        )


if __name__ == "__main__":
    unittest.main()
