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

from testgen.presets import _obligation_step, build_scenario


def _clins(contract):
    return [(p, c) for p in contract["periods"] for c in p["clins"]]


def _award(seed, **opts):
    return build_scenario(seed, opts)["contract"]


def _tranche_frac(clin):
    """One obligation tranche as a fraction of the CLIN's ceiling — the amount a
    funded figure can sit below its target purely from rounding down."""
    return _obligation_step(clin["funded"]) / clin["ceiling"]


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
        c = _award(
            seed,
            pop_in_progress=True,
            option_years=2,
            contract_type="CPFF",
            funding="incremental",
        )
        for p in c["periods"]:
            if not (p["exercised"] and p["pop_start"] <= today <= p["pop_end"]):
                continue
            span = (p["pop_end"] - p["pop_start"]).days
            elapsed = (today - p["pop_start"]).days / span
            for cl in p["clins"]:
                if not cl.get("is_labor"):
                    continue
                frac = cl["funded"] / cl["ceiling"]
                # Funded runs at most ~3 months of runway ahead of the clock and
                # lags by at most ~2 months behind it, plus the one tranche the
                # obligation is rounded down by.
                tranche = _tranche_frac(cl)
                assert (
                    elapsed - 0.21 - tranche <= frac <= min(1.0, elapsed + 0.26)
                ), f"seed {seed}: {cl['clin']} funded {frac:.2f} at {elapsed:.2f} elapsed"
                checked += 1
    assert checked, "no mid-flight labor CLIN was generated to check"


def test_today_can_land_in_an_option_year():
    # A monitored contract is often in its second or third year, with the base
    # year performed and closed. When every award anchored today to the base
    # year, no generated option period was ever exercised or funded — option
    # ceilings read as permanently $0 obligated.
    today = datetime.date.today()
    active_names, funded_options = set(), 0
    for seed in range(60):
        c = _award(seed, pop_in_progress=True, option_years=3)
        for p in c["periods"]:
            if p["pop_start"] <= today <= p["pop_end"]:
                active_names.add(p["name"])
            if p["name"] != "Base Year" and any(cl["funded"] for cl in p["clins"]):
                funded_options += 1
    assert len(active_names) > 1, f"only ever in flight during {active_names}"
    assert funded_options, "no option period ever carried funding"


def test_periods_before_the_active_one_are_exercised_and_closed_out():
    # The anchor says this contract is alive in period N, so periods 1..N-1 were
    # performed: exercised, and funded at a closed period's level.
    today = datetime.date.today()
    for seed in range(40):
        c = _award(seed, pop_in_progress=True, option_years=3, funding="incremental")
        for p in c["periods"]:
            if p["pop_end"] >= today:
                continue
            assert p["exercised"], f"seed {seed}: closed {p['name']} not exercised"
            for cl in p["clins"]:
                if cl.get("is_labor"):
                    assert cl["funded"] / cl["ceiling"] >= 0.8, (
                        f"seed {seed}: closed {cl['clin']} funded "
                        f"{cl['funded'] / cl['ceiling']:.2f}"
                    )


def test_fixed_price_awards_are_funded_in_full():
    for seed in range(25):
        c = _award(seed, contract_type="FFP", option_years=2)
        for p, cl in _clins(c):
            expected = cl["ceiling"] if p["exercised"] else 0
            assert cl["funded"] == expected, f"seed {seed}: FFP CLIN {cl['clin']}"


def test_cost_type_awards_can_be_pinned_to_either_funding_posture():
    # Both cases have to be available on demand: incremental keeps the
    # Limitation of Funds scenario in the corpus, full is the FAR 32.702 default
    # a lot of real cost/T&M awards actually follow.
    for seed in range(25):
        incr = _award(
            seed,
            contract_type="CPFF",
            option_years=1,
            pop_in_progress=True,
            funding="incremental",
        )
        assert incr["total_obligated"] < incr["total_ceiling"]

        full = _award(
            seed,
            contract_type="CPFF",
            option_years=1,
            pop_in_progress=True,
            funding="full",
        )
        for p, cl in _clins(full):
            assert cl["funded"] == (cl["ceiling"] if p["exercised"] else 0)


def test_cost_type_awards_are_a_mix_of_both_postures():
    # Unpinned, neither posture may dominate to the point of vanishing. Measured
    # on the *exercised* periods: an unexercised option is unfunded either way,
    # so a whole-award balance can't tell the two postures apart.
    full = 0
    for seed in range(60):
        c = _award(seed, contract_type="CPFF", option_years=1, pop_in_progress=True)
        held = [cl for p, cl in _clins(c) if p["exercised"]]
        full += all(cl["funded"] == cl["ceiling"] for cl in held)
    assert 5 <= full <= 55, f"{full}/60 CPFF awards fully funded"


def test_partial_obligations_land_on_round_tranches():
    # A partial obligation is written in round increments, never as a percentage
    # of the ceiling. A line funded to its full price is the exception: it
    # matches that price to the cent.
    for seed in range(25):
        c = _award(seed, pop_in_progress=True, option_years=2)
        for _p, cl in _clins(c):
            funded = cl["funded"]
            assert funded <= cl["ceiling"] + 0.005
            if funded and funded != cl["ceiling"]:
                step = _obligation_step(funded)
                assert (
                    funded % step == 0
                ), f"seed {seed}: {cl['clin']} funded {funded} is not a {step} tranche"


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
        c = _award(
            seed,
            pop_in_progress=True,
            contract_type="CPFF",
            option_years=1,
            funding="incremental",
        )
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
