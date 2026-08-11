#!/usr/bin/env python3
"""Export every table in an Azure SQL database to one CSV per table.

One-off utility for inspecting/migrating production data. Connects to a live
Azure SQL database with pymssql and writes one CSV per table:
"<table>.csv" for the dbo schema, "<schema>.<table>.csv" otherwise.

Credentials come from environment variables so they stay out of shell
history and version control:

    AZURE_SQL_SERVER    e.g. myserver.database.windows.net
    AZURE_SQL_DB        database name
    AZURE_SQL_USER      SQL auth user. Try "user" first; if Azure rejects the
                        login, use "user@myserver" (the short server name).
    AZURE_SQL_PASSWORD  password

Run from the repo root (cwd = worldcovers/). --out-dir defaults to
backups/db_export/, which is gitignored (the backups/* rule). Example:

    AZURE_SQL_SERVER=myserver.database.windows.net \
    AZURE_SQL_DB=mydb \
    AZURE_SQL_USER=myuser \
    AZURE_SQL_PASSWORD='...' \
    uv run python tools/export_azure_sql_to_csv.py

WARNING: the exported CSVs contain user emails and password hashes. Do NOT
commit backups/db_export/.
"""
import argparse
import csv
import os
import sys
from pathlib import Path

# pymssql bundles FreeTDS in its wheels; easiest Azure SQL driver on macOS.
# Install with: uv pip install pymssql
import pymssql

LIST_TABLES_SQL = (
    "SELECT s.name AS sch, t.name AS tbl "
    "FROM sys.tables t "
    "JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "ORDER BY s.name, t.name"
)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default="backups/db_export")
    args = ap.parse_args(argv)

    env = {
        "AZURE_SQL_SERVER": os.environ.get("AZURE_SQL_SERVER"),
        "AZURE_SQL_DB": os.environ.get("AZURE_SQL_DB"),
        "AZURE_SQL_USER": os.environ.get("AZURE_SQL_USER"),
        "AZURE_SQL_PASSWORD": os.environ.get("AZURE_SQL_PASSWORD"),
    }
    missing = [name for name, val in env.items() if not val]
    if missing:
        sys.exit("Missing required env vars: " + ", ".join(missing))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Azure SQL listens on 1433 and requires TLS; pymssql/FreeTDS negotiate
    # it. tds_version 7.4 is correct for Azure SQL.
    conn = pymssql.connect(
        server=env["AZURE_SQL_SERVER"],
        user=env["AZURE_SQL_USER"],
        password=env["AZURE_SQL_PASSWORD"],
        database=env["AZURE_SQL_DB"],
        port=1433,
        tds_version="7.4",
    )
    try:
        cur = conn.cursor()
        cur.execute(LIST_TABLES_SQL)
        tables = [(row[0], row[1]) for row in cur.fetchall()]
        print("Found %d tables in %s" % (len(tables), env["AZURE_SQL_DB"]))

        total_rows = 0
        for sch, tbl in tables:
            # Identifiers come from sys.tables (trusted), not user input.
            # Python-format the final SQL so no '%' remains for pymssql's
            # paramstyle to misinterpret, then execute with no params.
            cur.execute("SELECT * FROM [%s].[%s]" % (sch, tbl))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

            name = tbl if sch == "dbo" else "%s.%s" % (sch, tbl)
            path = out_dir / (name + ".csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(cols)
                writer.writerows(rows)

            total_rows += len(rows)
            print("  %-40s %8d rows -> %s"
                  % ("[%s].[%s]" % (sch, tbl), len(rows), path))

        print("Done. %d tables, %d rows total, written to %s/"
              % (len(tables), total_rows, out_dir))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
