"""Tests for per-CLIN obligated funding on generated awards.

The rules these pin down are the ones that make the funding data believable to
someone who reads real awards:
  * money is never obligated against a period that has not started
  * a period in flight is funded against the clock, not at a closed period's
    level
  * a fixed-price award is funded in full, not incrementally
  * obligations are whole dollars, and never exceed the line's ceiling
"""

from __future__ import annotations

import datetime

from testgen.presets import build_scenario


def _clins(contract):
    return [(p, c) for p in contract["periods"] for c in p["clins"]]


def _award(seed, **opts):
    return build_scenario(seed, opts)["contract"]


def test_unstarted_periods_are_never_exercised_or_funded():
    today = datetime.date.today()
    for seed in range(40):
        c = _award(seed, pop_in_progress=True, option_years=3)
        for p in c["periods"]:
            if p["pop_start"] > today:
                assert not p["exercised"], f"seed {seed}: future {p['name']} exercised"
                assert all((cl.get("funded") or 0) == 0 for cl in p["clins"])
                assert all(cl.get("acrn") is None for cl in p["clins"])


def test_a_mid_flight_period_is_not_funded_like_a_closed_one():
    # The bug this replaces: an option year wrongly exercised made the *active*
    # base year take the closed-period band (85-100% of ceiling) while it was
    # only half elapsed. A period in flight must track its own clock, so its
    # labor funding stays within reach of elapsed time — never the near-full
    # level of a period that has already been performed.
    today = datetime.date.today()
    checked = 0
    for seed in range(40):
        c = _award(seed, pop_in_progress=True, option_years=2, contract_type="CPFF")
        for p in c["periods"]:
            if not (p["exercised"] and p["pop_start"] <= today <= p["pop_end"]):
                continue
            span = (p["pop_end"] - p["pop_start"]).days
            elapsed = (today - p["pop_start"]).days / span
            for cl in p["clins"]:
                if not cl.get("is_labor"):
                    continue
                frac = cl["funded"] / cl["ceiling"]
                # Funded runs at most ~3 months of runway ahead of the clock,
                # and lags by at most ~2 months behind it.
                assert (
                    elapsed - 0.21 <= frac <= min(1.0, elapsed + 0.26)
                ), f"seed {seed}: {cl['clin']} funded {frac:.2f} at {elapsed:.2f} elapsed"
                checked += 1
    assert checked, "no mid-flight labor CLIN was generated to check"


def test_fixed_price_awards_are_funded_in_full():
    for seed in range(25):
        c = _award(seed, contract_type="FFP", option_years=2)
        for p, cl in _clins(c):
            expected = cl["ceiling"] if p["exercised"] else 0
            assert cl["funded"] == expected, f"seed {seed}: FFP CLIN {cl['clin']}"


def test_cost_type_awards_are_incrementally_funded():
    # The counterpart to the FFP rule: a cost-type award must NOT come out fully
    # funded, or the Limitation of Funds case disappears from the test data.
    for seed in range(25):
        c = _award(seed, contract_type="CPFF", option_years=1, pop_in_progress=True)
        assert c["total_obligated"] < c["total_ceiling"]


def test_partial_obligations_are_whole_dollars_within_ceiling():
    # A partial increment is a round figure. A line funded to its full price is
    # the exception: it matches that price to the cent.
    for seed in range(25):
        c = _award(seed, pop_in_progress=True, option_years=2)
        for _p, cl in _clins(c):
            funded = cl["funded"]
            assert funded <= cl["ceiling"] + 0.005
            if funded != cl["ceiling"]:
                assert funded == round(funded), f"seed {seed}: {cl['clin']} has cents"


def test_total_obligated_is_the_sum_of_the_clin_lines():
    for seed in range(25):
        c = _award(seed, pop_in_progress=True, option_years=2)
        assert round(c["total_obligated"], 2) == round(
            sum(cl["funded"] for _p, cl in _clins(c)), 2
        )


def test_both_funding_postures_are_reachable():
    # Some awards fund ahead of the clock, some fall behind — the lagging ones
    # are what the funding-pace tripwire is demonstrated against, so neither
    # posture may vanish from the generator.
    today = datetime.date.today()
    ahead = behind = 0
    for seed in range(60):
        c = _award(seed, pop_in_progress=True, contract_type="CPFF", option_years=1)
        for p in c["periods"]:
            if not (p["exercised"] and p["pop_start"] <= today <= p["pop_end"]):
                continue
            span = (p["pop_end"] - p["pop_start"]).days
            elapsed = (today - p["pop_start"]).days / span
            labor = [cl for cl in p["clins"] if cl.get("is_labor")]
            if not labor:
                continue
            frac = labor[0]["funded"] / labor[0]["ceiling"]
            if frac >= elapsed:
                ahead += 1
            else:
                behind += 1
    assert ahead and behind, f"ahead={ahead} behind={behind}"
