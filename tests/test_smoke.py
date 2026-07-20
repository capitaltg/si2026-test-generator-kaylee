"""Tests for the data engine.

These cover the two things that matter most for Ticket 2: the engine produces
the right shape of data, and it is reproducible (same seed -> same output).
"""
from __future__ import annotations

import datetime
import re

import pytest

from testgen import available_field_types, generate, register_field_type
from testgen.cli import main

# A small schema exercising several field types, reused across tests.
SCHEMA = [
    {"name": "id", "type": "sequence", "prefix": "A-", "start": 1},
    {"name": "vendor", "type": "company"},
    {"name": "amount", "type": "int", "min": 100, "max": 200},
    {"name": "awarded_on", "type": "date", "start": "2020-01-01", "end": "2020-12-31"},
]


def test_generate_returns_requested_number_of_rows():
    rows = generate(SCHEMA, rows=5, seed=42)
    assert len(rows) == 5
    assert set(rows[0]) == {"id", "vendor", "amount", "awarded_on"}


def test_same_seed_is_reproducible():
    first = generate(SCHEMA, rows=10, seed=42)
    second = generate(SCHEMA, rows=10, seed=42)
    assert first == second


def test_different_seed_changes_output():
    first = generate(SCHEMA, rows=10, seed=42)
    second = generate(SCHEMA, rows=10, seed=99)
    assert first != second


def test_field_options_are_respected():
    rows = generate(SCHEMA, rows=50, seed=1)
    assert all(100 <= r["amount"] <= 200 for r in rows)
    assert rows[0]["id"] == "A-1"
    assert rows[1]["id"] == "A-2"
    assert all(isinstance(r["awarded_on"], datetime.date) for r in rows)


def test_unknown_field_type_is_a_clear_error():
    with pytest.raises(ValueError):
        generate([{"name": "x", "type": "not_a_real_type"}], rows=1, seed=1)


def test_empty_schema_is_a_clear_error():
    with pytest.raises(ValueError):
        generate([], rows=1, seed=1)


def test_pattern_type_matches_its_template():
    rows = generate(
        [{"name": "cage", "type": "pattern", "pattern": "CAGE-#####"}],
        rows=5,
        seed=1,
    )
    assert all(re.fullmatch(r"CAGE-\d{5}", r["cage"]) for r in rows)


def test_constant_type_is_the_same_on_every_row():
    rows = generate(
        [{"name": "fy", "type": "constant", "value": "FY2024"}], rows=4, seed=1
    )
    assert all(r["fy"] == "FY2024" for r in rows)


def test_user_can_register_a_custom_type():
    def row_label(field, index, rng, faker):
        return f"ROW-{index}"

    register_field_type("row_label", row_label)
    assert "row_label" in available_field_types()

    rows = generate([{"name": "label", "type": "row_label"}], rows=3, seed=1)
    assert [r["label"] for r in rows] == ["ROW-0", "ROW-1", "ROW-2"]


def test_cli_door_still_runs():
    # main() returns 0 on success, like a real command-line program.
    assert main(["--rows", "3", "--seed", "42"]) == 0


def test_cli_list_types_runs():
    assert main(["--list-types"]) == 0
