"""Tests for the opt-in `pop_in_progress` generation option.

The default (opt OFF) generation must be byte-for-byte what it always was; the
opt ON must anchor the award's base period of performance to the present so a
burn/runway tool sees a mid-flight contract whose timesheets fall inside it.
"""

from __future__ import annotations

import datetime

from testgen.presets import _active_period, build_scenario, generate_preset


def _base_pop(scenario):
    base = scenario["contract"]["periods"][0]
    return base["pop_start"], base["pop_end"]


def _active_pop(scenario):
    """The period the contract is performing in. `pop_in_progress` anchors today
    inside one of the award's periods — not necessarily the base year, since a
    monitored contract is often in an option year."""
    active = _active_period(scenario["contract"])
    return active["pop_start"], active["pop_end"]


def test_default_effective_date_is_unchanged_and_deterministic():
    # With the opt OFF (and absent), seed 42's award anchors to the historical
    # default, independent of today's date — proving core generation is untouched.
    for opts in (None, {}):
        scenario = build_scenario(42, opts)
        assert scenario["contract"]["effective_date"] == datetime.date(2024, 1, 26)


def test_default_generation_is_reproducible():
    a = generate_preset("govcon_timesheet", rows=25, seed=42)
    b = generate_preset("govcon_timesheet", rows=25, seed=42)
    assert a == b


def test_pop_in_progress_anchors_a_period_to_today():
    today = datetime.date.today()
    for seed in (42, 7, 13, 19, 21):
        scenario = build_scenario(seed, {"pop_in_progress": True})
        start, end = _active_pop(scenario)
        assert start <= today <= end
        # Today should sit well inside that period (~5-10 months in), never at
        # day 0.
        weeks_in = (today - start).days // 7
        assert 20 <= weeks_in <= 44
        # And the period in flight has to be one the contract actually holds.
        active = _active_period(scenario["contract"])
        assert active["exercised"]


def test_timesheet_weeks_fall_inside_pop_when_on():
    # Hours must charge the CLINs of the period the contract is actually
    # performing in, and fall inside that period's window. Anchoring the roster
    # to the base year while today sits in an option year would bill a period
    # that closed a year ago.
    for seed in (42, 7, 13, 19, 21):
        scenario = build_scenario(seed, {"pop_in_progress": True})
        start, end = _active_pop(scenario)
        piid = scenario["contract"]["piid"]
        active_clins = {
            c["clin"] for c in _active_period(scenario["contract"])["clins"]
        }

        rows = generate_preset(
            "govcon_timesheet", rows=25, seed=seed, opts={"pop_in_progress": True}
        )
        for row in rows:
            week = datetime.date.fromisoformat(row["week_ending"])
            assert start <= week <= end
            assert row["contract_no"] == piid
            assert row["charge_code"] in active_clins


def test_pop_option_does_not_leak_into_default_run():
    # A default run right after an opt-in run must still be the historical default.
    generate_preset("govcon_timesheet", rows=5, seed=42, opts={"pop_in_progress": True})
    scenario = build_scenario(42)
    assert scenario["contract"]["effective_date"] == datetime.date(2024, 1, 26)


def test_constraint_opts_are_honored():
    opts = {
        "agency": "Department of the Navy",
        "contract_type": "FFP",
        "set_aside": "8(a)",
        "option_years": 3,
        "lcat_lines": 2,
    }
    contract = build_scenario(42, opts)["contract"]
    assert contract["agency"] == "Department of the Navy"
    assert contract["contract_type"] == "FFP"
    assert contract["set_aside"] == "8(a)"
    assert len(contract["periods"]) - 1 == 3  # base year + 3 option years
    labor = [c for c in contract["periods"][0]["clins"] if c.get("is_labor")]
    assert all(len(c["labor_rates"]) == 2 for c in labor)


def test_zero_option_years_is_pinnable():
    contract = build_scenario(42, {"option_years": 0})["contract"]
    assert len(contract["periods"]) == 1  # base year only


def test_empty_and_unknown_opts_fall_back_to_random():
    # Blank strings from the UI (and unknown labels) must not crash or pin.
    opts = {
        "agency": "",
        "contract_type": "",
        "set_aside": "Not A Real Category",
        "option_years": "",
        "lcat_lines": "",
    }
    a = build_scenario(42, opts)["contract"]
    b = build_scenario(42)["contract"]
    # Falling back to random means seed 42 reproduces the untuned contract exactly.
    assert a["contract_type"] == b["contract_type"]
    assert a["set_aside"] == b["set_aside"]
    assert a["agency"] == b["agency"]
