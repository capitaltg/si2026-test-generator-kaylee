"""Pricing continuation sheets — the exhibit that backs a labor CLIN.

A form face (SF-1449, SF-26) carries only a CLIN *summary* — item number,
description, amount. What backs it lives on a continuation sheet: "Continuation
of SF-1449, Schedule of Line Items and Pricing" for the commercial-items form,
or the Uniform Contract Format's "Section B - Supplies or Services and
Prices/Costs" for a negotiated award (SF-26).

*What* that sheet states depends on how the contract is priced, and this module
draws both shapes:

  * a negotiated-rate award (T&M, LH, FFP) states fully-burdened hourly rates,
    because under FAR 52.232-7(a) the rate is the price — one number covering
    wages, indirect cost and profit;
  * a cost-reimbursement award (CPFF, CPIF, CPAF) states a **cost buildup**,
    because there is no price per hour to state: direct labor by category, each
    indirect pool applied to its own base, total estimated cost, and fee as its
    own line. A loaded rate never appears on it.

Both are drawn from the generated contract's `labor_rates` and `cost_buildup`
(generated in presets.py against the company rate set in indirects.py) and
returned as PDF bytes, ready to append after the filled form. Both carry the
same SIMULATED footer as the forms, so an appended page is never mistaken for a
genuine one.
"""

from __future__ import annotations

from fpdf import FPDF

from . import indirects
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

# The cost-reimbursement exhibit, which is a different table and not a variant
# of the one above. A CPFF Section B does not state a loaded rate at all — the
# government is not buying qualified hours at a price, it is reimbursing
# allowable cost — so the price columns become direct labor and the pools that
# burden it are the rows underneath (see `_buildup_rows`).
#
# The qualification columns come off with the price. A minimum education or a
# clearance level is on a rate schedule to justify the rate being charged; where
# there is no rate being charged there is nothing for them to justify, and a
# real cost buildup does not carry them — the LCAT qualification floor lives in
# Section H or an LCAT-description attachment instead. Every one of those fields
# is still generated on the labor line for a consumer to read; this is only the
# question of what the pricing exhibit prints.
_COST_COLS = [
    ("Labor Category (LCAT)", 84, "L", "lcat"),
    ("Direct Rate/Hr", 34, "R", "direct_rate"),
    ("Est. Hrs", 30, "R", "est_hours"),
    ("Direct Labor Cost", 46, "R", "direct_amount"),
]

_MONEY_KEYS = ("loaded_rate", "amount", "direct_rate", "direct_amount")


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
            "target_fee": "Target Fee",
        }.get(element, "Fee")
        cost_label = (
            "Target Cost" if element == "target_fee" else "Total Estimated Cost"
        )
        rows = [(cost_label, hours, clin["estimated_cost"], False)]
        if clin.get("award_fee_pool") is not None:
            # Award fee is two elements, not one, and printing their sum as "fee"
            # loses the only thing that matters about it: the base fee is paid
            # whatever happens and the pool has to be earned. A real CPAF Section B
            # prices them as two lines.
            rows += [
                (
                    f"Base Fee ({_pct(clin.get('base_fee_rate'))})",
                    None,
                    clin.get("base_fee") or 0,
                    False,
                ),
                (
                    f"Award Fee Pool ({_pct(clin.get('award_fee_rate'))})",
                    None,
                    clin["award_fee_pool"],
                    False,
                ),
            ]
        else:
            rows.append((f"{label} ({rate})", None, clin.get("fee") or 0, False))
        total_label = (
            "Total Target Amount (Target Cost + Target Fee)"
            if element == "target_fee"
            else "Total CLIN Amount (Cost + Fee)"
        )
        rows.append((total_label, None, extended, True))
        if clin.get("max_fee") is not None:
            # The brackets the fee adjustment stops at (FAR 16.304). They are
            # dollar figures the contract states, so they are priced lines — and
            # they sit below the target the way a price ceiling sits below a target
            # price, because neither is part of the sum above it. The cost points
            # they bind at go in the note under the table.
            rows += [
                ("Minimum Fee (cost overrun)", None, clin["min_fee"], False),
                ("Maximum Fee (cost underrun)", None, clin["max_fee"], False),
            ]
        return rows
    if clin.get("target_profit") is not None:
        return [
            ("Target Cost", hours, clin["target_cost"], False),
            (f"Target Profit ({rate})", None, clin["target_profit"], False),
            ("Target Price", None, clin.get("target_price"), True),
            ("Price Ceiling (FAR 16.403)", None, clin.get("ceiling_price"), False),
            ("Minimum Profit (cost overrun)", None, clin.get("min_profit"), False),
            ("Maximum Profit (cost underrun)", None, clin.get("max_profit"), False),
        ]
    if clin.get("firm_price") is not None:
        # A firm-fixed-price CLIN states one figure. Naming it "Firm-Fixed Price"
        # instead of "CLIN Total" is the whole statement — there is no ceiling,
        # no fee and no second number to reconcile against.
        return [("Firm-Fixed Price", hours, clin["firm_price"], True)]
    if clin.get("profit_in_rates"):
        return [("Total Ceiling Price", hours, extended, True)]
    return [("CLIN Total", hours, extended, True)]


def _buildup_rows(clin):
    """The cost-buildup rows that sit between a cost-type CLIN's direct labor
    lines and its total-estimated-cost line, as (label, hours, amount, bold).

    The direct-labor subtotal carries the hours, because on this exhibit it is
    the hours-bearing line — the pools below it are dollar figures applied to a
    base, and total estimated cost is a dollar figure too. Each pool names the
    base it applies to, which is the part a reader checks: overhead loads labor
    *and* fringe, and G&A loads everything before it.
    """
    rows = [
        ("Total Direct Labor", clin.get("est_hours"), clin.get("direct_labor"), False)
    ]
    for pool in clin.get("cost_buildup") or []:
        label = f"{pool['label']} @ {_pct(pool['rate'])} of {pool['base_label']}"
        rows.append((label, None, pool["amount"], False))
    return rows


def _share_ratio_text(clin):
    ratio = clin.get("share_ratio")
    return f"{ratio[0]}/{ratio[1]}" if ratio else ""


def _fee_note(clin):
    """The sentence that qualifies a cost-type CLIN's fee, where the fee is not
    simply a figure. What a consumer cannot get from the table is *how the fee
    moves*: an award-fee pool is earned by determination and forfeited if it is
    not, and an incentive fee slides with cost between two brackets."""
    if clin.get("award_fee_pool") is not None:
        return (
            "The base fee is payable without regard to performance; the award fee "
            "pool is earned only to the extent determined by the Fee Determining "
            "Official under the Award Fee Plan (FAR 16.401(e)), and award fee not "
            "earned in an evaluation period is not available for award in any "
            "subsequent period."
        )
    if clin.get("max_fee") is not None:
        return (
            f"Fee adjusts from the target fee by the {_share_ratio_text(clin)} "
            "Government/Contractor share ratio as allowable cost varies from the "
            "target cost (FAR 16.304). The maximum fee is reached at a total "
            f"allowable cost of {_money(clin.get('max_fee_at_cost'))} and the "
            f"minimum fee at {_money(clin.get('min_fee_at_cost'))}; outside that "
            "range the fee is fixed at the bracket."
        )
    return ""


def _table_note(clin, rates=None):
    """The note a sheet carries under a CLIN's table, where a real award puts
    one — a short qualification of the rates above, not a restatement of them."""
    if clin.get("cost_buildup") and clin.get("estimated_cost") is not None:
        note = (
            "Note: indirect rates shown are provisional billing rates "
            "(FAR 42.704), applied to the bases stated. Costs are reimbursed as "
            "allowable, allocable and reasonable under FAR 31.2, subject to "
            "final indirect cost rate determination (FAR 52.216-7)."
        )
        fee_note = _fee_note(clin)
        return f"{note} {fee_note}" if fee_note else note
    if clin.get("profit_in_rates"):
        note = (
            "Note: the fixed hourly rates above are inclusive of all direct and "
            "indirect costs and profit. Materials and other direct costs are "
            "reimbursed separately at cost, without fee (FAR 52.232-7(a))."
        )
        # When the negotiated rates were not struck at the indirect rates the
        # contractor now carries, say so on the face of the schedule. That is
        # the disclosure a rate reconciliation is supposed to pick up, and a
        # real award does carry it when the two differ.
        variance = (rates or {}).get("variance") or 0.0
        if variance:
            direction = "below" if variance < 0 else "above"
            note += (
                f" The negotiated rates are approximately "
                f"{abs(variance) * 100:.1f}% {direction} the rates supported by "
                "the Contractor's current indirect rate buildup."
            )
        return note
    if clin.get("target_profit") is not None:
        return (
            "Note: costs incurred above the price ceiling are borne by the "
            f"Contractor; profit adjusts by the {_share_ratio_text(clin)} "
            "Government/Contractor share ratio as cost varies from the target, "
            f"reaching the maximum profit at {_money(clin.get('max_profit_at_cost'))} "
            f"and the minimum at {_money(clin.get('min_profit_at_cost'))} "
            "(FAR 16.403)."
        )
    return ""


def _row_values(line, cols):
    out = []
    for _, _, _, key in cols:
        v = line.get(key)
        if key in _MONEY_KEYS:
            v = _money(v)
        elif key == "est_hours":
            v = f"{int(v):,}" if v else ""
        elif v is None:
            v = ""
        out.append(v)
    return out


def rate_schedule_bytes(contract, form_title, section_label, cost_section_label=None):
    """Draw the pricing schedule for a generated contract and return PDF bytes.

    form_title          the sheet title, e.g. "CONTINUATION OF SF-1449".
    section_label       the schedule heading, e.g.
                        "SCHEDULE OF LINE ITEMS AND PRICING".
    cost_section_label  the heading to use instead on a cost-reimbursement
                        award, whose exhibit is a cost buildup and not a rate
                        schedule. Falls back to `section_label`.
    """
    pdf = _SchedulePDF(unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    usable = pdf.w - pdf.l_margin - pdf.r_margin

    pricing = contract.get("pricing") or {}
    rates = (contract.get("contractor") or {}).get("indirect_rates") or {}
    # What kind of exhibit this is. A cost-reimbursement award does not price
    # hours at a rate, so its sheet is a cost buildup with its own heading and
    # its own columns.
    cost_exhibit = bool(pricing.get("cost_reimbursement"))
    if cost_exhibit and cost_section_label:
        section_label = cost_section_label
    cols = _COST_COLS if cost_exhibit else _COLS

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
    # The negotiated indirect rates, stated once above the tables. A cost-type
    # award prints them on its face because they are terms of the contract — the
    # rates the government agreed cost would be burdened at — and every buildup
    # below applies these same figures. They sit here, above the schedule,
    # because that is where a Section B states the rates its exhibits use.
    if cost_exhibit and rates:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(
            0,
            5,
            _latin1(indirects.rate_disclosure(rates)),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_font("Helvetica", "", 9)
    pdf.ln(3)

    def table_header():
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(240, 242, 245)
        for heading, w, _, _ in cols:
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
                for value, (_, w, align, _key) in zip(_row_values(line, cols), cols):
                    _cell(pdf, value, w, align)
                pdf.ln(6)

            # What closes the table. On a cost exhibit the pool rows come first
            # — direct labor, then each indirect pool applied to its base — and
            # the summary block that used to foot the labor column now foots the
            # buildup instead, which is the same block doing the same job one
            # layer further down the cost model.
            total = sum(float(l.get("amount") or 0) for l in lines)
            hours = sum(int(l.get("est_hours") or 0) for l in lines)
            rest = sum(w for _, w, _, _ in cols[4:])
            rows = []
            if cost_exhibit and clin.get("cost_buildup"):
                rows += _buildup_rows(clin)
                # Hours are already stated on the direct-labor row above.
                hours = None
            rows += _summary_rows(clin, pricing, hours, total)
            # The label on these rows runs across the rate column as well as the
            # category column. It has to: "Overhead @ 48.5% of Direct Labor +
            # Fringe" does not fit in the width a labor category needs, and every
            # one of these rows leaves the rate column blank anyway — none of
            # them is priced at a rate per hour. A real exhibit runs the element
            # label across the description block the same way.
            label_w = cols[0][1] + cols[1][1]
            for label, row_hours, amount, bold in rows:
                pdf.set_font("Helvetica", "B" if bold else "", 8)
                pdf.cell(label_w, 6, _latin1(label), border=1, align="L")
                pdf.cell(
                    cols[2][1],
                    6,
                    _latin1(f"{row_hours:,}") if row_hours else "",
                    border=1,
                    align="R",
                )
                pdf.cell(cols[3][1], 6, _latin1(_money(amount)), border=1, align="R")
                # The qualification columns, blank, where the table has them. A
                # zero-width cell is not a no-op in fpdf — width 0 means "run to
                # the right margin" — so the cost exhibit, whose table ends at
                # the amount column, must skip the call entirely.
                if rest:
                    pdf.cell(rest, 6, "", border=1)
                pdf.ln(6)
            pdf.set_font("Helvetica", "", 8)

            note = _table_note(clin, rates)
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

    # The fee and payment clauses the award carries, and — on an award-fee
    # contract — the plan that says how the pool is earned. A Section B is not
    # Section I, so these are listed the way an award lists a clause whose text it
    # does not print: incorporated by reference under FAR 52.252-2. What makes them
    # worth stating here is that the list follows the type — 52.216-8 on a CPFF
    # award, 52.216-10 on a CPIF award, neither on a T&M one.
    clauses = pricing.get("fee_clause_text") or []
    plan = pricing.get("award_fee")
    if clauses or plan:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(20, 60, 120)
        pdf.cell(
            0,
            7,
            _latin1("FEE AND PAYMENT CLAUSES INCORPORATED BY REFERENCE"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 8)
        for clause in clauses:
            pdf.multi_cell(usable, 5, _latin1(clause), new_x="LMARGIN", new_y="NEXT")
        if plan:
            pdf.multi_cell(
                usable,
                5,
                _latin1(
                    "Award Fee Plan (Attachment J-1), FAR "
                    f"{plan.get('far', '16.401(e)')} - "
                    f"{plan.get('periods_total', 0)} evaluation periods on a "
                    f"{plan.get('cadence_months', 6)}-month cycle, award fee pool "
                    f"{_money(plan.get('award_fee_pool'))}."
                ),
                new_x="LMARGIN",
                new_y="NEXT",
            )

    return bytes(pdf.output())


def sf1449_continuation(contract):
    """The SF-1449 pricing continuation sheet."""
    return rate_schedule_bytes(
        contract,
        "CONTINUATION OF SF-1449",
        "SCHEDULE OF LINE ITEMS AND PRICING",
        "SCHEDULE OF LINE ITEMS AND ESTIMATED COST",
    )


def sf26_section_b(contract):
    """The SF-26 Uniform Contract Format Section B schedule."""
    return rate_schedule_bytes(
        contract,
        "SECTION B - SUPPLIES OR SERVICES AND PRICES/COSTS",
        "LABOR RATE SCHEDULE (FULLY BURDENED)",
        "COST BUILDUP AND ESTIMATED COST BY LINE ITEM",
    )
