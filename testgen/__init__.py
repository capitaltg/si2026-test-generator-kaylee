"""testgen: generate realistic, reproducible fake test data (CSV, SQL, PDF).

This is the library "front door". Anyone can write:

    from testgen import generate

and call generate(...) from their own Python code. The CLI (see cli.py) is a
thin wrapper that ends up calling this exact same function, so both doors lead
to the same engine.
"""

from __future__ import annotations

from .core import generate
from .fields import available_field_types, field_type_groups, register_field_type
from .infer import (
    from_csv_headers,
    from_description,
    guess_type,
    infer_json_sample,
    parse_ddl,
)
from .fillable import render_fillable
from .formfill import available_forms, fill_form_bytes, fill_forms_bytes
from .pdf import to_pdf_docs_bytes, to_pdf_table_bytes
from .presets import (
    PRESETS,
    generate_preset,
    list_presets,
    preset_form_values,
)
from .writers import to_csv_string, to_sql_string, to_sqlite_bytes, write_sqlite

__version__ = "0.1.0"
__all__ = [
    "generate",
    "register_field_type",
    "available_field_types",
    "field_type_groups",
    "parse_ddl",
    "from_csv_headers",
    "infer_json_sample",
    "from_description",
    "guess_type",
    "to_csv_string",
    "to_sql_string",
    "write_sqlite",
    "to_sqlite_bytes",
    "to_pdf_table_bytes",
    "to_pdf_docs_bytes",
    "available_forms",
    "fill_form_bytes",
    "fill_forms_bytes",
    "render_fillable",
    "PRESETS",
    "list_presets",
    "generate_preset",
    "preset_form_values",
    "__version__",
]
