"""strip_dot_leaders: single space-isolated dot leaders (DE Issue #11).

The extract sometimes emits a dot leader as a single '.' (when only a
couple of dots were scanned) instead of the usual '...'. A space-isolated
single dot is always a leader -- abbreviation periods attach to a
letter/digit ('C.D.', 'St.', 'N.W.') and never sit space-flanked -- so it
must collapse like a 2+ dot run. Without this, the trailing leader residue
survives value-stripping and blocks the manuscript date peel, gluing the
dates into the post-office name (e.g. 'Cantwells Bridge 1807,1810,...').
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from munger.text_utils import strip_dot_leaders


class TestSingleDotLeaders(unittest.TestCase):
    def test_single_dot_leader_before_value_collapses(self):
        # The DE manuscript row that broke the munge.
        self.assertEqual(
            strip_dot_leaders(
                "Cantwells Bridge ... 1807,1810,1823,1846 . 150/75.00"),
            "Cantwells Bridge 1807,1810,1823,1846 150/75.00")

    def test_single_dot_leader_before_value_handstamp(self):
        # The sibling (VA/MI/FL/MD) handstamp form -- value leader.
        self.assertEqual(
            strip_dot_leaders("AYLETTS/Va.(1841-51;30;PAID;Red) . 25.00"),
            "AYLETTS/Va.(1841-51;30;PAID;Red) 25.00")

    def test_mid_string_leader_drives_inscription_fix(self):
        # The VA catalog line whose inscription artifact this fix removes
        # downstream (mid-string ' . ' between '(Co)' and the date).
        self.assertEqual(
            strip_dot_leaders("*Browns Store Franklin (Co) . 1813 --"),
            "*Browns Store Franklin (Co) 1813 --")

    def test_trailing_bare_dot_is_conservative(self):
        # A dot with no following whitespace is NOT treated as a leader
        # (could be meaningful); the fix only targets space-flanked dots.
        self.assertEqual(strip_dot_leaders("Browns Store Franklin ."),
                         "Browns Store Franklin .")

    def test_abbreviation_periods_survive(self):
        # Periods attached to letters are NOT leaders -- must be preserved.
        for s in ["Duck C.D. 1798", "St. George", "N.W. Fork Bridge",
                  "CHARLOTTE C.H./Va.(1845-56;30;PAID;Black)"]:
            self.assertEqual(strip_dot_leaders(s), s, f"mangled: {s!r}")

    def test_multi_dot_leader_unchanged_behavior(self):
        # The original 2+ dot behavior is preserved.
        self.assertEqual(strip_dot_leaders("Black Bird ... 1851 ... 150.00"),
                         "Black Bird 1851 150.00")

    def test_decimal_points_in_values_survive(self):
        # A dot inside a number is not space-flanked -> untouched.
        self.assertEqual(strip_dot_leaders("Same(SL-28x2.5;Black) 150.00"),
                         "Same(SL-28x2.5;Black) 150.00")


if __name__ == "__main__":
    unittest.main()
