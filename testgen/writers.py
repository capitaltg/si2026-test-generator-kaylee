"""Output writers: turn the generated rows into real, saveable output.

`generate()` in core.py hands back a plain list of dicts (rows). That is the
in-memory shape. This module turns that shape into the formats people actually
want on disk:

    csv     a spreadsheet-friendly .csv file
    sql     a text dump of CREATE TABLE + INSERT statements (any SQL database)
    sqlite  a real SQLite .db file you can open and query immediately

Like the engine, nothing here knows about GovCon or any specific dataset. It
just serializes whatever rows it is given. Keeping these writers separate from
the engine means the engine can stay focused on *making* data while this file
focuses on *saving* it, and the future UI can reuse both.

The text formats (csv, sql) are built as strings by the `to_*` functions, which
makes them trivial to test and lets the CLI either print them to the screen or
save them to a file. SQLite is binary, so it writes straight to a path.
"""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import tempfile


def to_csv_string(rows):
    """Return the rows rendered as CSV text (header row + one row per record).

    Uses Python's built-in csv module, which handles the fiddly parts for us:
    quoting values that contain commas, escaping quotes, and so on. Non-string
    values (ints, dates) are written via their str() form, so a date comes out
    as '2024-03-01'.
    """
    if not rows:
        return ""
    # The first row's keys define the column order and the header.
    fieldnames = list(rows[0].keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def to_sql_string(rows, table="records"):
    """Return a portable SQL dump: one CREATE TABLE plus one INSERT per row.

    This is plain text that most SQL databases (SQLite, Postgres, MySQL) can
    read. Column types are inferred from the values so numbers become INTEGER or
    REAL and everything else becomes TEXT.
    """
    if not rows:
        return ""

    columns = list(rows[0].keys())
    # Infer a column type per column from the first row's values.
    col_types = {name: _sql_type(rows[0][name]) for name in columns}

    lines = []
    col_defs = ", ".join(f'"{name}" {col_types[name]}' for name in columns)
    lines.append(f"CREATE TABLE {table} ({col_defs});")

    col_list = ", ".join(f'"{name}"' for name in columns)
    for row in rows:
        values = ", ".join(_sql_literal(row[name]) for name in columns)
        lines.append(f"INSERT INTO {table} ({col_list}) VALUES ({values});")

    # Trailing newline so the file ends cleanly, like most tools produce.
    return "\n".join(lines) + "\n"


def write_sqlite(rows, path, table="records"):
    """Write the rows into a real SQLite database file at `path`.

    Unlike the text writers this produces a binary .db file you can open with
    any SQLite tool and query right away. We use parameter substitution (the ?
    placeholders) rather than pasting values into the SQL string, which is the
    safe, standard way to insert data.
    """
    if not rows:
        # Still create an empty file so the caller gets a valid, if empty, db.
        sqlite3.connect(path).close()
        return

    columns = list(rows[0].keys())
    col_types = {name: _sql_type(rows[0][name]) for name in columns}

    col_defs = ", ".join(f'"{name}" {col_types[name]}' for name in columns)
    col_list = ", ".join(f'"{name}"' for name in columns)
    placeholders = ", ".join("?" for _ in columns)

    connection = sqlite3.connect(path)
    try:
        connection.execute(f"CREATE TABLE {table} ({col_defs})")
        insert = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        for row in rows:
            connection.execute(insert, [_sqlite_value(row[name]) for name in columns])
        connection.commit()
    finally:
        connection.close()


def to_sqlite_bytes(rows, table="records"):
    """Return a SQLite database as raw bytes, for handing over as a download.

    write_sqlite() writes to a file path, but a web download (or any in-memory
    caller) needs the file's *contents*. So we write to a throwaway temp file,
    read its bytes back, and delete it. Keeps the temp-file dance in one place
    instead of every front door reinventing it.
    """
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = handle.name
    handle.close()
    try:
        write_sqlite(rows, path, table=table)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)


def _sql_type(value):
    """Guess a SQL column type from a Python value. bool is checked before int
    because in Python bool is a subclass of int (True is also an int)."""
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _sql_literal(value):
    """Render one Python value as a SQL literal for the text dump.

    Strings are wrapped in single quotes with any inner quote doubled (the SQL
    way to escape a quote). None becomes NULL, booleans become 1/0, numbers are
    left bare, and anything else (like a date) is treated as text.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _sqlite_value(value):
    """Normalize a value into something sqlite3 can store directly.

    sqlite3 natively handles str, int, float, and None but not things like date
    objects, so we convert anything unusual to its string form. Booleans become
    1/0 to match the INTEGER column type we declared for them.
    """
    if value is None or isinstance(value, (int, float, str)):
        if isinstance(value, bool):
            return 1 if value else 0
        return value
    return str(value)
