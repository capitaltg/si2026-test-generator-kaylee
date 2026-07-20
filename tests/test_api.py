"""Tests for the FastAPI web API (Fixtura's backend).

These need the optional web extras (pip install ".[web]"). If FastAPI or its
test client (httpx) is not installed, the whole module skips, so the core engine
test suite still runs without them.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402
from testgen.fields import FIELD_TYPES  # noqa: E402

client = TestClient(app)


def test_field_types_are_grouped_and_real():
    resp = client.get("/field-types")
    assert resp.status_code == 200
    groups = resp.json()["groups"]
    names = [g["name"] for g in groups]
    assert "Identity" in names and "Dates" in names
    # every advertised type is actually a real generator
    for group in groups:
        for t in group["types"]:
            assert t["value"] in FIELD_TYPES


def test_generate_returns_rows():
    body = {
        "fields": [
            {"name": "id", "type": "autoIncrement", "prefix": "N-", "start": 1},
            {"name": "amount", "type": "int", "min": 5, "max": 7},
        ],
        "rows": 4,
        "seed": 1,
    }
    resp = client.post("/generate", json=body)
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 4
    assert rows[0] == {"id": "N-1", "amount": rows[0]["amount"]}
    assert all(5 <= r["amount"] <= 7 for r in rows)


def test_generate_same_seed_is_reproducible():
    body = {"fields": [{"name": "v", "type": "uuid"}], "rows": 5, "seed": 42}
    first = client.post("/generate", json=body).json()
    second = client.post("/generate", json=body).json()
    assert first == second


def test_generate_unknown_type_is_400():
    body = {"fields": [{"name": "x", "type": "not_a_type"}], "rows": 1}
    resp = client.post("/generate", json=body)
    assert resp.status_code == 400


def test_export_csv_downloads_with_header():
    body = {"fields": [{"name": "v", "type": "int"}], "rows": 3, "seed": 1}
    resp = client.post("/export", json={**body, "format": "csv", "table": "t"})
    assert resp.status_code == 200
    assert 'filename="t.csv"' in resp.headers["content-disposition"]
    assert resp.text.splitlines()[0] == "v"


def test_export_sqlite_is_a_real_db():
    body = {"fields": [{"name": "v", "type": "int"}], "rows": 3, "seed": 1}
    resp = client.post("/export", json={**body, "format": "sqlite", "table": "t"})
    assert resp.status_code == 200
    assert resp.content[:16] == b"SQLite format 3\x00"


def test_export_unknown_format_is_400():
    body = {"fields": [{"name": "v", "type": "int"}], "rows": 1, "format": "xlsx"}
    resp = client.post("/export", json=body)
    assert resp.status_code == 400
