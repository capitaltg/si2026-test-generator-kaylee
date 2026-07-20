"""testgen: generate realistic, reproducible fake test data (CSV, SQL, PDF).

This is the library "front door". Anyone can write:

    from testgen import generate

and call generate(...) from their own Python code. The CLI (see cli.py) is a
thin wrapper that ends up calling this exact same function, so both doors lead
to the same engine.
"""
from __future__ import annotations

from .core import generate
from .fields import available_field_types, register_field_type

__version__ = "0.1.0"
__all__ = [
    "generate",
    "register_field_type",
    "available_field_types",
    "__version__",
]
