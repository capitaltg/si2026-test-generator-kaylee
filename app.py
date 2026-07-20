"""The web front door: a small Streamlit app for generating test data.

This is the THIRD way to reach the engine, alongside the Python library and the
CLI. It does not reimplement anything. It imports the exact same generate() and
writer functions the CLI uses and simply wraps them in a browser page.

How to run it (from the repo root):

    pip install ".[ui]"          # once, to get Streamlit
    streamlit run app.py         # starts a local web server and opens the page

IMPORTANT mental model: Streamlit reruns this whole file top to bottom every
time you interact with the page (click, type, etc.). So the code below is not
"set up once and wait for events"; it is "describe what the page looks like
right now," re-executed on every interaction. We use st.session_state (a dict
that survives across those reruns) to remember the generated rows so the preview
and the download buttons always show the same data.
"""

from __future__ import annotations

import os
import tempfile

import streamlit as st

# We reuse the engine and writers exactly as the CLI does. EXAMPLE_SCHEMA is the
# same demo schema the CLI prints; in the next ticket we replace it with an
# interactive builder, but for this scaffold it gives us real data to show.
from testgen import generate, to_csv_string, to_sql_string, write_sqlite
from testgen.cli import EXAMPLE_SCHEMA


def sqlite_bytes(rows, table="records"):
    """Produce a SQLite .db as raw bytes so it can be offered as a download.

    write_sqlite() writes to a file path, but a download button needs the file's
    *contents* in memory. So we write to a throwaway temporary file, read its
    bytes back, and delete it. The user never sees this temp file; it exists just
    long enough to capture the bytes.
    """
    # Make a temp path, then close it immediately so sqlite can open it itself.
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = handle.name
    handle.close()
    try:
        write_sqlite(rows, path, table=table)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)


# --- The page itself ---------------------------------------------------------

# st.set_page_config just sets the browser tab title and layout. Must be the
# first Streamlit call on the page.
st.set_page_config(page_title="testgen", page_icon="🧪")

st.title("🧪 testgen")
st.write(
    "Generate realistic, reproducible fake test data, then download it as CSV, "
    "SQL, or a SQLite database."
)

# These two widgets draw controls AND return their current values in one step.
# number_input draws a number box; whatever the user has typed comes back here.
rows = st.number_input("How many rows", min_value=1, max_value=10000, value=10)

# Seed is optional. A checkbox decides whether we pass a seed at all. With a
# seed, the same settings always produce the same data (reproducible). Without
# one, every generation is fresh and different.
use_seed = st.checkbox("Use a fixed seed (reproducible output)", value=True)
seed = st.number_input("Seed", value=42, disabled=not use_seed) if use_seed else None

# A button returns True only on the rerun where it was just clicked. So this
# block runs once, right after the click: we generate the data and stash it in
# session_state so it survives later reruns (like clicking a download button).
if st.button("Generate data", type="primary"):
    st.session_state["rows"] = generate(EXAMPLE_SCHEMA, rows=int(rows), seed=seed)

# On every rerun, if we have generated rows stored, draw the preview and the
# download buttons. This lives OUTSIDE the button block on purpose: after you
# click a download button the script reruns, the button above is no longer
# "just clicked" (so it's False), but the stored rows are still here.
if "rows" in st.session_state:
    data = st.session_state["rows"]

    st.subheader(f"Preview ({len(data)} rows)")
    # st.dataframe renders a list of dicts as an interactive, scrollable grid.
    st.dataframe(data, use_container_width=True)

    st.subheader("Download")
    # Each download_button needs its file contents ready at draw time (there is
    # no "generate on click" callback), so we build all three formats up front
    # from the same stored rows. Three columns just lay the buttons out in a row.
    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "CSV",
            data=to_csv_string(data),
            file_name="testgen.csv",
            mime="text/csv",
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
else:
    # Shown before the first generation, so the page is not just an empty form.
    st.info("Set your options above and click **Generate data** to begin.")
