"""Per-type contract specifications — what each pricing type actually carries.

`contract_type` used to be a label that changed three things: how often the
award funded in full, which letter went in the SF-30's "contract ID code" box,
and (before #62) the type stamped on a labor CLIN. Two awards of different types
were otherwise identical documents. That is not how the types differ in reality:
the type decides *what cost elements the contract states*, *whether fee is a
separate element at all*, and *which funding clause governs the money*.

This module is the single declaration of those differences. It owns the type
list, the weights the draw uses, the cost elements each type carries per its FAR
reference, and the funding-clause resolution — and `full_funding_odds` remains a
field of the spec rather than a table of its own.

Scope note: this module *declares* every element a type carries and *generates*
the estimated-cost / fee split, because that split is what a cost-type award
prints on its face. The richer fee structures each element implies — an award-fee
pool divided into evaluation periods, a CPIF share ratio with its minimum and
maximum fee — are generated in #58, which reads `cost_elements` from here.
"""

from __future__ import annotations

# The types, and everything that varies by type.
#
#   far                  the FAR subpart that defines the type
#   fixed_price          True => the contractor bears cost overrun, and no
#                        limitation-of-cost/funds clause applies
#   cost_reimbursement   True => the government reimburses allowable cost
#                        (FAR 31.2) and pays fee separately
#   cost_elements        the figures the contract states for this type. A
#                        consumer codes against these; #57/#58 generate the ones
#                        this ticket only declares.
#   fee_element          the name of the type's fee element, or None when fee is
#                        not a separate element (FFP has none; on T&M profit is
#                        inside the negotiated hourly rate per 52.232-7(a))
#   fee_band             the fee rate range, as a fraction of estimated cost
#   full_funding_odds    how often an award of this type funds in full at award
#   id_code              the SF-30 block 1 award-instrument letter
#   weight               relative frequency in the unpinned draw
#
# The weights are not uniform on purpose. Fixed-price dominates federal services
# work, T&M is close behind, CPFF is the common cost-reimbursement type, and
# CPIF / CPAF / FPI are genuinely rare — a corpus that is one-sixth CPAF would be
# as unrealistic as one with no CPAF at all. Pinning `contract_type` still gets
# any of the seven on demand.
_SPECS = {
    "FFP": {
        "label": "Firm-Fixed-Price",
        "far": "16.202",
        "fixed_price": True,
        "cost_reimbursement": False,
        "cost_elements": ("firm_price",),
        "fee_element": None,
        "fee_band": None,
        "full_funding_odds": 1.0,
        "id_code": "C",
        "weight": 30,
    },
    "T&M": {
        "label": "Time-and-Materials",
        "far": "16.601",
        "fixed_price": False,
        "cost_reimbursement": False,
        # The hourly rates cover wages, indirect cost and profit as one
        # negotiated figure (52.232-7(a)); the materials portion is separately
        # reimbursed at cost, which is why it is its own element.
        "cost_elements": ("ceiling_price", "loaded_labor", "reimbursable_materials"),
        "fee_element": None,
        "fee_band": None,
        "full_funding_odds": 0.5,
        "id_code": "C",
        "weight": 28,
    },
    "CPFF": {
        "label": "Cost-Plus-Fixed-Fee",
        "far": "16.306",
        "fixed_price": False,
        "cost_reimbursement": True,
        "cost_elements": ("estimated_cost", "fixed_fee", "total"),
        "fee_element": "fixed_fee",
        "fee_band": (0.06, 0.10),
        "full_funding_odds": 0.35,
        "id_code": "C",
        "weight": 16,
    },
    "CPIF": {
        "label": "Cost-Plus-Incentive-Fee",
        "far": "16.304",
        "fixed_price": False,
        "cost_reimbursement": True,
        # min_fee / max_fee / share_ratio are declared here and generated in #58.
        "cost_elements": (
            "target_cost",
            "target_fee",
            "min_fee",
            "max_fee",
            "share_ratio",
        ),
        "fee_element": "target_fee",
        "fee_band": (0.05, 0.09),
        "full_funding_odds": 0.35,
        "id_code": "C",
        "weight": 4,
    },
    "CPAF": {
        "label": "Cost-Plus-Award-Fee",
        "far": "16.401(e)",
        "fixed_price": False,
        "cost_reimbursement": True,
        # The base-fee / award-fee-pool split and its evaluation periods are #58.
        "cost_elements": ("estimated_cost", "base_fee", "award_fee_pool"),
        "fee_element": "award_fee",
        "fee_band": (0.05, 0.12),
        "full_funding_odds": 0.35,
        "id_code": "C",
        "weight": 6,
    },
    "FPI": {
        "label": "Fixed-Price Incentive",
        "far": "16.403",
        "fixed_price": True,
        "cost_reimbursement": False,
        "cost_elements": (
            "target_cost",
            "target_profit",
            "ceiling_price",
            "share_ratio",
        ),
        "fee_element": "target_profit",
        "fee_band": (0.07, 0.12),
        "full_funding_odds": 1.0,
        "id_code": "C",
        "weight": 2,
    },
    "IDIQ": {
        "label": "Indefinite-Delivery/Indefinite-Quantity",
        "far": "16.504",
        # A vehicle is not itself priced — each order is (see #51 for the
        # minimum-guarantee half). What governs the money depends on how the
        # order was priced, so the clause resolves through `order_pricing`.
        "fixed_price": False,
        "cost_reimbursement": False,
        "cost_elements": ("minimum_guarantee", "ceiling"),
        "fee_element": None,
        "fee_band": None,
        "full_funding_odds": 0.5,
        "id_code": "D",
        "weight": 14,
    },
}

KNOWN_TYPES = tuple(_SPECS)

# Types an IDIQ order can be priced as (FAR 16.504) — the per-order draw.
ORDER_PRICING_TYPES = ("T&M", "CPFF")

_DEFAULT_FULL_FUNDING_ODDS = 0.5

# Funding / payment clauses, by the situation that makes each one apply.
_LIMITATION_OF_COST = ("FAR 52.232-20", "Limitation of Cost")
_LIMITATION_OF_FUNDS = ("FAR 52.232-22", "Limitation of Funds")
_TM_PAYMENTS = (
    "FAR 52.232-7",
    "Payments under Time-and-Materials and Labor-Hour Contracts",
)


def spec(contract_type):
    """The specification for a type. Unknown types get the T&M shape, which is
    what the generator produced for everything before types were specified."""
    return _SPECS.get(contract_type) or _SPECS["T&M"]


def pick_type(rng, pinned=None):
    """The contract type to generate. A pinned type is honored when it is one of
    the seven; anything else (or unset) draws one by weight."""
    if pinned and pinned in _SPECS:
        return pinned
    return rng.choices(KNOWN_TYPES, weights=[s["weight"] for s in _SPECS.values()])[0]


def is_fixed_price(contract_type):
    """Whether the contractor bears the cost risk. Fixed-price awards fund in
    full as a matter of policy and carry no limitation-of-funds clause."""
    return bool(spec(contract_type)["fixed_price"])


def is_cost_reimbursement(contract_type):
    """Whether the government reimburses allowable cost and pays fee separately
    — the types whose CLINs state estimated cost and fee as two figures."""
    return bool(spec(contract_type)["cost_reimbursement"])


def full_funding_odds(contract_type):
    return spec(contract_type).get("full_funding_odds", _DEFAULT_FULL_FUNDING_ODDS)


def id_code(contract_type):
    """The SF-30 block 1 "contract ID code" letter — an award-instrument code,
    not the type name."""
    return spec(contract_type).get("id_code", "C")


def cost_elements(contract_type):
    return tuple(spec(contract_type)["cost_elements"])


def fee_element(contract_type):
    return spec(contract_type).get("fee_element")


def fee_rate(contract_type, roll):
    """The type's fee rate, as a fraction of estimated cost, from a [0,1) roll.

    Taking the roll outside this function and passing it in keeps the draw in
    one place in the seeded stream for every type — including the types that
    have no fee element and discard it."""
    band = spec(contract_type).get("fee_band")
    if not band:
        return 0.0
    lo, hi = band
    return round(lo + (hi - lo) * roll, 4)


def split_cost_and_fee(total, rate):
    """Decompose a stated total into estimated cost and fee at `rate`.

    Fee is a percentage *of cost*, so `total == cost * (1 + rate)`. Deriving the
    two figures from the total (rather than adding fee on top of it) is what
    keeps the CLIN ceiling — and every ceiling and obligation that rolls up from
    it — exactly where it was before fee became a separate element. Returns
    (estimated_cost, fee), summing to `total` to the cent."""
    total = round(float(total), 2)
    if not rate:
        return total, 0.0
    cost = round(total / (1.0 + rate), 2)
    return cost, round(total - cost, 2)


def ceiling_clause(contract_type, order_pricing=None):
    """The clause that governs the contract's *own* limit — the number the
    contractor is not obligated to work past — as (citation, title) or None.

    Distinct from `funding_clause`, and the distinction is the whole point:
    running out of allotted funds and reaching the contract's ceiling are two
    different hard stops with two different remedies (an allotment vs. a ceiling
    increase), and a consumer that treats them as one raises the wrong alarm.

      * T&M — the ceiling price, FAR 16.601(c)(1), payment governed by 52.232-7.
      * Cost-reimbursement — the estimated cost, governed by 52.232-20.
      * Fixed-price (FFP, FPI) — none; the price is the price.
    """
    if contract_type == "IDIQ":
        return ceiling_clause(order_pricing) if order_pricing else None
    if is_fixed_price(contract_type):
        return None
    return _LIMITATION_OF_COST if is_cost_reimbursement(contract_type) else _TM_PAYMENTS


def funding_clause(contract_type, full_funding, order_pricing=None):
    """The clause that governs how far the government's *obligated money* goes,
    as (citation, title) — or None when no such clause applies.

    A function of the type *and* the funding profile, and the app that consumes
    this data gets it wrong by assuming one of them:

      * Fixed-price (FFP, FPI) — none. There is no limitation-of-cost or
        limitation-of-funds notification to make.
      * Incrementally funded — 52.232-22, Limitation of Funds. The government's
        liability stops at the funds allotted so far, whatever the ceiling says.
      * Fully funded — nothing limits payment below the contract's own ceiling,
        so the governing clause is the ceiling clause: 52.232-20 on a cost
        contract, 52.232-7 on T&M.
      * IDIQ — whatever governs the order, so it resolves through the order's
        own pricing type.
    """
    if contract_type == "IDIQ":
        return funding_clause(order_pricing, full_funding) if order_pricing else None
    if is_fixed_price(contract_type):
        return None
    if not full_funding:
        return _LIMITATION_OF_FUNDS
    return ceiling_clause(contract_type)


def clause_text(clause):
    """A clause as an award prints it: "FAR 52.232-22, Limitation of Funds"."""
    return f"{clause[0]}, {clause[1]}" if clause else ""


def build_pricing(rng, contract_type, order_pricing):
    """The award-level pricing block: the type's identity, the elements it
    carries, and its fee rate. The governing funding clause is stamped on later
    by `resolve_funding_clause` — it depends on whether the award funds in full,
    which is not drawn until after the periods are priced.

    Two rolls are taken here and they are taken for *every* type, discarded
    where the type has no use for them, so that pinning a type does not shift
    the seeded stream relative to any other type:

      * `fee_roll`    positions the fee rate inside the type's band.
      * `struct_roll` is the type's second structural figure. Today only FPI
        uses it (for the spread between target price and price ceiling); #58
        extends it to the CPAF base-fee/pool split and the CPIF share ratio.
    """
    fee_roll = rng.random()
    struct_roll = rng.random()
    s = spec(contract_type)
    priced_as = order_pricing if contract_type == "IDIQ" else contract_type
    rate = fee_rate(priced_as, fee_roll)
    pricing = {
        "type": contract_type,
        "label": s["label"],
        "far": s["far"],
        "order_pricing": order_pricing,
        # What the CLINs are actually priced as. Identical to `type` except on a
        # vehicle, where the order's pricing is what the money follows.
        "priced_as": priced_as,
        "cost_elements": list(cost_elements(priced_as)),
        "fee_element": fee_element(priced_as),
        "fee_rate": rate,
        "fixed_price": bool(s["fixed_price"]),
        "cost_reimbursement": is_cost_reimbursement(priced_as),
    }
    if contract_type == "FPI":
        # FAR 16.403: the price ceiling sits above target cost + target profit,
        # and the contractor bears cost above it. 8-15% is the usual spread.
        pricing["ceiling_spread"] = round(0.08 + 0.07 * struct_roll, 4)
    return pricing


def resolve_funding_clause(pricing, full_funding):
    """Stamp the governing funding clause on a pricing block, once it is known
    whether the award obligates in full. Mutates and returns the block."""
    order_pricing = pricing.get("order_pricing")
    clause = funding_clause(pricing["type"], full_funding, order_pricing)
    ceiling = ceiling_clause(pricing["type"], order_pricing)
    pricing["fully_funded"] = bool(full_funding)
    pricing["funding_clause"] = clause_text(clause)
    pricing["funding_clause_cite"] = clause[0] if clause else ""
    pricing["funding_clause_title"] = clause[1] if clause else ""
    pricing["ceiling_clause"] = clause_text(ceiling)
    pricing["ceiling_clause_cite"] = ceiling[0] if ceiling else ""
    return pricing


def clin_cost_elements(pricing, ceiling):
    """The cost elements to stamp on one priced labor CLIN, given the award's
    pricing block and the CLIN's stated total.

    The total does not move. A cost-type CLIN's ceiling is decomposed into the
    estimated cost and the fee that sum back to it (FAR 16.306: a real CPFF
    CLIN prints both, and their sum), and a fixed-price CLIN simply names the
    figure it already carried."""
    priced_as = pricing["priced_as"]
    total = round(float(ceiling), 2)
    rate = pricing["fee_rate"]
    if is_cost_reimbursement(priced_as):
        cost, fee = split_cost_and_fee(total, rate)
        out = {"estimated_cost": cost, "fee": fee, "fee_rate": rate}
        if priced_as == "CPIF":
            out["target_cost"], out["target_fee"] = cost, fee
        return out
    if priced_as == "FPI":
        cost, profit = split_cost_and_fee(total, rate)
        return {
            "target_cost": cost,
            "target_profit": profit,
            # The CLIN's stated amount is the target price; the ceiling price
            # sits above it and is what the contractor is at risk against.
            "target_price": total,
            "ceiling_price": round(total * (1.0 + pricing["ceiling_spread"]), 2),
            "fee_rate": rate,
        }
    if priced_as == "FFP":
        return {"firm_price": total}
    # T&M / LH: the ceiling price is the limit, and profit is inside the rates.
    return {"ceiling_price": total, "profit_in_rates": True}
