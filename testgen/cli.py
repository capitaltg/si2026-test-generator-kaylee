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
    # default=str lets json print things it does not natively understand, like
    # date objects, by falling back to their string form.
    print(json.dumps(rows, indent=2, default=str))
    return 0
