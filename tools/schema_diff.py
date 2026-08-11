"""Normalize two mysqldump --no-data files and diff them table-by-table.

Usage: python schema_diff.py A.sql B.sql

Strips AUTO_INCREMENT counters and sorts the lines within each table so that
ordering and volatile counters do not show up as differences. Prints tables only
in A, tables only in B, and per-table body differences for shared tables.

Used by tools/fresh_migrate.md as the schema-diff gate when reconciling a server
DB to the reset `common` migration history.
"""
import re
import sys


def parse(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    tables = {}
    for m in re.finditer(
        r"CREATE TABLE `(?P<name>[^`]+)` \((?P<body>.*?)\n\)(?P<opts>[^;]*);",
        text, re.S):
        lines = []
        for ln in m.group("body").split("\n"):
            ln = ln.strip().rstrip(",")
            if not ln:
                continue
            ln = re.sub(r"AUTO_INCREMENT=\d+", "", ln)
            lines.append(ln)
        lines.sort()
        tables[m.group("name")] = lines
    return tables


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python schema_diff.py A.sql B.sql")
    a, b = parse(sys.argv[1]), parse(sys.argv[2])
    print("=== tables only in %s ===" % sys.argv[1])
    for t in sorted(set(a) - set(b)):
        print("  " + t)
    print("=== tables only in %s ===" % sys.argv[2])
    for t in sorted(set(b) - set(a)):
        print("  " + t)
    print("=== per-table body diffs (shared tables) ===")
    any_diff = False
    for t in sorted(set(a) & set(b)):
        if a[t] != b[t]:
            any_diff = True
            print("--- table: %s" % t)
            for ln in sorted(set(a[t]) - set(b[t])):
                print("   only in A: " + ln)
            for ln in sorted(set(b[t]) - set(a[t])):
                print("   only in B: " + ln)
    if not any_diff:
        print("  (no body differences in shared tables)")


if __name__ == "__main__":
    main()
