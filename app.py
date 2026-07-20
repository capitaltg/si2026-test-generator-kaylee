"""The web front door: a Streamlit app for generating test data.

This is the THIRD way to reach the engine, alongside the Python library and the
CLI. It reimplements nothing: it imports the same generate() and writer
functions the CLI uses and wraps them in a browser page.

How to run it (from the repo root):

    pip install ".[ui]"          # once, to get Streamlit
    streamlit run app.py         # starts a local web server and opens the page

IMPORTANT mental model: Streamlit reruns this whole file top to bottom every
time you interact with the page (click, type, etc.). So the code below is not
"set up once and wait for events"; it is "describe what the page looks like
right now," re-executed on every interaction. Anything that must survive across
those reruns (the schema you are building, the rows you generated) lives in
st.session_state, a dict that persists between reruns.
"""

from __future__ import annotations

import datetime
import os
import tempfile

import streamlit as st

# We reuse the engine and writers exactly as the CLI does. available_field_types
# gives us the live list of types to offer in the type dropdown, so the UI never
# drifts out of sync with what the engine actually supports.
from testgen import (
    available_field_types,
    generate,
    to_csv_string,
    to_sql_string,
    write_sqlite,
)


def sqlite_bytes(rows, table="records"):
    """Produce a SQLite .db as raw bytes so it can be offered as a download.

    write_sqlite() writes to a file path, but a download button needs the file's
    *contents* in memory. So we write to a throwaway temporary file, read its
    bytes back, and delete it. The user never sees this temp file.
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


def new_field():
    """Create one blank column for the schema, with a stable unique id.

    The id is what lets us add and remove columns safely. We key each row's
    widgets off this id (not its position in the list), so deleting the middle
    column never makes the other rows' values jump around.
    """
    fid = st.session_state["next_id"]
    st.session_state["next_id"] += 1
    return {"id": fid, "name": f"column_{fid + 1}", "type": "name"}


# Which engine options each field type accepts. This is the single list that
# both drives the option inputs below and tells the Generate step which options
# to pass through. Types not listed here (name, email, city, ...) take none.
TYPE_OPTIONS = {
    "int": ["min", "max"],
    "float": ["min", "max", "round"],
    "money": ["min", "max"],
    "choice": ["choices", "weights"],
    "date": ["start", "end"],
    "pattern": ["pattern"],
    "constant": ["value"],
    "sequence": ["prefix", "start"],
    "bool": ["true_chance"],
}


def render_options(field, fid):
    """Draw the option inputs for this column's type and store the chosen values
    in field["options"]. A column whose type takes no options draws nothing.

    Each widget's key is namespaced by the field type (opt_<type>_<name>_<id>).
    That matters because two types can share an option name as *different* widget
    kinds (date's "start" is a date picker, sequence's "start" is a number). If
    both used the same key, switching a column's type would reuse one key for two
    widget kinds and crash. Namespacing by type keeps them separate.
    """
    field_type = field["type"]
    opts = field.setdefault("options", {})

    def option_key(option_name):
        return f"opt_{field_type}_{option_name}_{fid}"

    if field_type in ("int", "money"):
        default_lo, default_hi = (0, 100) if field_type == "int" else (1000, 1_000_000)
        c1, c2 = st.columns(2)
        opts["min"] = c1.number_input(
            "min", value=int(opts.get("min", default_lo)), key=option_key("min")
        )
        opts["max"] = c2.number_input(
            "max", value=int(opts.get("max", default_hi)), key=option_key("max")
        )
    elif field_type == "float":
        c1, c2, c3 = st.columns(3)
        opts["min"] = c1.number_input(
            "min", value=float(opts.get("min", 0.0)), key=option_key("min")
        )
        opts["max"] = c2.number_input(
            "max", value=float(opts.get("max", 1.0)), key=option_key("max")
        )
        opts["round"] = c3.number_input(
            "decimals",
            min_value=0,
            max_value=10,
            value=int(opts.get("round", 2)),
            key=option_key("round"),
        )
    elif field_type == "choice":
        # The user types a comma-separated list; we split it into real choices.
        # We keep the raw text too so the box shows exactly what they typed.
        raw = st.text_input(
            "choices (comma-separated)",
            value=opts.get("choices_raw", ""),
            key=option_key("choices"),
            placeholder="Red, Green, Blue",
        )
        opts["choices_raw"] = raw
        opts["choices"] = [c.strip() for c in raw.split(",") if c.strip()]
        wraw = st.text_input(
            "weights (optional, one number per choice)",
            value=opts.get("weights_raw", ""),
            key=option_key("weights"),
            placeholder="e.g. 5, 3, 1",
        )
        opts["weights_raw"] = wraw
        numbers = []
        for piece in wraw.split(","):
            piece = piece.strip()
            if piece:
                try:
                    numbers.append(float(piece))
                except ValueError:
                    pass
        # Only use weights if there is exactly one per choice; otherwise ignore.
        opts["weights"] = (
            numbers if numbers and len(numbers) == len(opts["choices"]) else None
        )
    elif field_type == "date":
        c1, c2 = st.columns(2)
        start = c1.date_input(
            "start",
            value=datetime.date.fromisoformat(opts.get("start", "2000-01-01")),
            key=option_key("start"),
        )
        end = c2.date_input(
            "end",
            value=datetime.date.fromisoformat(opts.get("end", "2025-12-31")),
            key=option_key("end"),
        )
        # Store as ISO strings; the engine's date type accepts those directly.
        opts["start"] = start.isoformat()
        opts["end"] = end.isoformat()
    elif field_type == "pattern":
        opts["pattern"] = st.text_input(
            "pattern  (# = digit, ? = letter)",
            value=opts.get("pattern", "CAGE-#####"),
            key=option_key("pattern"),
        )
    elif field_type == "constant":
        opts["value"] = st.text_input(
            "value (same on every row)",
            value=opts.get("value", ""),
            key=option_key("value"),
        )
    elif field_type == "sequence":
        c1, c2 = st.columns(2)
        opts["prefix"] = c1.text_input(
            "prefix",
            value=opts.get("prefix", ""),
            key=option_key("prefix"),
            placeholder="GS-",
        )
        opts["start"] = c2.number_input(
            "start", value=int(opts.get("start", 1)), key=option_key("start")
        )
    elif field_type == "bool":
        opts["true_chance"] = st.slider(
            "chance of True",
            0.0,
            1.0,
            value=float(opts.get("true_chance", 0.5)),
            key=option_key("true_chance"),
        )


# --- One-time setup of the builder's state -----------------------------------

# This block runs only on the very first load (when "schema" is not in state
# yet). It seeds the page with a couple of example columns so it is not empty.
# On later reruns "schema" already exists, so we leave it alone.
if "schema" not in st.session_state:
    st.session_state["next_id"] = 0
    st.session_state["schema"] = [
        {"id": 0, "name": "vendor", "type": "company"},
        {"id": 1, "name": "amount", "type": "int"},
    ]
    st.session_state["next_id"] = 2  # the next new column will be id 2


# --- The page ----------------------------------------------------------------

st.set_page_config(page_title="testgen", page_icon="🧪")
st.title("🧪 testgen")
st.write("Design a table, then generate realistic, reproducible fake data for it.")

st.subheader("Columns")

types = available_field_types()  # the dropdown options, straight from the engine

# Draw one row of controls per column. We loop over a *copy* of the list
# (list(...)) because a Remove click edits the real list mid-loop; iterating the
# copy avoids "changed size during iteration" surprises.
for field in list(st.session_state["schema"]):
    fid = field["id"]
    # Each column is a bordered card holding its name/type row and, underneath,
    # the option inputs for whatever type is selected.
    with st.container(border=True):
        # Three columns of the layout: name box (wide), type dropdown (wide),
        # and a small remove button. The numbers are relative widths.
        name_col, type_col, remove_col = st.columns([4, 4, 1])

        # value= sets the starting text; key= gives this widget a stable identity
        # so Streamlit remembers it across reruns. Assigning back into
        # field["name"] updates the dict in session_state (field is a reference).
        field["name"] = name_col.text_input(
            "Name",
            value=field["name"],
            key=f"name_{fid}",
            label_visibility="collapsed",
        )
        field["type"] = type_col.selectbox(
            "Type",
            options=types,
            index=types.index(field["type"]),
            key=f"type_{fid}",
            label_visibility="collapsed",
        )
        # A remove button returns True only on the rerun where it was clicked. We
        # rebuild the list without this id, then st.rerun() re-executes the
        # script immediately so the row disappears right away.
        if remove_col.button("✕", key=f"rm_{fid}", help="Remove this column"):
            st.session_state["schema"] = [
                f for f in st.session_state["schema"] if f["id"] != fid
            ]
            st.rerun()

        # The type-specific option inputs (min/max, choices, dates, ...).
        render_options(field, fid)

# The add button sits below the rows. On click we append a fresh column and
# rerun so it shows up immediately.
if st.button("➕ Add column"):
    st.session_state["schema"].append(new_field())
    st.rerun()


st.subheader("Settings")

rows = st.number_input("How many rows", min_value=1, max_value=10000, value=10)
use_seed = st.checkbox("Use a fixed seed (reproducible output)", value=True)
seed = st.number_input("Seed", value=42, disabled=not use_seed) if use_seed else None


# --- Generate ----------------------------------------------------------------


def build_schema():
    """Turn the builder state into the schema the engine expects.

    For each column we keep name + type, then add only the options that belong
    to that type (from TYPE_OPTIONS), skipping any that are unset. The internal
    "id" and the raw text helpers (choices_raw, ...) are left out.
    """
    schema = []
    for f in st.session_state["schema"]:
        spec = {"name": f["name"], "type": f["type"]}
        for key in TYPE_OPTIONS.get(f["type"], []):
            value = f.get("options", {}).get(key)
            if value is not None:
                spec[key] = value
        schema.append(spec)
    return schema


def schema_problems(schema):
    """Return a list of human-readable issues to fix before generating, e.g. a
    choice column with no choices, or min greater than max."""
    problems = []
    for f in schema:
        if f["type"] == "choice" and not f.get("choices"):
            problems.append(f'"{f["name"]}" (choice) needs at least one choice')
        if f["type"] == "pattern" and not f.get("pattern"):
            problems.append(f'"{f["name"]}" (pattern) needs a template')
        if (
            f["type"] in ("int", "float", "money")
            and f.get("min") is not None
            and f.get("max") is not None
            and f["min"] > f["max"]
        ):
            problems.append(f'"{f["name"]}": min is greater than max')
    return problems


if st.button("Generate data", type="primary"):
    schema = build_schema()
    problems = schema_problems(schema)
    if problems:
        st.session_state.pop("rows", None)
        st.warning("Fix these before generating:\n\n- " + "\n- ".join(problems))
    else:
        try:
            st.session_state["rows"] = generate(schema, rows=int(rows), seed=seed)
        except Exception as error:
            # Mainly the empty-schema case lands here now. Show it, don't crash.
            st.session_state.pop("rows", None)
            st.error(f"Could not generate: {error}")


# --- Preview + downloads -----------------------------------------------------

# Lives outside the button block so it stays on screen across reruns (e.g. after
# clicking a download button, which triggers its own rerun).
if "rows" in st.session_state:
    data = st.session_state["rows"]

    st.subheader(f"Preview ({len(data)} rows)")
    st.dataframe(data, width="stretch")

    st.subheader("Download")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "CSV", data=to_csv_string(data), file_name="testgen.csv", mime="text/csv"
        )
    with col2:
        st.download_button(
            "SQL",
            data=to_sql_string(data),
            file_name="testgen.sql",
            mime="text/plain",
        )
    with col3:
        st.download_button(
            "SQLite",
            data=sqlite_bytes(data),
            file_name="testgen.db",
            mime="application/x-sqlite3",
        )
