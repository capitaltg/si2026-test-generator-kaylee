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


def _pct(value):
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return ""


def _summary_rows(clin, pricing, hours, extended):
    """The summary rows that close a CLIN's pricing table, as
    (label, hours, amount, bold) tuples.

    These belong *in* the table, continuing the Extended Amount column, because
    that is where a real Section B pricing exhibit states them — a cost-type
    award states estimated cost and fee as their own priced lines footing to the
    CLIN amount (FAR 16.306), not as a sentence underneath the table. Only the
    hours-bearing row carries the hours figure; the element rows are dollar
    figures and a real sheet leaves that column blank on them.

    #57 extends this same block upward: the indirect-pool rows of the cost
    buildup land between the labor lines and these totals, at which point the
    labor column becomes direct cost and this block foots to it naturally.
    """
    element = (pricing or {}).get("fee_element")
    rate = _pct(clin.get("fee_rate"))
    if clin.get("estimated_cost") is not None:
        label = {
            "fixed_fee": "Fixed Fee",
            "award_fee": "Base Fee + Award Fee Pool",
            "target_fee": "Target Fee",
        }.get(element, "Fee")
        cost_label = (
            "Target Cost" if element == "target_fee" else "Total Estimated Cost"
        )
        return [
            (cost_label, hours, clin["estimated_cost"], False),
            (f"{label} ({rate})", None, clin.get("fee") or 0, False),
            ("Total CLIN Amount (Cost + Fee)", None, extended, True),
        ]
    if clin.get("target_profit") is not None:
        return [
            ("Target Cost", hours, clin["target_cost"], False),
            (f"Target Profit ({rate})", None, clin["target_profit"], False),
            ("Target Price", None, clin.get("target_price"), True),
            ("Price Ceiling (FAR 16.403)", None, clin.get("ceiling_price"), False),
        ]
    if clin.get("firm_price") is not None:
        # A firm-fixed-price CLIN states one figure. Naming it "Firm-Fixed Price"
        # instead of "CLIN Total" is the whole statement — there is no ceiling,
        # no fee and no second number to reconcile against.
        return [("Firm-Fixed Price", hours, clin["firm_price"], True)]
    if clin.get("profit_in_rates"):
        return [("Total Ceiling Price", hours, extended, True)]
    return [("CLIN Total", hours, extended, True)]


def _table_note(clin):
    """The note a sheet carries under a CLIN's table, where a real award puts
    one — a short qualification of the rates above, not a restatement of them."""
    if clin.get("profit_in_rates"):
        return (
            "Note: the fixed hourly rates above are inclusive of all direct and "
            "indirect costs and profit. Materials and other direct costs are "
            "reimbursed separately at cost, without fee (FAR 52.232-7(a))."
        )
    if clin.get("target_profit") is not None:
        return (
            "Note: costs incurred above the price ceiling are borne by the "
            "Contractor; profit adjusts by the share ratio (FAR 16.403)."
        )
    return ""


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
    pricing = contract.get("pricing") or {}
    ident = (
        f"Contract No.: {contract.get('piid', '')}    "
        f"Contractor: {contract.get('contractor', {}).get('name', '')}    "
        f"Type: {contract.get('contract_type', '')}"
    )
    pdf.cell(0, 5, _latin1(ident), new_x="LMARGIN", new_y="NEXT")
    # The type spelled out with its FAR authority, the way a negotiated award
    # names the type it was awarded under.
    if pricing.get("label"):
        far = f" (FAR {pricing['far']})" if pricing.get("far") else ""
        pdf.cell(
            0,
            5,
            _latin1(f"Contract Type: {pricing['label']}{far}"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
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
            # What the CLIN's headline figure IS depends on the type. A firm-
            # fixed-price line has a price, not a ceiling; a cost-type line's
            # ceiling is an estimate the government may be asked to raise.
            amount_label = "Ceiling"
            if clin.get("firm_price") is not None:
                amount_label = "Firm-Fixed Price"
            elif clin.get("target_price") is not None:
                amount_label = "Target Price"
            head = (
                f"CLIN {clin.get('clin', '')} - {clin.get('title', '')} "
                f"({clin.get('type', '')}) - {amount_label} "
                f"{_money(clin.get('ceiling'))}"
            )
            # Award-time obligation only. This schedule is an attachment to the
            # award form, which is signed once — it cannot cite money that later
            # SF-30 mods obligated. `funded` (the cumulative as of today) belongs
            # on a funding summary, not here.
            #
            # And it is stated only when it is a *second* fact. A fully funded
            # fixed-price CLIN obligates its whole price at award, so printing
            # "Firm-Fixed Price $X - Obligated at award $X" states one number
            # twice and invites the reader to look for the difference between
            # them. The ACRN still prints, because which accounting line funds
            # the CLIN is genuinely additional; the amount does not.
            funded = clin.get("funded_at_award")
            acrn = clin.get("acrn")
            if funded:
                same = abs(float(funded) - float(clin.get("ceiling") or 0)) < 0.5
                if not same:
                    head += f" - Obligated at award {_money(funded)}"
                head += f" (ACRN {acrn})" if acrn else ""
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

            # The summary rows that close the table: the cost elements this
            # CLIN's type states, priced in the same column as the labor lines
            # above them (extended amounts foot to the CLIN's stated amount).
            total = sum(float(l.get("amount") or 0) for l in lines)
            hours = sum(int(l.get("est_hours") or 0) for l in lines)
            rest = sum(w for _, w, _, _ in _COLS[4:])
            for label, row_hours, amount, bold in _summary_rows(
                clin, pricing, hours, total
            ):
                pdf.set_font("Helvetica", "B" if bold else "", 8)
                pdf.cell(_COLS[0][1], 6, _latin1(label), border=1, align="L")
                pdf.cell(_COLS[1][1], 6, "", border=1)
                pdf.cell(
                    _COLS[2][1],
                    6,
                    _latin1(f"{row_hours:,}") if row_hours else "",
                    border=1,
                    align="R",
                )
                pdf.cell(_COLS[3][1], 6, _latin1(_money(amount)), border=1, align="R")
                pdf.cell(rest, 6, "", border=1)
                pdf.ln(6)
            pdf.set_font("Helvetica", "", 8)

            note = _table_note(clin)
            if note:
                pdf.ln(1)
                pdf.set_font("Helvetica", "I", 7)
                pdf.set_text_color(90, 90, 90)
                pdf.multi_cell(usable, 4, _latin1(note), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("Helvetica", "", 8)
            pdf.ln(3)

    # Accounting and Appropriation Data: which ACRN funds which CLIN and the
    # dollars obligated against it. This is the award's funding citation — the
    # per-CLIN obligated amounts a contractor bills against, which SF-30 mods
    # later amend. Only funded (exercised, obligated) CLINs appear.
    # Only the CLINs this award itself obligated money against. An option period
    # is priced in the schedule above but carries no accounting data until the
    # SF-30 that exercises it — so it does not appear here.
    funded_clins = [
        c
        for p in contract.get("periods", [])
        for c in p.get("clins", [])
        if (c.get("funded_at_award") or 0) > 0
    ]
    if funded_clins:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(20, 60, 120)
        pdf.cell(
            0,
            7,
            _latin1("ACCOUNTING AND APPROPRIATION DATA"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(0, 0, 0)
        # Each ACRN is a detached prefix to its accounting classification citation
        # (line of accounting), with the CLIN it funds and the obligated amount
        # (DFARS 204.7107). One funded CLIN per line, the way an award prints it.
        pdf.set_font("Helvetica", "", 8)
        total_obligated = 0.0
        for c in funded_clins:
            funded = float(c.get("funded_at_award") or 0)
            total_obligated += funded
            line = (
                f"ACRN {c.get('acrn') or '--'}: {c.get('loa') or ''}    "
                f"CLIN {c.get('clin') or ''}    Obligated {_money(funded)}"
            )
            pdf.multi_cell(usable, 5, _latin1(line), new_x="LMARGIN", new_y="NEXT")
        # Total presently allotted, and the clause that limits payment against
        # it. Which clause that is depends on the contract type as much as on
        # the funding profile — a fully funded cost contract is limited by
        # 52.232-20 (Limitation of Cost) and an incrementally funded one by
        # 52.232-22 (Limitation of Funds), while a fixed-price award is under
        # neither and printing one on it is a contradiction on the face of the
        # form. `pricing` has already resolved this; the ceiling comparison
        # stays as the check that the award really is short of its allotment.
        allotted_ceiling = sum(float(c.get("ceiling") or 0) for c in funded_clins)
        statement = f"Total amount obligated by this award: {_money(total_obligated)}."
        clause = pricing.get("funding_clause")
        if clause and total_obligated + 0.5 < allotted_ceiling:
            statement += f" Incremental funding is subject to {clause}."
        elif clause:
            statement += f" Payment is subject to {clause}."
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 8)
        pdf.multi_cell(
            usable,
            5,
            _latin1(statement),
            new_x="LMARGIN",
            new_y="NEXT",
        )
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
