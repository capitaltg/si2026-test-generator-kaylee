"""PDF output writers: turn generated rows into real PDF documents.

`writers.py` covers the plain-text and database formats (CSV, SQL, SQLite) and
stays pure-stdlib on purpose. PDFs are different enough — and important enough to
this tool — to live on their own. Producing believable sample PDFs is a core
goal here: a document-ingest/OCR pipeline (like Runway's) needs realistic files
to test against, so this is a first-class output, not an optional extra. That is
why fpdf2 (a small, pure-Python PDF library) is a core dependency.

There are two flavours, and they answer two different needs. Both return raw
`bytes` — like `to_sqlite_bytes` — so any front door (library, CLI, or a web
download) can hand the result straight to a caller without a temp file:

    to_pdf_table_bytes(fields, rows)          The whole dataset as one paginated
                                              table — a report. Works for any
                                              schema; think "the grid, as a PDF".

    to_pdf_docs_bytes(fields, rows, config)   One page per row, each row rendered
                                              as a labelled record sheet. This is
                                              the realistic per-record document
                                              (e.g. a contract award notice) that
                                              a PDF-ingest feature needs to chew
                                              on.

Nothing here is GovCon-specific. The document layout is driven entirely by the
fields and an optional light `config` (a title, an optional per-row identifier
field, and which fields sit in a header block). A GovCon award letter is just a
particular config — not special-cased code — which keeps this reusable for any
schema and honours the "generic engine, swappable preset" split.

We deliberately do NOT build a template language. The config is a handful of
plain keys; if richer templating is ever needed it can grow later.
"""

from __future__ import annotations

import re

from fpdf import FPDF

# Default ceiling for document mode. One page per row means 10k rows would be a
# 10k-page PDF, which is almost never what someone wants and is slow to build.
# Callers that really want more can raise it; the web layer surfaces the error.
MAX_DOC_PAGES = 500


class _PDF(FPDF):
    """FPDF with a shared page-number footer.

    fpdf2 calls footer() automatically as each page ends (on add_page and again
    when the document is finalised), so defining it here gives every page a
    "Page N" line for free, in both the table and document writers.
    """

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)


def to_pdf_table_bytes(fields, rows, title="Data export"):
    """Render the rows as a single paginated table and return the PDF bytes.

    Landscape orientation (tables are wide), the header row repeats at the top of
    every page, and long cell values are truncated to fit their column so the
    grid stays aligned. Works for any schema — it is just the data as a report.
    """
    title = _latin1(title)
    columns = _column_names(fields, rows)
    pdf = _new_pdf("L", title)
    # Manage page breaks by hand so we can repeat the header row on each new
    # page; auto page-break would split a row and skip the header redraw.
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, title)
    pdf.ln(12)

    if not columns:
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 8, "No columns to show.")
        return _emit(pdf)

    usable = pdf.w - pdf.l_margin - pdf.r_margin
    col_w = usable / len(columns)
    row_h = 7.0
    # Roughly how many characters fit in a column at the 9pt body font. Purely
    # cosmetic: it keeps a long value from spilling past its cell border.
    max_chars = max(4, int(col_w / 1.9))
    bottom = pdf.h - pdf.b_margin

    def draw_header():
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(240, 242, 245)
        for name in columns:
            pdf.cell(
                col_w,
                row_h,
                _fit(_cell_text(name), max_chars),
                border=1,
                align="L",
                fill=True,
            )
        pdf.ln(row_h)
        pdf.set_font("Helvetica", "", 9)

    draw_header()
    for row in rows:
        if pdf.get_y() + row_h > bottom:
            pdf.add_page()
            draw_header()
        for name in columns:
            pdf.cell(
                col_w,
                row_h,
                _fit(_cell_text(row.get(name)), max_chars),
                border=1,
                align="L",
            )
        pdf.ln(row_h)

    return _emit(pdf)


def to_pdf_docs_bytes(fields, rows, config=None, max_pages=MAX_DOC_PAGES):
    """Render one page per row as a labelled record sheet; return the PDF bytes.

    Each page shows a title, an optional header block, and the row's fields as
    label -> value lines. The look is controlled by a light `config` dict, all
    keys optional:

        title         document title printed at the top   (default "Record")
        title_field   a field whose value is appended to the title per row,
                      e.g. a contract id, so pages are distinguishable
        style         "sheet" (default) label -> value record; "letter" prose;
                      "form" a clean, official-looking federal contract form
        header_fields for sheet/letter, a header block; for form, the numbered boxes
        body_fields   the main body; for form, the schedule table. When omitted,
                      every field not in header_fields is used, in schema order
        body_template prose text with {field} placeholders; with style "letter"
                      the body is this filled-in paragraph instead of label lines

    With no config at all this still produces a clean generic sheet for any
    schema. Raises ValueError if the row count would exceed `max_pages`, since
    document mode makes exactly one page per row.
    """
    if len(rows) > max_pages:
        raise ValueError(
            f"Document mode makes one page per row, and {len(rows)} rows exceeds "
            f"the {max_pages}-page limit. Reduce the row count or use the table PDF."
        )

    config = config or {}
    title = _latin1(config.get("title") or "Record")
    title_field = config.get("title_field")
    header_fields = config.get("header_fields") or []
    all_names = _column_names(fields, rows)
    style = config.get("style")
    body_template = config.get("body_template")
    body_fields = config.get("body_fields")
    if body_fields is None:
        body_fields = [name for name in all_names if name not in header_fields]

    pdf = _new_pdf("P", title)
    pdf.set_auto_page_break(auto=True, margin=18)

    if not rows:
        # Still emit a valid one-page PDF so callers always get a real file.
        pdf.add_page()
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 10, "No records.")
        return _emit(pdf)

    for row in rows:
        pdf.add_page()

        # "form" is a whole-page federal-form layout, so it takes over the page
        # instead of the title/header/body flow below.
        if style == "form":
            _doc_form(pdf, row, title, header_fields, body_fields)
            continue

        heading = title
        if title_field and row.get(title_field) not in (None, ""):
            heading = f"{title} - {_cell_text(row.get(title_field))}"
        pdf.set_font("Helvetica", "B", 17)
        pdf.multi_cell(0, 9, heading)
        pdf.ln(2)

        # A thin rule under the title, like a letterhead.
        pdf.set_draw_color(150, 150, 150)
        y = pdf.get_y()
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(5)

        if header_fields:
            _doc_lines(pdf, row, header_fields)
            pdf.ln(3)
        if body_template:
            _doc_prose(pdf, row, body_template)
        else:
            _doc_lines(pdf, row, body_fields)

    return _emit(pdf)


# --- internals ---------------------------------------------------------------


def _new_pdf(orientation, title):
    """A configured _PDF (mm units, A4, sensible margins, page-number footer)."""
    pdf = _PDF(orientation=orientation, unit="mm", format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_title(title)
    return pdf


def _emit(pdf):
    """fpdf2 returns a bytearray from output(); hand back plain bytes."""
    return bytes(pdf.output())


def _doc_lines(pdf, row, names):
    """Render `names` as bold-label -> value rows on the current document page.

    The label sits in a fixed-width left column and the value wraps in the
    remaining width. We set the x/y of each cell explicitly rather than lean on
    fpdf2's post-cell cursor movement: drawing the label first and then jumping
    the cursor back to the row's top-left before drawing the value keeps the two
    columns aligned no matter how many lines the value wraps to.
    """
    left = pdf.l_margin
    label_w = 46.0
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    value_w = max(20.0, usable - label_w)
    line_h = 6.5
    for name in names:
        value = row.get(name)
        # A nested list (e.g. CLINs): show the label, then a line-item table.
        if isinstance(value, list):
            pdf.set_xy(left, pdf.get_y())
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(usable, line_h, _humanize(name), align="L")
            pdf.set_y(_record_table(pdf, value, left, usable, pdf.get_y()) + 2.0)
            continue
        y0 = pdf.get_y()
        # Label in the left column.
        pdf.set_xy(left, y0)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(label_w, line_h, _humanize(name), align="L")
        label_bottom = pdf.get_y()
        # Value in the right column, starting back at the row's top.
        pdf.set_xy(left + label_w, y0)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(20, 20, 20)
        value = _cell_text(row.get(name))
        pdf.multi_cell(value_w, line_h, value if value != "" else "-", align="L")
        value_bottom = pdf.get_y()
        # Continue below whichever column ran longer, with a little breathing room.
        pdf.set_y(max(label_bottom, value_bottom) + 2.0)
    pdf.set_text_color(0, 0, 0)


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _fill_template(template, row):
    """Substitute {field} placeholders in `template` with this row's values.

    A token whose name isn't a field in the row is left as-is, so a typo shows
    up in the output instead of silently vanishing. Newlines in the template are
    kept, so blank lines become paragraph breaks.
    """

    def replace(match):
        key = match.group(1).strip()
        if key in row:
            return _cell_text(row.get(key))
        return match.group(0)

    return _latin1(_PLACEHOLDER.sub(replace, template))


def _doc_prose(pdf, row, template):
    """Render a filled-in prose template as the document body (a letter)."""
    pdf.set_xy(pdf.l_margin, pdf.get_y())
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(20, 20, 20)
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.multi_cell(usable, 6.5, _fill_template(template, row), align="L")
    pdf.set_text_color(0, 0, 0)


_AMOUNT_HINTS = ("amount", "total", "price", "value", "cost", "obligated", "ceiling")


def _looks_like_amount(name):
    """True if a field name reads like money (used to right-align columns and
    find the form's total)."""
    low = str(name).lower()
    return any(hint in low for hint in _AMOUNT_HINTS)


def _guess_amount_field(names):
    """The first field that looks like a money total, for the form's total box."""
    for name in names:
        if _looks_like_amount(name):
            return name
    return None


def _record_table(pdf, items, left, width, y):
    """Draw a list of child records (e.g. CLIN line items) as a bordered table:
    a header row of column names, then one row per item. Money-looking columns
    are right-aligned. Returns the y just below the table.

    If the rows would run past the usable page area they are truncated with a
    visible "+N more" note rather than drawn off-page — realistic contracts have
    few enough line items to fit, and truncation is surfaced, never silent.
    """
    if not items:
        pdf.rect(left, y, width, 6)
        pdf.set_xy(left + 2, y + 1.6)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(width - 4, 3, "(no line items)")
        pdf.set_text_color(0, 0, 0)
        return y + 6

    columns = list(items[0].keys())
    col_w = width / max(1, len(columns))
    hrow, rrow = 6.0, 6.0
    bottom = pdf.h - pdf.b_margin - 22  # leave room for the total + signature

    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(60, 60, 60)
    for ci, col in enumerate(columns):
        pdf.rect(left + ci * col_w, y, col_w, hrow)
        pdf.set_xy(left + ci * col_w + 1.5, y + 1.7)
        pdf.cell(
            col_w - 3, 3, _fit(_humanize(col).upper(), max(3, int((col_w - 3) / 1.5)))
        )
    y += hrow

    aligns = ["R" if _looks_like_amount(col) else "L" for col in columns]
    shown = 0
    for item in items:
        if y + rrow > bottom:
            break
        for ci, (col, align) in enumerate(zip(columns, aligns)):
            x = left + ci * col_w
            pdf.rect(x, y, col_w, rrow)
            pdf.set_xy(x + 1.5, y + 1.6)
            pdf.set_font("Courier", "", 8.5)
            pdf.set_text_color(20, 20, 20)
            pdf.cell(
                col_w - 3,
                3,
                _fit(_cell_text(item.get(col)), max(3, int((col_w - 3) / 1.7))),
                align=align,
            )
        y += rrow
        shown += 1

    if shown < len(items):
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(120, 120, 120)
        pdf.set_xy(left + 1.5, y + 1)
        pdf.cell(
            width - 3,
            3,
            f"... +{len(items) - shown} more line items (see the data export for all)",
        )
        y += 5
    pdf.set_text_color(0, 0, 0)
    return y


def _form_box(pdf, x, y, w, h, label, value):
    """A bordered federal-form box: a small caption top-left, value below in a
    monospace (typewriter) font, the way the real standard forms print."""
    pdf.rect(x, y, w, h)
    pdf.set_xy(x + 1.8, y + 1.3)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(w - 3, 3, _fit(_latin1(label), max(4, int((w - 3) / 1.4))))
    pdf.set_xy(x + 2.5, y + 5)
    pdf.set_font("Courier", "", 9)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(w - 5, 4, _fit(_cell_text(value), max(4, int((w - 5) / 1.9))))


def _doc_form(pdf, row, title, header_fields, body_fields):
    """Render one record as a clean, official-looking federal contract form.

    Modelled on the spirit of Standard Form 1449 (not a pixel replica): a titled
    banner, a grid of numbered boxes from the header fields, a bordered
    "schedule" of the body fields, a total-award box when an amount-like field is
    present, and a signature block. A visible "SIMULATED / TEST DATA" line keeps
    it from being mistaken for a genuine government document.
    """
    left = pdf.l_margin
    width = pdf.w - pdf.l_margin - pdf.r_margin
    y = pdf.t_margin
    # The form is laid out by hand with rect()/explicit y, so turn off automatic
    # page breaks; the line-item table manages its own overflow.
    pdf.set_auto_page_break(False)

    # Title banner + honest "simulated" caption.
    pdf.rect(left, y, width, 11)
    pdf.set_xy(left, y + 1.5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(width, 6, _latin1(title.upper()), align="C")
    pdf.set_xy(left, y + 7)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(
        width,
        3,
        "SIMULATED FORM - GENERATED TEST DATA - NOT AN OFFICIAL DOCUMENT",
        align="C",
    )
    pdf.set_text_color(20, 20, 20)
    y += 11

    # Numbered header boxes, two per row.
    box_h = 13.0
    half = width / 2
    for i in range(0, len(header_fields), 2):
        pair = header_fields[i : i + 2]
        for j, name in enumerate(pair):
            bw = half if len(pair) == 2 else width
            label = f"{i + j + 1}. {_humanize(name).upper()}"
            _form_box(pdf, left + j * half, y, bw, box_h, label, row.get(name))
        y += box_h

    # Schedule of body fields as a bordered label | value table.
    pdf.rect(left, y, width, 6)
    pdf.set_xy(left + 1.8, y + 1.4)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(width, 3, "SCHEDULE OF SUPPLIES / SERVICES")
    y += 6
    label_w = 60.0
    line_h = 7.0
    for name in body_fields:
        value = row.get(name)
        # A nested list (e.g. CLINs) becomes a captioned line-item table.
        if isinstance(value, list):
            pdf.set_xy(left + 2, y + 1)
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_text_color(70, 70, 70)
            pdf.cell(width - 4, 3, _humanize(name).upper())
            y = _record_table(pdf, value, left, width, y + 5)
            continue
        pdf.rect(left, y, label_w, line_h)
        pdf.rect(left + label_w, y, width - label_w, line_h)
        pdf.set_xy(left + 2, y + 1.7)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(70, 70, 70)
        pdf.cell(label_w - 4, 4, _fit(_humanize(name), 34))
        pdf.set_xy(left + label_w + 2, y + 1.7)
        pdf.set_font("Courier", "", 9)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(width - label_w - 4, 4, _fit(_cell_text(value), 62))
        y += line_h

    # Total-award box, if a money-looking field exists.
    amount = _guess_amount_field(header_fields + body_fields) or _guess_amount_field(
        list(row.keys())
    )
    if amount:
        tw = 72.0
        _form_box(
            pdf, left + width - tw, y + 3, tw, 12, "TOTAL AWARD AMOUNT", row.get(amount)
        )
        y += 15

    # Signature block pinned near the bottom of the page.
    sig_y = max(y + 4, pdf.h - pdf.b_margin - 20)
    _form_box(
        pdf, left, sig_y, width * 0.65, 18, "SIGNATURE OF CONTRACTING OFFICER", ""
    )
    _form_box(pdf, left + width * 0.65, sig_y, width * 0.35, 18, "DATE SIGNED", "")
    pdf.set_text_color(0, 0, 0)


def _column_names(fields, rows):
    """Column order for output: the schema's field names, falling back to the
    keys of the first row when no schema was passed."""
    names = [f["name"] for f in (fields or []) if isinstance(f, dict) and f.get("name")]
    if names:
        return names
    return list(rows[0].keys()) if rows else []


def _latin1(text):
    """Coerce text into the range the built-in PDF fonts support.

    fpdf2's core fonts (Helvetica etc.) are latin-1 only, so any character
    outside that range — a curly quote, an em-dash, an emoji, many accents —
    raises during rendering. Because we render *generated* data, which can hold
    anything, we replace unsupported characters with '?' so a stray value never
    crashes an export. (Using a Unicode TTF font would avoid the substitution
    but pull in a font file; that is overkill for sample data.)
    """
    return text.encode("latin-1", "replace").decode("latin-1")


def _cell_text(value):
    """A value as safe display text: None becomes empty, everything else str()."""
    if value is None:
        return ""
    return _latin1(str(value))


def _fit(text, max_chars):
    """Truncate text to fit a table cell, adding an ellipsis when clipped."""
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return text[: max_chars - 1] + "..."


def _humanize(name):
    """Turn a field name into a readable label: contract_id -> 'Contract Id'."""
    return _latin1(name.replace("_", " ").replace("-", " ").strip().title())
