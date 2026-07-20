"""Makes `python -m testgen` work.

Python looks for this special file when you run a package with `-m`. It just
hands control to the CLI's main() and exits with whatever status code it
returns (0 = success).
"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
