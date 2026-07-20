"""The CLI "front door".

This is deliberately thin. Its only jobs are: read what the user typed in the
terminal, translate it into a call to generate(), and print the result. All the
actual logic lives in core.py, so the command line and the library never drift
apart.
"""

from __future__ import annotations

import argparse
import json

from . import __version__
from .core import generate
from .fields import available_field_types
from .writers import to_csv_string, to_sql_string, write_sqlite

# A tiny example schema so `python -m testgen` shows something real right now.
# This is NOT the official GovCon preset; that arrives (as a named, reusable
# preset) in Ticket 4. For now it just demonstrates the engine end to end.
EXAMPLE_SCHEMA = [
    {"name": "contract_id", "type": "sequence", "prefix": "GS-", "start": 1000},
    {"name": "vendor", "type": "company"},
    {
        "name": "agency",
        "type": "choice",
        "choices": ["Dept of Defense", "GSA", "Dept of Veterans Affairs", "NASA"],
    },
    {"name": "amount_usd", "type": "int", "min": 25000, "max": 5000000},
    {"name": "awarded_on", "type": "date", "start": "2021-01-01", "end": "2024-12-31"},
]


def build_parser():
    """Define what flags the command accepts. Kept separate so it is easy to
    read and easy to test."""
    parser = argparse.ArgumentParser(
        prog="testgen",
        description="Generate realistic, reproducible fake test data (CSV, SQL, PDF).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"testgen {__version__}",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=10,
        help="How many rows to generate (default: 10).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for reproducible output. The same seed always gives the same data.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "sql", "sqlite"],
        default="json",
        help="Output format (default: json).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="File to write to. If omitted, output is printed to the screen "
        "(not allowed for sqlite, which is a binary database file).",
    )
    parser.add_argument(
        "--table",
        default="records",
        help="Table name to use for sql and sqlite output (default: records).",
    )
    parser.add_argument(
        "--list-types",
        action="store_true",
        help="List every available field type and exit.",
    )
    return parser


def main(argv=None):
    """Entry point for the command line.

    argv defaults to None, which means argparse reads the real terminal args.
    We accept it as a parameter so tests can call main(["--rows", "3"]) directly.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_types:
        for name in available_field_types():
            print(name)
        return 0

    rows = generate(EXAMPLE_SCHEMA, rows=args.rows, seed=args.seed)

    # sqlite is a binary database file, so it must go to a real path, never the
    # screen. Handle it first and bail early with a clear message if --out is
    # missing.
    if args.format == "sqlite":
        if not args.out:
            parser.error(
                "--format sqlite requires --out (it writes a binary .db file)."
            )
        write_sqlite(rows, args.out, table=args.table)
        print(f"Wrote {len(rows)} rows to {args.out} (sqlite, table '{args.table}').")
        return 0

    # The text formats all become a single string, which we then either print
    # or save depending on whether --out was given.
    text = _render_text(rows, args.format, args.table)
    if args.out:
        with open(args.out, "w", newline="") as handle:
            handle.write(text)
        print(f"Wrote {len(rows)} rows to {args.out} ({args.format}).")
    else:
        print(text)
    return 0


def _render_text(rows, fmt, table):
    """Turn rows into the chosen text format. Kept separate so main() stays
    about *where* output goes while this handles *what shape* it takes."""
    if fmt == "csv":
        return to_csv_string(rows)
    if fmt == "sql":
        return to_sql_string(rows, table=table)
    # json: default=str lets it print values it does not natively understand,
    # like date objects, by falling back to their string form.
    return json.dumps(rows, indent=2, default=str)
