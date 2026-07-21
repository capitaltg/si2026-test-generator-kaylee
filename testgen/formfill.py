"""Fill real, blank government forms with generated data.

Some documents are published federal forms — SF-30 (contract modification),
SF-1449 (contract award). Rather than draw a look-alike, we fill the ACTUAL
blank form: a fillable PDF (AcroForm) shipped in testgen/forms/. The output is
the real layout with our generated values in the real boxes, so it is as
accurate as a sample can be.

Because a filled form is visually indistinguishable from a genuine one, every
page is stamped with a discreet footer marking it as SIMULATED test data. That
line is the difference between a useful fake sample and a forged government
document, so it is always applied.

This sits alongside the drawn generator in pdf.py: use a real form when one
exists for the document, and the drawn generator for everything else.
"""

from __future__ import annotations

import io
from pathlib import Path

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject, TextStringObject

from .pdf import _latin1

FORMS_DIR = Path(__file__).parent / "forms"

# The mark every filled form carries. Do not remove it: it is what keeps a
# realistic sample from being mistaken for (or used as) a real document.
SIM_FOOTER = "SIMULATED - GENERATED TEST DATA - NOT A GENUINE GOVERNMENT DOCUMENT"


def available_forms():
    """The blank form files bundled with the package (by file name)."""
    return sorted(p.name for p in FORMS_DIR.glob("*.pdf"))


def fill_form_bytes(form_name, values, footer=SIM_FOOTER):
    """Fill a bundled blank form and return the finished PDF as bytes.

    form_name  the bundled file, e.g. "SF30.pdf" (see available_forms()).
    values     {form_field_name: value} for the form's AcroForm fields. Names
               that don't exist on the form are ignored, so a mapping can be
               generous.
    footer     the simulated-data stamp added to every page; keep it on.

    Unknown/covered details handled here: we set NeedAppearances so viewers
    render our values, and drop any XFA packet so the AcroForm values (not a
    stale XFA copy) are what shows.
    """
    path = FORMS_DIR / form_name
    if not path.is_file():
        raise ValueError(
            f"Unknown form '{form_name}'. Available: {', '.join(available_forms())}"
        )

    reader = PdfReader(str(path))
    writer = PdfWriter()
    writer.append(reader)

    # Values come in keyed by field name; the same dict is offered to every page
    # and pypdf fills whichever fields live on that page.
    string_values = {k: _latin1(str(v)) for k, v in values.items() if v is not None}
    for page in writer.pages:
        try:
            writer.update_page_form_field_values(
                page, string_values, auto_regenerate=False
            )
        except Exception:
            # A page with no matching fields raises; that's fine, skip it.
            pass

    acro = writer._root_object.get("/AcroForm")
    if acro is not None:
        acro = acro.get_object()
        acro[NameObject("/NeedAppearances")] = BooleanObject(True)
        if "/XFA" in acro:
            del acro[NameObject("/XFA")]

    if footer:
        for page in writer.pages:
            box = page.mediabox
            overlay = _footer_overlay(float(box.width), float(box.height), footer)
            page.merge_page(overlay)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def fill_forms_bytes(form_name, values_list, footer=SIM_FOOTER):
    """Fill the same blank form once per record and return ONE combined PDF.

    Each record becomes its own copy of the form (its own page(s)) in the output.
    The catch: every copy of a form has identically-named AcroForm fields, so
    naively merging them makes all copies show the last record's values. We avoid
    that by giving each copy's top-level field a unique name prefix, so the merged
    forms stay independent.
    """
    if not values_list:
        raise ValueError("fill_forms_bytes needs at least one record.")
    if len(values_list) == 1:
        return fill_form_bytes(form_name, values_list[0], footer)

    master = PdfWriter()
    for i, values in enumerate(values_list):
        single = fill_form_bytes(form_name, values, footer)
        reader = PdfReader(io.BytesIO(single))
        _prefix_field_names(reader, f"c{i}_")
        master.append(reader)

    # Re-assert NeedAppearances on the merged AcroForm so every copy renders.
    acro = master._root_object.get("/AcroForm")
    if acro is not None:
        acro = acro.get_object()
        acro[NameObject("/NeedAppearances")] = BooleanObject(True)

    out = io.BytesIO()
    master.write(out)
    return out.getvalue()


def _prefix_field_names(reader, prefix):
    """Prefix every top-level AcroForm field name in `reader` in place. Because
    child field names are qualified by their parent, renaming the top-level field
    makes the whole tree's fully-qualified names unique to this copy."""
    root = reader.trailer["/Root"]
    acro = root.get("/AcroForm")
    if acro is None:
        return
    for ref in acro.get_object().get("/Fields", []):
        field = ref.get_object()
        if "/T" in field:
            field[NameObject("/T")] = TextStringObject(prefix + str(field["/T"]))


def _footer_overlay(width_pt, height_pt, text):
    """A single transparent page, same size as the form, carrying only the
    footer text near the bottom. Merged over each form page to stamp it."""
    # PDF units are points; fpdf works in mm, so convert the page size.
    w_mm = width_pt / 72.0 * 25.4
    h_mm = height_pt / 72.0 * 25.4
    pdf = FPDF(unit="mm", format=(w_mm, h_mm))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(150, 30, 30)
    pdf.set_xy(0, h_mm - 6.0)
    pdf.cell(w_mm, 4, _latin1(text), align="C")
    return PdfReader(io.BytesIO(bytes(pdf.output()))).pages[0]
