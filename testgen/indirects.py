"""Company indirect rates, and the cost buildup a loaded rate comes out of.

A fully-burdened bill rate is not a figure a contractor picks. It is the
*result* of a cost buildup: the direct wage, then each indirect pool applied to
its own base, then fee.

    Direct labor rate                       $ 62.00 /hr
    Fringe          32%  of direct labor      19.84
    Overhead        45%  of labor + fringe    36.83
    G&A             12%  of total cost input  14.24
    = Total cost                            $132.91 /hr
    Fee              8%  of estimated cost     10.63
    = Loaded rate                           $143.54 /hr

The generator used to draw the last line and leave nothing underneath it, which
is fine for a T&M schedule — FAR 52.232-7(a) says the negotiated hourly rate
*is* one number covering wages, indirect cost and profit — but leaves a
cost-reimbursement award with no cost model behind its price. So the draw is
inverted here: the direct rate and the indirect rates are what get generated,
and the loaded rate is derived from them. The buildup and the printed rate
reconcile by construction rather than by coincidence.

Indirect rates are a property of the *company*, not of a labor category and not
of a contract. One set of fringe / overhead / G&A applies across a contractor's
whole book of business, which is why `build_rate_set` is called once per
contractor and passed down to every labor line on every CLIN. (When #54 lands
its company primitive, that is the object these belong on.)

The bands are the ones a real services contractor's rates fall in: fringe
25-40%, overhead 30-60% (site-dependent), G&A 8-18%.
"""

from __future__ import annotations

_FRINGE = (0.25, 0.40)
_OVERHEAD = (0.30, 0.60)
_GA = (0.08, 0.18)

# The profit a company builds into a *negotiated rate*. Distinct from the fee on
# a cost-type award: there, fee is a separate contract element with its own band
# per FAR subpart (see contract_types.fee_band), and this figure is unused. On
# T&M and FFP there is no fee element at all, and this is the profit that sits
# inside the rate.
_PROFIT = (0.07, 0.12)

# How far a *negotiated* rate may sit from the rate its buildup supports, when
# the variance knob is on. Usually a discount (rates cut to win the work);
# occasionally high (rates negotiated at a prior year's indirect rates, since
# gone up).
_VARIANCE = (-0.06, 0.03)

# The pools, in the order they apply, each with the base it applies to. Order is
# the whole content of this table: overhead loads labor *and* fringe, G&A loads
# everything before it. Applying them in any other order — or all of them to
# direct labor — is the classic way a made-up cost buildup gives itself away.
_POOLS = (
    ("fringe", "Fringe Benefits", "Direct Labor"),
    ("overhead", "Overhead", "Direct Labor + Fringe"),
    ("g_and_a", "G&A", "Total Cost Input"),
)


def build_rate_set(rng, variance=False):
    """One company's indirect rate set. Drawn once per contractor.

    Four rolls are always taken, whatever the contract type and whatever the
    knobs say, so that turning the variance knob on (or pinning a type that has
    a fee element) does not shift the seeded stream for everything downstream.
    """
    rolls = [rng.random() for _ in range(4)]
    var_roll = rng.random()

    def _in(band, roll):
        lo, hi = band
        return round(lo + (hi - lo) * roll, 4)

    rates = {
        "fringe": _in(_FRINGE, rolls[0]),
        "overhead": _in(_OVERHEAD, rolls[1]),
        "g_and_a": _in(_GA, rolls[2]),
        "profit": _in(_PROFIT, rolls[3]),
        # Set only by the opt-in knob. Zero means the negotiated rate is exactly
        # the rate the buildup supports, which is the default and the case a
        # reconciliation check should find clean.
        "variance": _in(_VARIANCE, var_roll) if variance else 0.0,
    }
    rates["wrap_rate"] = wrap_rate(rates)
    return rates


def wrap_rate(rates):
    """The cost wrap — what a dollar of direct labor costs fully burdened,
    before fee. This is the figure the generator used to draw directly as an
    opaque 2.0-2.45 multiplier; it is now what the three pools multiply out to.
    """
    return round(
        (1.0 + rates["fringe"]) * (1.0 + rates["overhead"]) * (1.0 + rates["g_and_a"]),
        6,
    )


def fee_for_rates(rates, fee_rate):
    """The profit component inside a loaded rate.

    On a type with a fee element (CPFF, CPIF, CPAF, FPI) it is the contract's
    own negotiated fee rate, so the rate buildup and the CLIN's stated
    cost/fee split are the same decomposition. On T&M and FFP there is no fee
    element, and the profit inside the rate is the company's own.
    """
    return float(fee_rate) if fee_rate else rates["profit"]


def loaded_rate(direct, rates, fee_rate):
    """The fully-burdened rate a direct rate builds up to, to the cent."""
    fee = fee_for_rates(rates, fee_rate)
    return round(float(direct) * wrap_rate(rates) * (1.0 + fee), 2)


def total_multiplier(rates, fee_rate):
    """Direct-to-loaded, all pools and fee — `loaded_rate`'s multiplier."""
    return wrap_rate(rates) * (1.0 + fee_for_rates(rates, fee_rate))


def buildup(direct_cost, rates, total_cost=None):
    """The indirect-pool rows between a direct labor total and total estimated
    cost, as dicts of (key, label, base_label, rate, amount, subtotal).

    Each pool's amount is the difference between the running subtotals, so the
    rows foot to the total exactly rather than each rounding independently.

    `total_cost`, when given, is the total estimated cost the exhibit must foot
    to — the figure the CLIN actually states. The last pool absorbs the
    difference (a few cents, from the per-line rounding of rates and extended
    amounts), which is how a real exhibit foots and is far below the resolution
    of the rate it prints.
    """
    rows = []
    running = float(direct_cost)
    for key, label, base_label in _POOLS:
        rate = rates[key]
        prev = running
        running = prev * (1.0 + rate)
        rows.append(
            {
                "key": key,
                "label": label,
                "base_label": base_label,
                "rate": rate,
                "amount": round(running, 2) - round(prev, 2),
                "subtotal": round(running, 2),
            }
        )
    if total_cost is not None and rows:
        last = rows[-1]
        last["amount"] = round(
            last["amount"] + (round(float(total_cost), 2) - last["subtotal"]), 2
        )
        last["subtotal"] = round(float(total_cost), 2)
    return rows


def rate_disclosure(rates):
    """The one-line statement of a company's indirect rates, as a cost-type
    award prints it above the buildup."""
    return (
        f"Indirect Rates: Fringe {rates['fringe'] * 100:.1f}%  |  "
        f"Overhead {rates['overhead'] * 100:.1f}%  |  "
        f"G&A {rates['g_and_a'] * 100:.1f}%"
    )
