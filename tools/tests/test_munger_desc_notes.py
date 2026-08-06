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

from ascc_data_munger import extract_and_strip_see_clause, listing_desc_lines
from munger.fields import classify_all_fields, subparse_fields


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
            [{
                'date_granularity': 'DECADE',
                'date_year_start': 1850,
                'date_is_circa': False,
                'date_raw': "1850's",
            }],
            ['fancy lined X'],
        )
        self.assertEqual(lines, [
            'Backstamp',
            'See Colonial listing',
            "Date(s) seen: 1850's",
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

    def test_nor_size_qualifier_is_a_desc_note(self):
        sizes = [{'size_qualifier': 'NOR'}]
        self.assertEqual(
            listing_desc_lines([], None, [], [], sizes),
            ['NOR'],
        )

    def test_best_effort_size_descriptor_is_desc_note(self):
        sizes = [{'size_desc_note': 'framed arc'}]
        self.assertEqual(
            listing_desc_lines([], None, [], [], sizes),
            ['framed arc'],
        )

    def test_parsed_color_is_not_desc_note(self):
        fields = ['1802-04', '28', 'FREE,PAID', 'Red brown']
        parsed = subparse_fields({
            'paren_fields': fields,
            'paren_field_types': classify_all_fields(fields),
            'Manuscript': '',
        })
        self.assertEqual(parsed['other_fields'], [])
        self.assertEqual(
            listing_desc_lines([], None, [], parsed['other_fields'], []),
            [],
        )

    def test_arc_decade_listing_preserves_date_text_and_nor(self):
        sizes = [{'size_qualifier': 'NOR'}]
        dates = [{
            'date_granularity': 'DECADE',
            'date_year_start': 1850,
            'date_is_circa': False,
            'date_raw': '1850s',
        }]
        self.assertEqual(
            listing_desc_lines([], None, dates, [], sizes),
            ['Date(s) seen: 1850s', 'NOR'],
        )

    def test_circa_year_uses_exact_source_text(self):
        dates = [
            {
                'date_granularity': 'YEAR',
                'date_is_circa': True,
                'date_raw': 'c1850',
            },
            {
                'date_granularity': 'YEAR',
                'date_is_circa': True,
                'date_raw': '1850c',
            },
        ]
        self.assertEqual(
            listing_desc_lines([], None, dates, []),
            ['Date(s) seen: c1850, 1850c'],
        )

    def test_non_decade_dates_do_not_emit_dates_seen_line(self):
        lines = listing_desc_lines(
            [], None,
            [{'date_granularity': 'YEAR', 'date_year_start': 1850}],
            [],
        )
        self.assertEqual(lines, [])

    def test_see_clause_extraction_preserves_trailing_value(self):
        clause, clean_text = extract_and_strip_see_clause(
            "(L)(May 20, 1828) + See note below 750"
        )

        self.assertEqual(clause, "See note below")
        self.assertEqual(clean_text, "(L)(May 20, 1828) + 750")

    def test_see_clause_extraction_preserves_comma_value(self):
        clause, clean_text = extract_and_strip_see_clause(
            "(L)(April 21, 1780;Way) See Way Mail section 2,000"
        )

        self.assertEqual(clause, "See Way Mail section")
        self.assertEqual(clean_text, "(L)(April 21, 1780;Way) 2,000")

    def test_see_clause_extraction_preserves_dash_value(self):
        clause, clean_text = extract_and_strip_see_clause("(L) See State --")

        self.assertEqual(clause, "See State")
        self.assertEqual(clean_text, "(L) --")


if __name__ == "__main__":
    unittest.main()
