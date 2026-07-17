"""Tests for per-listing desc note assembly (errata promotion).

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover -s tools/tests \
        -p 'test_munger_desc_notes.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ascc_data_munger import listing_desc_lines


class ListingDescLinesTests(unittest.TestCase):

    def test_unresolved_other_field_recorded_verbatim(self):
        self.assertEqual(
            listing_desc_lines([], None, [], ['fancy lined X']),
            ['fancy lined X'],
        )

    def test_line_order_annotations_see_decades_errata(self):
        lines = listing_desc_lines(
            ['Backstamp'],
            'See Colonial listing',
            [{'date_granularity': 'DECADE', 'date_year_start': 1850}],
            ['fancy lined X'],
        )
        self.assertEqual(lines, [
            'Backstamp',
            'See Colonial listing',
            'Dates seen: 1850s',
            'fancy lined X',
        ])

    def test_duplicate_errata_line_dropped(self):
        self.assertEqual(
            listing_desc_lines(['Backstamp'], None, [], ['Backstamp']),
            ['Backstamp'],
        )

    def test_all_empty_inputs_return_empty_list(self):
        self.assertEqual(listing_desc_lines(None, None, None, None), [])
        self.assertEqual(listing_desc_lines([], None, [], []), [])

    def test_blank_other_fields_skipped(self):
        self.assertEqual(
            listing_desc_lines([], None, [], ['', '   ', 'fancy lined X']),
            ['fancy lined X'],
        )

    def test_bracketed_size_qualifier_recorded_without_delimiters(self):
        # ANNAPOLIS "SL-42x5,MDD[separate hdstp]": parse_size_field keeps the
        # note as size_qualifier '[separate hdstp]'; desc gets the bare text.
        sizes = [{'size_qualifier': '[separate hdstp]'}]
        self.assertEqual(
            listing_desc_lines([], None, [], [], sizes),
            ['separate hdstp'],
        )

    def test_unbracketed_size_qualifier_not_a_desc_note(self):
        # Positional suffixes ("SL-45x4,YMDD below" -> qualifier 'below')
        # describe layout, not errata; they stay out of desc.
        sizes = [{'size_qualifier': 'below'}, {'size_qualifier': None}]
        self.assertEqual(listing_desc_lines([], None, [], [], sizes), [])

    def test_non_decade_dates_do_not_emit_dates_seen_line(self):
        lines = listing_desc_lines(
            [], None,
            [{'date_granularity': 'YEAR', 'date_year_start': 1850}],
            [],
        )
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
