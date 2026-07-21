"""Generate *fillable* PDF documents — flat docs with real, editable form fields.

Some documents have no official government form to fill (a funding summary, an
award letter, an internal record sheet). We still want them to be editable after
generation, the same way the real SF-1449/SF-30 are: click a field, change the
value. fpdf2 (used by pdf.py for static drawn PDFs) cannot create interactive
form fields, so those documents are built here with reportlab, whose canvas can
lay down AcroForm text fields pre-filled with the generated value.

A caller supplies a `blocks` function: record -> (title, [block, ...]). Each
block is a small dict describing one piece of the layout (a labelled field, a
side-by-side pair, a prose area, or a table). The renderer walks the blocks top
to bottom, drawing labels and placing editable fields. Field names are prefixed
per record so multiple records in one PDF never collide.

Every page carries the same SIMULATED footer as the real filled forms.
"""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from .formfill import SIM_FOOTER
from .pdf import _latin1

PAGE_W, PAGE_H = LETTER
MARGIN = 54.0  # 0.75"
CONTENT_W = PAGE_W - 2 * MARGIN


def render_fillable(records, blocks_fn, footer=SIM_FOOTER):
    """Render one fillable page-set per record into a single PDF (bytes)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    for i, record in enumerate(records):
        title, blocks = blocks_fn(record)
        _draw_page(c, i, title, blocks, footer)
        c.showPage()
    c.save()
    return buf.getvalue()


def _draw_page(c, idx, title, blocks, footer):
    form = c.acroForm
    y = PAGE_H - MARGIN
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGIN, y, _latin1(title))
    y -= 9
    c.setLineWidth(0.6)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 26
    for block in blocks:
        y = _draw_block(c, form, idx, block, y)
        if y < MARGIN + 30:  # ran out of room; continue on a fresh page
            _footer(c, footer)
            c.showPage()
            y = PAGE_H - MARGIN
    _footer(c, footer)


def _footer(c, footer):
    if not footer:
        return
    c.setFont("Helvetica-Bold", 6)
    c.setFillColorRGB(0.59, 0.12, 0.12)
    c.drawCentredString(PAGE_W / 2, 26, _latin1(footer))
    c.setFillGray(0)


def _label(c, x, y, text):
    c.setFont("Helvetica", 7.5)
    c.setFillGray(0.42)
    c.drawString(x, y, _latin1(text).upper())
    c.setFillGray(0)


def _field(form, name, value, x, y, w, h, multiline=False, size=10):
    form.textfield(
        name=name,
        value=_latin1(str(value if value is not None else "")),
        x=x,
        y=y,
        width=w,
        height=h,
        fontSize=size,
        borderStyle="inset",
        borderWidth=0.5,
        forceBorder=True,
        fieldFlags="multiline" if multiline else "",
    )


def _draw_block(c, form, idx, b, y):
    t = b["type"]
    if t == "field":
        lines = b.get("lines", 1)
        fh = 16 if lines == 1 else 14 * lines
        _label(c, MARGIN, y, b["label"])
        y -= 13
        _field(
            form,
            f"r{idx}_{b['name']}",
            b.get("value", ""),
            MARGIN,
            y - fh,
            CONTENT_W,
            fh,
            multiline=lines > 1,
        )
        return y - fh - 12
    if t == "pair":
        gap = 16
        w = (CONTENT_W - gap) / 2
        for j, (name, label, value) in enumerate(b["fields"]):
            x = MARGIN + j * (w + gap)
            _label(c, x, y, label)
            _field(form, f"r{idx}_{name}", value, x, y - 13 - 16, w, 16)
        return y - 13 - 16 - 12
    if t == "prose":
        h = b.get("height", 300)
        _label(c, MARGIN, y, b.get("label", "Body"))
        y -= 13
        _field(
            form,
            f"r{idx}_{b['name']}",
            b.get("value", ""),
            MARGIN,
            y - h,
            CONTENT_W,
            h,
            multiline=True,
            size=10,
        )
        return y - h - 12
    if t == "table":
        return _draw_table(c, form, idx, b, y)
    return y


def _draw_table(c, form, idx, b, y):
    cols = b["columns"]  # list of (key, label, width_fraction)
    if b.get("label"):
        _label(c, MARGIN, y, b["label"])
        y -= 15
    c.setFont("Helvetica-Bold", 7)
    c.setFillGray(0.3)
    x = MARGIN
    for key, label, frac in cols:
        c.drawString(x + 2, y, _latin1(label).upper())
        x += frac * CONTENT_W
    c.setFillGray(0)
    y -= 13
    rh = 15
    for ri, row in enumerate(b["rows"]):
        x = MARGIN
        for key, label, frac in cols:
            w = frac * CONTENT_W
            _field(
                form,
                f"r{idx}_{b['name']}_{ri}_{key}",
                row.get(key, ""),
                x,
                y - rh,
                w - 3,
                rh,
                size=8,
            )
            x += w
        y -= rh + 2
    return y - 12
