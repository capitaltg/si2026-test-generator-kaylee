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


def test_a_closed_period_is_fully_obligated():
    # A period that has been performed had to be funded to perform it. Anything
    # short of its ceiling would mean the contractor worked a year it was never
    # paid for.
    today = datetime.date.today()
    checked = 0
    for seed in range(40):
        c = _award(seed, pop_in_progress=True, option_years=3, funding="incremental")
        for p in c["periods"]:
            if p["pop_end"] >= today:
                continue
            for cl in p["clins"]:
                assert cl["funded"] == cl["ceiling"], (
                    f"seed {seed}: closed {cl['clin']} funded {cl['funded']} "
                    f"of {cl['ceiling']}"
                )
                checked += 1
    assert checked, "no closed period was generated to check"


def test_the_award_shows_only_what_it_obligated():
    # An award form is signed once, so it can only cite the money that signature
    # obligated. `funded_at_award` is that figure; `funded` is the cumulative as
    # of today, which includes obligations later mods made and which the award
    # cannot possibly show.
    for seed in range(30):
        c = _award(seed, pop_in_progress=True, option_years=2)
        award = c["obligation_history"][0]
        assert award["mod"] == "Award"
        at_award = sum(cl["funded_at_award"] for _p, cl in _clins(c))
        assert round(at_award, 2) == round(award["cumulative_obligated"], 2)
        for _p, cl in _clins(c):
            assert cl["funded_at_award"] <= cl["funded"] + 0.005
        # Only the base period can carry award-time funding: an option is
        # obligated by the mod that exercises it, not by the award.
        for p in c["periods"][1:]:
            assert all(cl["funded_at_award"] == 0 for cl in p["clins"])


def test_an_option_is_obligated_by_a_mod_dated_at_its_start():
    # The SF-30 exercising an option is signed within the notice window before
    # that period begins (FAR 52.217-9) — not months early, and not after the
    # period is under way.
    today = datetime.date.today()
    checked = 0
    for seed in range(40):
        c = _award(seed, pop_in_progress=True, option_years=3)
        by_name = {p["name"]: p for p in c["periods"]}
        for m in c["obligation_history"]:
            if not m["action"].startswith("Exercise option period ("):
                continue
            name = m["action"].split("(", 1)[1].rstrip(")")
            p = by_name[name]
            assert m["date"] <= min(p["pop_start"], today)
            assert m["date"] >= p["pop_start"] - datetime.timedelta(days=45)
            # And it funds that period's CLINs, nobody else's.
            assert {l["clin"] for l in m["funding_lines"]} <= {
                cl["clin"] for cl in p["clins"]
            }
            checked += 1
    assert checked, "no option-exercise mod was generated to check"


def test_the_timeline_reconciles_to_the_per_clin_funding():
    for seed in range(30):
        c = _award(seed, pop_in_progress=True, option_years=2)
        history = c["obligation_history"]
        assert round(history[-1]["cumulative_obligated"], 2) == round(
            c["total_obligated"], 2
        )
        # Every action's lines sum to that action's amount, and each CLIN's lines
        # across the whole timeline sum to what it is funded to.
        per_clin = {}
        for m in history:
            assert round(sum(l["amount"] for l in m["funding_lines"]), 2) == round(
                m["amount"], 2
            )
            for l in m["funding_lines"]:
                per_clin[l["clin"]] = per_clin.get(l["clin"], 0.0) + l["amount"]
        for _p, cl in _clins(c):
            assert round(per_clin.get(cl["clin"], 0.0), 2) == round(cl["funded"], 2)


def test_active_period_is_pinnable():
    # Pinning matters because an award form can only show what it obligated: on a
    # contract performing option year 2 the award says nothing about the period
    # in flight. `active_period: 0` is how a demo gets an award that does.
    today = datetime.date.today()
    for pin in (0, 1, 2):
        for seed in (3, 11, 42):
            c = _award(seed, pop_in_progress=True, option_years=2, active_period=pin)
            active = [
                p for p in c["periods"] if p["pop_start"] <= today <= p["pop_end"]
            ]
            assert len(active) == 1
            assert c["periods"].index(active[0]) == pin
    # Out of range clamps rather than raising.
    c = _award(3, pop_in_progress=True, option_years=1, active_period=9)
    assert any(p["pop_start"] <= today <= p["pop_end"] for p in c["periods"])


def test_n_mods_is_the_exact_number_of_mods():
    # An exercised option always costs one mod (it cannot be exercised without
    # one), so the pin sets how many *incremental* mods sit on top.
    for option_years in (0, 2):
        for n in (0, 1, 3):
            c = _award(
                7,
                pop_in_progress=True,
                option_years=option_years,
                n_mods=n,
                funding="incremental",
            )
            mods = [m for m in c["obligation_history"] if m["mod"] != "Award"]
            exercised_options = sum(
                1 for p in c["periods"][1:] if any(cl["funded"] for cl in p["clins"])
            )
            assert len(mods) == max(
                n, exercised_options
            ), f"option_years={option_years} n_mods={n} -> {len(mods)} mods"


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
