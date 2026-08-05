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
the fee structure those elements imply — the estimated-cost / fee split, the CPAF
base-fee / award-fee-pool split and its evaluation periods, the CPIF share ratio
with the minimum and maximum fee it reaches. What it does not do is invent dates
or dollars: an evaluation-period structure is tiled onto the periods presets.py
has already built, and every fee figure is decomposed out of a CLIN total that
does not move.
"""

from __future__ import annotations

import datetime

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
        # The share ratio, and the minimum and maximum fee it reaches, are
        # generated per CLIN in `clin_cost_elements`.
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
        # The base-fee / award-fee-pool split is generated per CLIN; the pool's
        # evaluation periods and their determinations are the award-fee plan
        # (`build_award_fee_plan`), which is an attachment to the award.
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

# --- Fee structure ------------------------------------------------------------
# Base fee on a CPAF award, as a fraction of estimated cost. DFARS 215.404-74
# caps it at 3% on a DoD award-fee contract, and DoD policy is that most of the
# fee has to be at risk for it to be an incentive at all. Civilian agencies are
# not bound by that cap, so their base fee is drawn a little more freely — the
# whole point of the constraint is that it applies to *some* of the corpus.
_BASE_FEE_DOD = (0.0, 0.03)
_BASE_FEE_CIVILIAN = (0.0, 0.05)

# However the base fee lands, this much of the estimated cost stays in the pool,
# and the pool stays the majority of the fee. An award-fee arrangement whose fee is
# mostly guaranteed base fee is a CPFF award with extra paperwork — the point of
# the structure is that most of the fee is genuinely at risk.
_MIN_AWARD_FEE_RATE = 0.02
_MAX_BASE_FEE_SHARE = 0.5

# The share ratios an incentive arrangement is actually negotiated at, as
# (government, contractor) percentages: the government absorbs most of an overrun
# and keeps most of an underrun, and the contractor's share is what makes cost
# control worth something to it (FAR 16.304, 16.403).
_SHARE_RATIOS = ((80, 20), (85, 15))

# How far cost has to swing from target before the incentive brackets bind — the
# range of incentive effectiveness. Outside it fee is flat at the minimum or the
# maximum and the share ratio stops applying.
_INCENTIVE_SWING = (0.10, 0.20)

# The DoD award-fee rating table (DFARS PGI 216.470), as
# (minimum score, adjectival rating). Above a score of 50 the percentage of the
# available pool earned *is* the score; at or below it nothing is earned, which is
# the part that makes award fee a real risk rather than a discount schedule.
_AWARD_FEE_RATINGS = (
    (91, "Excellent"),
    (76, "Very Good"),
    (61, "Good"),
    (51, "Satisfactory"),
    (0, "Unsatisfactory"),
)
_UNSATISFACTORY_MAX = 50

# The evaluation criteria an award-fee plan scores against, with the band each
# factor's weight is drawn from. Technical performance carries the most weight on
# a services award; the weights sum to 100 with the last factor taking the
# remainder, so a plan's criteria always foot exactly.
_AWARD_FEE_CRITERIA = (
    ("Technical Performance", (30, 45)),
    ("Schedule and Timeliness", (15, 25)),
    ("Cost Control", (20, 30)),
    ("Management and Small Business Participation", None),
)

# How long after an evaluation period closes the Fee Determining Official signs
# the determination. Until then the period is closed but its fee is undetermined
# — a third state, and the one a consumer most often gets wrong by folding it
# into either "earned" or "not earned yet".
_DETERMINATION_LAG_DAYS = (30, 60)

# Fee and payment clauses by type, beyond the funding clauses below. 52.216-7
# governs every cost-reimbursement award; the fee clause itself is the type's.
_ALLOWABLE_COST = ("FAR 52.216-7", "Allowable Cost and Payment")
_FEE_CLAUSES = {
    "CPFF": (_ALLOWABLE_COST, ("FAR 52.216-8", "Fixed Fee")),
    "CPIF": (_ALLOWABLE_COST, ("FAR 52.216-10", "Incentive Fee")),
    # There is no FAR fee clause for award fee — the arrangement is stated in the
    # award-fee plan the contract incorporates, under the FAR 16.401(e) criteria.
    "CPAF": (_ALLOWABLE_COST,),
    "FPI": (("FAR 52.216-16", "Incentive Price Revision - Firm Target"),),
}

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


def _band(band, roll):
    lo, hi = band
    return lo + (hi - lo) * float(roll)


def award_fee_split(fee_rate, roll, dod=False):
    """Split a CPAF award's total fee rate into (base fee rate, pool rate).

    Base fee is the guaranteed part — paid regardless of how the contractor is
    rated — and the pool is the part it has to earn. DFARS 215.404-74 caps base
    fee at 3% of estimated cost on a DoD award-fee contract, so the band the roll
    lands in depends on who is buying. Whatever the band says, the pool keeps the
    majority of the fee and at least `_MIN_AWARD_FEE_RATE` of estimated cost."""
    total = float(fee_rate)
    base = round(_band(_BASE_FEE_DOD if dod else _BASE_FEE_CIVILIAN, roll), 4)
    base = min(
        base,
        round(total * _MAX_BASE_FEE_SHARE, 4),
        round(max(0.0, total - _MIN_AWARD_FEE_RATE), 4),
    )
    return base, round(total - base, 4)


def share_ratio(roll):
    """The (government, contractor) share ratio, from a [0,1) roll."""
    return _SHARE_RATIOS[0] if roll < 0.6 else _SHARE_RATIOS[1]


def share_ratio_text(ratio):
    """A share ratio as a contract states it: "80/20" (government/contractor)."""
    return f"{ratio[0]}/{ratio[1]}" if ratio else ""


def incentive_swing(roll):
    """The cost variance from target at which the fee brackets bind."""
    return round(_band(_INCENTIVE_SWING, roll), 4)


def incentive_brackets(target_cost, target_fee, ratio, swing):
    """The CPIF minimum and maximum fee, and the cost each one binds at.

    Fee moves from the target by the contractor's share of the cost variance
    (FAR 16.304), so the brackets are not free-standing draws — they are where
    that adjustment stops. Underrun by `swing` and the contractor keeps its share
    of the saving on top of target fee; overrun by it and the same share comes off.
    Deriving them this way is what makes "a share ratio the brackets are reachable
    under" true by construction rather than by inspection, and it puts a real
    number on the cost point a consumer needs to model the incentive."""
    target_cost = round(float(target_cost), 2)
    target_fee = round(float(target_fee), 2)
    contractor_share = ratio[1] / 100.0
    adjustment = round(target_cost * float(swing) * contractor_share, 2)
    return {
        "min_fee": round(max(0.0, target_fee - adjustment), 2),
        "max_fee": round(target_fee + adjustment, 2),
        # The maximum fee is reached by underrunning, so it sits at the LOW cost.
        "max_fee_at_cost": round(target_cost * (1.0 - float(swing)), 2),
        "min_fee_at_cost": round(target_cost * (1.0 + float(swing)), 2),
    }


def award_fee_rating(score):
    """The (adjectival rating, fraction of the available pool earned) for a score
    on the DoD award-fee rating table (DFARS PGI 216.470)."""
    if score is None:
        return None, 0.0
    score = int(score)
    for floor, label in _AWARD_FEE_RATINGS:
        if score >= floor:
            return label, (0.0 if score <= _UNSATISFACTORY_MAX else score / 100.0)
    return "Unsatisfactory", 0.0


def fee_clauses(contract_type, order_pricing=None):
    """The fee and payment clauses this type's award carries, as
    ((citation, title), ...) — empty where fee is not a separate element."""
    priced_as = order_pricing if contract_type == "IDIQ" else contract_type
    return _FEE_CLAUSES.get(priced_as, ())


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


def build_pricing(rng, contract_type, order_pricing, dod=False):
    """The award-level pricing block: the type's identity, the elements it
    carries, its fee rate and the structure that rate is paid under. The
    governing funding clause is stamped on later by `resolve_funding_clause` — it
    depends on whether the award funds in full, which is not drawn until after
    the periods are priced.

    Two rolls are taken here and they are taken for *every* type, discarded
    where the type has no use for them, so that pinning a type does not shift
    the seeded stream relative to any other type:

      * `fee_roll`    positions the fee rate inside the type's band.
      * `struct_roll` is the type's second structural figure — the CPAF
        base-fee/pool split, the CPIF and FPI share ratio and the cost swing
        their brackets bind at, the FPI price-ceiling spread. Every type that
        needs a second figure reads it off this one roll rather than taking a
        draw of its own, which is what keeps the stream type-independent.

    `dod` says whether the buying agency is a DoD component, because one of these
    figures is regulated and one is not: DFARS 215.404-74 caps CPAF base fee at
    3% of estimated cost, and no equivalent cap binds a civilian agency.
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
        "dod": bool(dod),
    }
    clauses = fee_clauses(contract_type, order_pricing)
    pricing["fee_clauses"] = [list(c) for c in clauses]
    pricing["fee_clause_text"] = [clause_text(c) for c in clauses]
    if priced_as == "CPAF":
        # The fee is one rate with two halves: what the contractor is paid for
        # showing up, and what it has to be rated to earn.
        base, pool = award_fee_split(rate, struct_roll, dod)
        pricing["base_fee_rate"] = base
        pricing["award_fee_rate"] = pool
    if priced_as == "CPIF":
        pricing["share_ratio"] = list(share_ratio(struct_roll))
        pricing["incentive_swing"] = incentive_swing(struct_roll)
    if contract_type == "FPI":
        # FAR 16.403: the price ceiling sits above target cost + target profit,
        # and the contractor bears cost above it. 8-15% is the usual spread.
        pricing["ceiling_spread"] = round(0.08 + 0.07 * struct_roll, 4)
        pricing["share_ratio"] = list(share_ratio(struct_roll))
        pricing["incentive_swing"] = incentive_swing(struct_roll)
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
        if priced_as == "CPFF":
            # FAR 16.306: the fee is a dollar figure fixed at award. It is stated
            # under its own name so nothing downstream can mistake it for a rate
            # to reapply to actual cost — the one invariant that makes it CPFF.
            out["fixed_fee"] = fee
        if priced_as == "CPAF":
            # Guaranteed base fee, and the pool it has to be rated to earn. Both
            # come out of the fee already in the total, so the split changes what
            # the CLIN *says*, not what it costs.
            base = round(cost * pricing["base_fee_rate"], 2)
            out["base_fee"] = base
            out["award_fee_pool"] = round(fee - base, 2)
            out["base_fee_rate"] = pricing["base_fee_rate"]
            out["award_fee_rate"] = pricing["award_fee_rate"]
        if priced_as == "CPIF":
            out["target_cost"], out["target_fee"] = cost, fee
            ratio = tuple(pricing["share_ratio"])
            out["share_ratio"] = list(ratio)
            out["incentive_swing"] = pricing["incentive_swing"]
            out.update(incentive_brackets(cost, fee, ratio, pricing["incentive_swing"]))
        return out
    if priced_as == "FPI":
        cost, profit = split_cost_and_fee(total, rate)
        ratio = tuple(pricing["share_ratio"])
        out = {
            "target_cost": cost,
            "target_profit": profit,
            # The CLIN's stated amount is the target price; the ceiling price
            # sits above it and is what the contractor is at risk against.
            "target_price": total,
            "ceiling_price": round(total * (1.0 + pricing["ceiling_spread"]), 2),
            "fee_rate": rate,
            "share_ratio": list(ratio),
            "incentive_swing": pricing["incentive_swing"],
        }
        # Profit adjusts by the share ratio the same way CPIF fee does, up to the
        # point the price ceiling takes over (FAR 16.403).
        brackets = incentive_brackets(cost, profit, ratio, pricing["incentive_swing"])
        out["min_profit"] = brackets["min_fee"]
        out["max_profit"] = brackets["max_fee"]
        out["max_profit_at_cost"] = brackets["max_fee_at_cost"]
        out["min_profit_at_cost"] = brackets["min_fee_at_cost"]
        return out
    if priced_as == "FFP":
        return {"firm_price": total}
    # T&M / LH: the ceiling price is the limit, and profit is inside the rates.
    return {"ceiling_price": total, "profit_in_rates": True}


# --- The award-fee plan (CPAF) ------------------------------------------------
# The pool is a number on the award; the plan is the only document that says how
# it gets earned. Without the evaluation periods and their determinations, a
# consumer can see that $600k of award fee exists and cannot tell how much of it
# has been earned, how much was forfeited, and how much is still at risk — which
# is the whole distinction award fee exists to create.

# The evaluation cadence, in months. Semiannual is the common arrangement; annual
# happens. (Weighted by repetition rather than a weights table — one draw, and the
# reader can see the odds.)
_EVALUATION_CADENCE = (6, 6, 6, 12)

# Score bands and their relative frequency. Most determinations land Very Good or
# Excellent — a contractor performing badly enough for a Satisfactory rating is
# usually in more trouble than an award-fee determination — but the tail matters,
# because a period that earned nothing is the case a fee model has to survive.
_SCORE_BANDS = (
    ((91, 97), 30),
    ((76, 90), 40),
    ((61, 75), 20),
    ((51, 60), 7),
    ((35, 50), 3),
)


def award_fee_rating_table():
    """The rating table as a document prints it: rating, score range, and the
    percentage of the available pool earned."""
    rows = []
    ceiling = 100
    for floor, label in _AWARD_FEE_RATINGS:
        earned = "0%" if floor <= _UNSATISFACTORY_MAX else f"{floor}-{ceiling}%"
        rows.append(
            {
                "rating": label,
                "score": f"{floor}-{ceiling}",
                "fee_earned": earned,
            }
        )
        ceiling = floor - 1
    return rows


def _evaluation_windows(period, n):
    """`n` contiguous windows tiling one contract period, inside its PoP.

    Each window starts the day the last one ended, the first starts on the
    period's first day and the last ends on its last — so the windows are
    contiguous, non-overlapping and inside the PoP by construction rather than by
    a check afterwards (the CPAF half of invariant 6)."""
    start, end = period["pop_start"], period["pop_end"]
    span = (end - start).days + 1
    out = []
    for i in range(n):
        w_start = start + datetime.timedelta(days=(i * span) // n)
        w_end = (
            end
            if i == n - 1
            else start + datetime.timedelta(days=(((i + 1) * span) // n) - 1)
        )
        out.append((w_start, w_end))
    return out


def _pool_shares(pool, n):
    """One period's award-fee pool divided across `n` evaluation periods.

    Equal shares, with the last one absorbing the rounding remainder, so the
    shares sum to the pool to the cent — the identity a consumer validates."""
    pool = round(float(pool), 2)
    if n <= 1:
        return [pool]
    each = round(pool / n, 2)
    return [each] * (n - 1) + [round(pool - each * (n - 1), 2)]


def _award_fee_criteria(rng):
    """The plan's evaluation factors and their weights, summing to 100."""
    out, used = [], 0
    for factor, band in _AWARD_FEE_CRITERIA:
        if band is None:
            weight = 100 - used
        else:
            weight = rng.randint(*band)
        used += weight
        out.append({"factor": factor, "weight": weight})
    return out


def _award_fee_score(rng):
    """A determination score, drawn from the rating bands."""
    band = rng.choices(
        [b for b, _ in _SCORE_BANDS], weights=[w for _, w in _SCORE_BANDS]
    )[0]
    return rng.randint(*band)


def build_award_fee_plan(rng, pricing, periods, today=None):
    """The award-fee plan for a CPAF award: the evaluation periods the pool is
    divided into, and a determination for each one that has closed and been
    evaluated. None for every other type.

    Three states, not two, and the middle one is the point. A period whose end is
    behind the Fee Determining Official's signature is *determined* — a score, a
    rating, an earned amount and a forfeited remainder. A period that has closed
    but whose determination is not yet signed is *in evaluation*. A period ahead of
    today is *pending*, and its share of the pool is fee the contractor has not
    yet had the chance to earn. Fee earned, fee forfeited and fee still at risk are
    three different numbers, and an award document alone states none of them.

    The plan covers the exercised periods only. An option nobody has exercised has
    no evaluation periods, because the fee in it is not yet under contract — the
    SF-30 that exercises the option is what adds them.

    `rng` should be a substream of its own (see presets.build_contract): a plan
    exists on one of seven types, so its draws must not move the shared stream.
    """
    if pricing.get("priced_as") != "CPAF":
        return None
    covered = [p for p in periods if p.get("exercised")]
    if not covered:
        return None
    today = today or datetime.date.today()
    cadence = rng.choice(_EVALUATION_CADENCE)
    criteria = _award_fee_criteria(rng)

    evaluations = []
    base_fee = 0.0
    for period in covered:
        clins = period.get("clins") or []
        base_fee += sum(float(c.get("base_fee") or 0) for c in clins)
        pool = sum(float(c.get("award_fee_pool") or 0) for c in clins)
        # A period of ~12 months splits into two semiannual evaluations, or stays
        # one annual evaluation. Evaluation periods never straddle a contract
        # period, so each period's own pool is what its evaluations divide.
        windows = _evaluation_windows(period, 2 if cadence == 6 else 1)
        for (start, end), available in zip(windows, _pool_shares(pool, len(windows))):
            # Both draws are taken for every window whatever its state, so a
            # generated-today plan does not have a different substream from the
            # same plan generated next month.
            score = _award_fee_score(rng)
            lag = rng.randint(*_DETERMINATION_LAG_DAYS)
            determined_on = end + datetime.timedelta(days=lag)
            evaluation = {
                "number": len(evaluations) + 1,
                "contract_period": period.get("name"),
                "start": start,
                "end": end,
                "available_fee": available,
            }
            if determined_on <= today:
                rating, earned_pct = award_fee_rating(score)
                earned = round(available * earned_pct, 2)
                evaluation.update(
                    {
                        "status": "determined",
                        "score": score,
                        "rating": rating,
                        "determination_date": determined_on,
                        "fee_earned": earned,
                        # Unearned award fee is not available for award in any
                        # later period, so this is forfeited, not deferred.
                        "fee_forfeited": round(available - earned, 2),
                    }
                )
            else:
                evaluation.update(
                    {
                        "status": "in_evaluation" if end < today else "pending",
                        "score": None,
                        "rating": None,
                        "determination_date": None,
                        "fee_earned": None,
                        "fee_forfeited": None,
                    }
                )
            evaluations.append(evaluation)

    determined = [e for e in evaluations if e["status"] == "determined"]
    pool_total = round(sum(e["available_fee"] for e in evaluations), 2)
    fee_determined = round(sum(e["available_fee"] for e in determined), 2)
    fee_earned = round(sum(e["fee_earned"] for e in determined), 2)
    return {
        "far": "16.401(e)",
        "cadence_months": cadence,
        "criteria": criteria,
        "evaluations": evaluations,
        "base_fee": round(base_fee, 2),
        "award_fee_pool": pool_total,
        "total_fee": round(base_fee + pool_total, 2),
        # What has been determined, what came out of it, and what has not been
        # determined yet. `fee_at_risk` is the pool the contractor still has to
        # earn — the figure a fee model reports and the one that cannot be derived
        # from the award form.
        "fee_determined": fee_determined,
        "fee_earned": fee_earned,
        "fee_forfeited": round(fee_determined - fee_earned, 2),
        "fee_at_risk": round(pool_total - fee_determined, 2),
        "periods_determined": len(determined),
        "periods_total": len(evaluations),
    }
