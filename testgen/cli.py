"""The CLI "front door".

This is deliberately thin. Its only jobs are: read what the user typed in the
terminal, translate it into a call to generate(), and print the result. All the
actual logic lives in core.py, so the command line and the library never drift
apart.
"""
from __future__ import annotations

import argparse

from . import __version__
from .core import generate


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
    return parser


def main(argv=None):
    """Entry point for the command line.

    argv defaults to None, which means argparse reads the real terminal args.
    We accept it as a parameter so tests can call main(["--rows", "3"]) directly.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    result = generate(rows=args.rows, seed=args.seed)
    print(result)
    return 0
