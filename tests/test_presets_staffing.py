"""Tests for the opt-in `staffing` factor on scenario presets.

Unset, the roster keeps its original one-person-per-labor-line shape (unchanged
output for every existing caller). Set, each line is crewed to round(staffing *
its FTEs) people, and `scenario_roster_size` reports that count so the UI can
size rows to show every person once.
"""

from __future__ import annotations

from testgen.presets import build_scenario, scenario_roster_size


def test_default_roster_is_one_person_per_line():
    # No staffing (and empty opts) => one roster entry per labor line, stable.
    base = build_scenario(42, None)
    assert len(base["roster"]) == len(build_scenario(42, {})["roster"])
    # Every entry is a distinct line assignment with the expected fields.
    for member in base["roster"]:
        assert set(member) >= {"employee", "labor_category", "clin", "bill_rate"}


def test_staffing_scales_roster_up():
    unset = len(build_scenario(42, None)["roster"])
    on_plan = len(build_scenario(42, {"staffing": 1.0})["roster"])
    hot = len(build_scenario(42, {"staffing": 1.2})["roster"])
    # A staffing factor fields more people than the one-per-line default, and a
    # higher factor fields at least as many as a lower one.
    assert on_plan > unset
    assert hot >= on_plan


def test_staffing_never_drops_below_one_per_line():
    # Even a tiny factor keeps at least one person per line (max(1, ...)).
    tiny = len(build_scenario(42, {"staffing": 0.01})["roster"])
    assert tiny >= len(build_scenario(42, None)["roster"])


def test_roster_size_matches_scenario_and_ignores_private_opts():
    for staffing in (None, 0.25, 1.0, 1.2):
        opts = {} if staffing is None else {"staffing": staffing}
        expected = len(build_scenario(42, opts)["roster"])
        # A leftover private "_scenario" key must not leak into the rebuild.
        got = scenario_roster_size(
            "govcon_timesheet", seed=42, opts={**opts, "_scenario": object()}
        )
        assert got == expected


def test_roster_size_is_none_for_non_scenario_preset():
    assert scenario_roster_size("govcon_award_sf1449", seed=42) is None
