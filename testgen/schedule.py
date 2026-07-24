"""Labor-rate schedule continuation sheets.

A form face (SF-1449, SF-26) carries only a CLIN *summary* — item number,
description, amount. The negotiated fully-burdened labor rates that back a
T&M / labor CLIN live on a continuation sheet: "Continuation of SF-1449,
Schedule of Line Items and Pricing" for the commercial-items form, or the
Uniform Contract Format's "Section B - Supplies or Services and Prices/Costs"
for a negotiated award (SF-26). Real awards put the rate table there; so do we.

This module draws that sheet from a generated contract's `labor_rates` and
returns it as PDF bytes, ready to append after the filled form. It carries the
same SIMULATED footer as the forms, so an appended page is never mistaken for a
genuine one.
"""

from __future__ import annotations

from fpdf import FPDF

from .formfill import SIM_FOOTER
from .pdf import _latin1

# Labor-line table columns: (heading, width_mm, align, key). Widths sum to the
# portrait-Letter usable width (~194mm at 10mm margins).
_COLS = [
    ("Labor Category (LCAT)", 52, "L", "lcat"),
    ("Loaded Rate/Hr", 26, "R", "loaded_rate"),
    ("Est. Hrs", 18, "R", "est_hours"),
    ("Extended Amount", 30, "R", "amount"),
    ("Min. Education", 28, "L", "min_education"),
    ("Min. Yrs", 15, "C", "min_experience_yrs"),
    ("Clearance", 25, "L", "clearance"),
]


def _money(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return ""


class _SchedulePDF(FPDF):
    """Portrait-Letter page with the page number and the SIMULATED stamp on
    every page (fpdf2 calls footer() as each page is finalised)."""

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(140, 140, 140)
        self.cell(
            0, 5, f"Page {self.page_no()}", align="C", new_x="LMARGIN", new_y="NEXT"
        )
        self.set_font("Helvetica", "B", 6)
        self.set_text_color(150, 30, 30)
        self.cell(0, 4, _latin1(SIM_FOOTER), align="C")
        self.set_text_color(0, 0, 0)


def _cell(pdf, text, w, align):
    pdf.cell(w, 6, _latin1(str(text)), border=1, align=align)


def _row_values(line):
    out = []
    for _, _, _, key in _COLS:
        v = line.get(key)
        if key in ("loaded_rate", "amount"):
            v = _money(v)
        elif key == "est_hours":
            v = f"{int(v):,}" if v else ""
        elif v is None:
            v = ""
        out.append(v)
    return out


def rate_schedule_bytes(contract, form_title, section_label):
    """Draw the labor-rate schedule for a generated contract and return PDF bytes.

    form_title     the sheet title, e.g. "CONTINUATION OF SF-1449".
    section_label  the schedule heading, e.g.
                   "SCHEDULE OF LINE ITEMS AND PRICING".
    """
    pdf = _SchedulePDF(unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    usable = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, _latin1(form_title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, _latin1(section_label), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    ident = (
        f"Contract No.: {contract.get('piid', '')}    "
        f"Contractor: {contract.get('contractor', {}).get('name', '')}    "
        f"Type: {contract.get('contract_type', '')}"
    )
    pdf.cell(0, 5, _latin1(ident), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    def table_header():
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(240, 242, 245)
        for heading, w, _, _ in _COLS:
            pdf.cell(w, 6, _latin1(heading), border=1, align="C", fill=True)
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 8)

    for period in contract.get("periods", []):
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(20, 60, 120)
        span = f"{period.get('pop_start', '')} to {period.get('pop_end', '')}"
        exercised = "" if period.get("exercised") else "  (option not exercised)"
        pdf.cell(
            0,
            7,
            _latin1(f"{period.get('name', '')}  ({span}){exercised}"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(0, 0, 0)

        for clin in period.get("clins", []):
            pdf.set_font("Helvetica", "B", 9)
            head = (
                f"CLIN {clin.get('clin', '')} - {clin.get('title', '')} "
                f"({clin.get('type', '')}) - Ceiling {_money(clin.get('ceiling'))}"
            )
            pdf.multi_cell(usable, 5, _latin1(head), new_x="LMARGIN", new_y="NEXT")

            lines = clin.get("labor_rates") or []
            if not lines:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(110, 110, 110)
                pdf.cell(
                    0,
                    5,
                    _latin1(
                        "    Cost-reimbursable line - no fixed labor rates "
                        "(billed at cost)."
                    ),
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                pdf.set_text_color(0, 0, 0)
                pdf.ln(1)
                continue

            table_header()
            for line in lines:
                for value, (_, w, align, _key) in zip(_row_values(line), _COLS):
                    _cell(pdf, value, w, align)
                pdf.ln(6)

            # Per-CLIN total row (extended amounts sum to the CLIN ceiling).
            total = sum(float(l.get("amount") or 0) for l in lines)
            hours = sum(int(l.get("est_hours") or 0) for l in lines)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(_COLS[0][1], 6, _latin1("CLIN Total"), border=1, align="L")
            pdf.cell(_COLS[1][1], 6, "", border=1)
            pdf.cell(_COLS[2][1], 6, _latin1(f"{hours:,}"), border=1, align="R")
            pdf.cell(_COLS[3][1], 6, _latin1(_money(total)), border=1, align="R")
            rest = sum(w for _, w, _, _ in _COLS[4:])
            pdf.cell(rest, 6, "", border=1)
            pdf.ln(8)
            pdf.set_font("Helvetica", "", 8)

    return bytes(pdf.output())


def sf1449_continuation(contract):
    """The SF-1449 pricing continuation sheet."""
    return rate_schedule_bytes(
        contract,
        "CONTINUATION OF SF-1449",
        "SCHEDULE OF LINE ITEMS AND PRICING",
    )


def sf26_section_b(contract):
    """The SF-26 Uniform Contract Format Section B schedule."""
    return rate_schedule_bytes(
        contract,
        "SECTION B - SUPPLIES OR SERVICES AND PRICES/COSTS",
        "LABOR RATE SCHEDULE (FULLY BURDENED)",
    )
