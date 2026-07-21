"""The web API: a FastAPI server that puts the engine behind HTTP.

This is the backend for Fixtura (the designed web UI). It is the fourth way to
reach the engine, alongside the Python library, the CLI, and (soon) the browser
front end. It reimplements nothing: every endpoint just calls generate() and the
writers we already have, and returns the result over HTTP.

Run it (from the repo root):

    pip install ".[web]"                 # once, to get FastAPI + a server
    uvicorn server:app --reload          # dev server with auto-reload
    # or simply:  python3 server.py

Then the API lives at http://127.0.0.1:8000 . The endpoints:

    GET  /field-types   the grouped type menu for the dropdown
    POST /generate      { fields, rows, seed } -> { rows: [...] }
    POST /export        { fields, rows, seed, format, table } -> a file download

A "field" here is exactly what the engine's schema wants: {name, type, ...options},
plus an optional null_pct. We call the request body key "fields" (not "schema")
only because pydantic reserves some schema-related names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from testgen import (
    field_type_groups,
    generate,
    to_csv_string,
    to_pdf_docs_bytes,
    to_pdf_table_bytes,
    to_sql_string,
    to_sqlite_bytes,
)
from testgen.infer import (
    from_csv_headers,
    from_description,
    infer_json_sample,
    parse_ddl,
)

app = FastAPI(title="Fixtura API", version="0.1.0")


# --- Request shapes ----------------------------------------------------------


class FieldSpec(BaseModel):
    """One column. name + type are required; any extra keys (min, max, choices,
    values, pattern, start, ...) are allowed and passed straight to the engine,
    which is why extra='allow' is set."""

    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    null_pct: float = 0


class GenerateRequest(BaseModel):
    fields: List[FieldSpec]
    rows: int = 25
    seed: Optional[int] = 42


class ExportRequest(GenerateRequest):
    format: str = "csv"  # csv | sql | sqlite | json | pdf-table | pdf-docs
    table: str = "records"
    # Optional light layout config for the document PDF (pdf-docs): title,
    # title_field, header_fields, body_fields. Ignored by every other format.
    pdf_config: Optional[dict] = None


class DdlRequest(BaseModel):
    ddl: str


class CsvRequest(BaseModel):
    csv: str


class JsonSampleRequest(BaseModel):
    sample: str


class DescribeRequest(BaseModel):
    text: str


def _schema_from(fields: List[FieldSpec]) -> List[dict]:
    """Turn the request's field models back into the plain dicts the engine
    expects (name, type, and whatever options were sent)."""
    return [f.model_dump() for f in fields]


def _make_rows(req: GenerateRequest) -> List[dict]:
    """Shared generate step with a friendly error instead of a 500 crash."""
    try:
        return generate(_schema_from(req.fields), rows=req.rows, seed=req.seed)
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=400, detail=str(error))


# --- Endpoints ---------------------------------------------------------------


@app.get("/field-types")
def field_types() -> dict:
    """The grouped, labelled type menu, straight from the engine's metadata."""
    groups = [
        {"name": name, "types": [{"value": v, "label": label} for v, label in items]}
        for name, items in field_type_groups()
    ]
    return {"groups": groups}


@app.post("/generate")
def generate_rows(req: GenerateRequest) -> dict:
    """Generate rows and return them as JSON. FastAPI encodes dates/datetimes to
    strings automatically."""
    return {"rows": _make_rows(req)}


# How each export format is built and served. Each build fn takes (rows, req)
# so the PDF builders can reach the schema (for column order) and pdf_config;
# the simpler formats just use req.table.
_EXPORTS = {
    "csv": ("text/csv", "csv", lambda rows, req: to_csv_string(rows).encode()),
    "sql": (
        "text/plain",
        "sql",
        lambda rows, req: to_sql_string(rows, table=req.table).encode(),
    ),
    "json": (
        "application/json",
        "json",
        lambda rows, req: json.dumps(rows, indent=2, default=str).encode(),
    ),
    "sqlite": (
        "application/x-sqlite3",
        "db",
        lambda rows, req: to_sqlite_bytes(rows, table=req.table),
    ),
    # The whole dataset as one paginated table.
    "pdf-table": (
        "application/pdf",
        "pdf",
        lambda rows, req: to_pdf_table_bytes(_schema_from(req.fields), rows),
    ),
    # One formatted page per row, laid out from the optional pdf_config.
    "pdf-docs": (
        "application/pdf",
        "pdf",
        lambda rows, req: to_pdf_docs_bytes(
            _schema_from(req.fields), rows, config=req.pdf_config
        ),
    ),
}


@app.post("/export")
def export(req: ExportRequest) -> Response:
    """Generate rows and return them as a downloadable file in the chosen
    format."""
    if req.format not in _EXPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format '{req.format}'. Use one of: " + ", ".join(_EXPORTS),
        )
    rows = _make_rows(req)
    media_type, ext, build = _EXPORTS[req.format]
    try:
        content = build(rows, req)
    except ValueError as error:
        # e.g. document PDF asked for more pages than the per-row limit allows.
        raise HTTPException(status_code=400, detail=str(error))
    filename = f"{req.table or 'data'}.{ext}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Schema builders: turn something you already have into a schema ----------


@app.post("/schema/from-ddl")
def schema_from_ddl(req: DdlRequest) -> dict:
    """Parse a CREATE TABLE statement into a schema (plus the table name)."""
    try:
        table, fields = parse_ddl(req.ddl)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"table": table, "fields": fields}


@app.post("/schema/from-csv")
def schema_from_csv(req: CsvRequest) -> dict:
    """Build a schema from a CSV header row."""
    try:
        return {"fields": from_csv_headers(req.csv)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/schema/from-json")
def schema_from_json(req: JsonSampleRequest) -> dict:
    """Infer a schema from a sample JSON object or array."""
    try:
        return {"fields": infer_json_sample(req.sample)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/schema/from-description")
def schema_from_description(req: DescribeRequest) -> dict:
    """Build a schema from a plain-English description (keyword rules, no AI)."""
    return {"fields": from_description(req.text)}


# Serve the Fixtura front end (static files) at the root. Mounted LAST so the
# API routes defined above are matched first; anything else (/, /styles.css,
# /app.js) is served from the static/ folder. html=True serves index.html at /.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
