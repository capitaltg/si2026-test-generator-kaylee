"""Tests for the data engine.

These cover the two things that matter most for Ticket 2: the engine produces
the right shape of data, and it is reproducible (same seed -> same output).
"""

from __future__ import annotations

import csv
import datetime
import io
import re
import sqlite3

import pytest

from testgen import (
    available_field_types,
    field_type_groups,
    from_csv_headers,
    from_description,
    generate,
    guess_type,
    infer_json_sample,
    parse_ddl,
    register_field_type,
    to_csv_string,
    to_sql_string,
    write_sqlite,
)
from testgen.cli import main
from testgen.fields import FIELD_TYPES

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


# --- Ticket 3: output writers -------------------------------------------------


def test_csv_has_header_and_one_line_per_row():
    rows = generate(SCHEMA, rows=3, seed=1)
    text = to_csv_string(rows)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert len(parsed) == 3
    assert set(parsed[0]) == {"id", "vendor", "amount", "awarded_on"}
    # Values survive the round trip (csv is all strings, so compare as strings).
    assert parsed[0]["id"] == "A-1"


def test_csv_of_empty_rows_is_empty_string():
    assert to_csv_string([]) == ""


def test_sql_dump_has_create_and_one_insert_per_row():
    rows = generate(SCHEMA, rows=3, seed=1)
    text = to_sql_string(rows, table="awards")
    assert text.count("CREATE TABLE awards") == 1
    assert text.count("INSERT INTO awards") == 3


def test_sql_dump_escapes_single_quotes():
    rows = [{"note": "O'Brien & Co"}]
    text = to_sql_string(rows, table="t")
    # The single quote must be doubled so the SQL literal stays valid.
    assert "'O''Brien & Co'" in text


def test_sqlite_file_is_queryable(tmp_path):
    rows = generate(SCHEMA, rows=5, seed=1)
    db_path = tmp_path / "out.db"
    write_sqlite(rows, str(db_path), table="awards")

    connection = sqlite3.connect(str(db_path))
    try:
        count = connection.execute("SELECT COUNT(*) FROM awards").fetchone()[0]
    finally:
        connection.close()
    assert count == 5


def test_cli_writes_csv_file(tmp_path):
    out = tmp_path / "data.csv"
    assert (
        main(["--rows", "4", "--seed", "1", "--format", "csv", "--out", str(out)]) == 0
    )
    parsed = list(csv.DictReader(out.open()))
    assert len(parsed) == 4


def test_cli_writes_sqlite_file(tmp_path):
    out = tmp_path / "data.db"
    assert (
        main(["--rows", "4", "--seed", "1", "--format", "sqlite", "--out", str(out)])
        == 0
    )
    assert out.exists()


def test_cli_sqlite_without_out_is_an_error():
    # argparse's parser.error() exits with SystemExit, not a normal return.
    with pytest.raises(SystemExit):
        main(["--format", "sqlite"])


# --- Fixtura P1: expanded types, grouped metadata, null % --------------------


def test_every_grouped_type_generates():
    """Build a schema with one column of every type in the grouped menu and
    make sure they all produce a value (the two that need options get them)."""
    schema = []
    for _group, items in field_type_groups():
        for type_name, _label in items:
            field = {"name": type_name, "type": type_name}
            if type_name == "enum":
                field["values"] = "a, b, c"
            if type_name == "constant":
                field["value"] = "X"
            if type_name == "pattern":
                field["pattern"] = "AB-####"
            schema.append(field)
    rows = generate(schema, rows=3, seed=1)
    assert len(rows) == 3
    assert all(rows[0][field["name"]] is not None for field in schema)


def test_field_type_groups_reference_real_types():
    for _group, items in field_type_groups():
        for type_name, _label in items:
            assert type_name in FIELD_TYPES, f"{type_name} not registered"


def test_fixtura_aliases_map_to_the_right_generator():
    rows = generate(
        [
            {"name": "a", "type": "autoIncrement", "prefix": "N-", "start": 1},
            {"name": "b", "type": "price", "min": 10, "max": 20},
        ],
        rows=3,
        seed=1,
    )
    assert rows[0]["a"] == "N-1"
    assert rows[1]["a"] == "N-2"
    assert all(10 <= r["b"] <= 20 for r in rows)


def test_enum_accepts_a_comma_separated_values_string():
    rows = generate(
        [{"name": "s", "type": "enum", "values": "active, pending, closed"}],
        rows=30,
        seed=1,
    )
    assert all(r["s"] in {"active", "pending", "closed"} for r in rows)


def test_null_pct_100_is_all_null():
    rows = generate([{"name": "x", "type": "int", "null_pct": 100}], rows=20, seed=1)
    assert all(r["x"] is None for r in rows)


def test_null_pct_0_is_never_null():
    rows = generate([{"name": "x", "type": "int", "null_pct": 0}], rows=20, seed=1)
    assert all(r["x"] is not None for r in rows)


def test_null_pct_is_reproducible_and_partial():
    spec = [{"name": "x", "type": "int", "null_pct": 50}]
    first = generate(spec, rows=100, seed=7)
    assert first == generate(spec, rows=100, seed=7)
    nulls = sum(1 for r in first if r["x"] is None)
    assert 0 < nulls < 100  # roughly half, but at least some of each


# --- Fixtura P3: schema inference from DDL / CSV / JSON / description ---------


def test_guess_type_basic_and_ordering():
    assert guess_type("email") == "email"
    assert guess_type("customer_email") == "email"
    assert guess_type("is_active") == "bool"
    assert guess_type("salary") == "price"
    assert guess_type("created_at") == "datetime"
    # zip contains "ip" but must stay a zip code, not become an IPv4
    assert guess_type("zip") == "zip"
    assert guess_type("id", "uuid") == "uuid"


def test_parse_ddl_reads_columns_and_table():
    ddl = """
    CREATE TABLE users (
      id UUID PRIMARY KEY,
      first_name VARCHAR(50),
      email VARCHAR(120),
      salary DECIMAL(10,2),
      is_active BOOLEAN,
      created_at TIMESTAMP
    );
    """
    table, fields = parse_ddl(ddl)
    assert table == "users"
    by_name = {field["name"]: field["type"] for field in fields}
    assert by_name["first_name"] == "firstName"
    assert by_name["email"] == "email"
    assert by_name["salary"] == "price"
    assert by_name["is_active"] == "bool"
    assert by_name["created_at"] == "datetime"
    # PRIMARY KEY line was skipped, so we only got the 6 real columns
    assert len(fields) == 6
    # and the inferred schema actually generates
    assert len(generate(fields, rows=2, seed=1)) == 2


def test_parse_ddl_without_columns_raises():
    with pytest.raises(ValueError):
        parse_ddl("CREATE TABLE oops")


def test_from_csv_headers():
    fields = from_csv_headers("order_id, customer_email, quantity\n1,a@b.com,5")
    by_name = {field["name"]: field["type"] for field in fields}
    assert by_name["order_id"] == "int"  # ends in _id, no uuid hint
    assert by_name["customer_email"] == "email"
    assert by_name["quantity"] == "int"


def test_infer_json_sample_uses_names_and_values():
    sample = '{"id": "abc", "age": 34, "balance": 12.5, "verified": true}'
    fields = infer_json_sample(sample)
    by_name = {field["name"]: field["type"] for field in fields}
    assert by_name["age"] == "age"
    assert by_name["verified"] == "bool"
    assert by_name["balance"] == "price"


def test_infer_json_invalid_raises():
    with pytest.raises(ValueError):
        infer_json_sample("{not valid json")


def test_from_description_finds_fields_and_adds_id():
    fields = from_description(
        "A customer with full name, email, city, and lifetime spend."
    )
    names = {field["name"] for field in fields}
    assert "id" in names  # always prepended
    assert "email" in names
    assert "full_name" in names
    types = {field["type"] for field in fields}
    assert "price" in types  # "spend" -> price
    assert len(generate(fields, rows=2, seed=1)) == 2
