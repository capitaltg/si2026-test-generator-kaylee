"""Billable-hours correctness, direct hours, holidays and dated leave (#60)."""

import datetime
import random

from testgen import calendars, presets

from faker import Faker


def _contract(seed=5, **opts):
    rng = random.Random(seed)
    faker = Faker()
    faker.seed_instance(seed)
    return presets.build_contract(rng, faker, 0, opts)


def _labor_lines(c):
    return [
        line
        for period in c["periods"]
        for clin in period["clins"]
        for line in clin["labor_rates"]
    ]


# --- Problem 1: leave is not billable -----------------------------------------


def _timesheet(seed=19, rows=400, **opts):
    opts.setdefault("staffing", 1)
    return presets.generate_preset("govcon_timesheet", rows=rows, seed=seed, opts=opts)


def test_total_hours_is_billable_only():
    """The defect: total_hours used to include leave, and the consumer prices
    total_hours at the CLIN's loaded rate — billing PTO as direct labor."""
    for r in _timesheet():
        assert r["total_hours"] == round(r["reg_hours"] + r["ot_hours"], 1)


def test_paid_hours_carries_the_all_in_figure():
    for r in _timesheet():
        assert r["paid_hours"] == round(
            r["reg_hours"] + r["ot_hours"] + r["holiday_hours"] + r["leave_hours"], 1
        )
        assert r["paid_hours"] >= r["total_hours"]


def test_a_fully_absent_week_bills_nothing():
    rows = [r for r in _timesheet() if r["reg_hours"] == 0]
    assert rows, "expected at least one week fully out"
    assert all(r["total_hours"] == 0 for r in rows)


# --- Problem 2: direct hours, not the calendar --------------------------------


def test_est_hours_defaults_to_direct_hours():
    lines = _labor_lines(_contract())
    assert lines
    for line in lines:
        assert line["direct_hours_per_year"] == calendars.DIRECT_HOURS_PER_YEAR
        assert line["est_hours"] % calendars.DIRECT_HOURS_PER_YEAR == 0


def test_the_knob_restores_the_calendar_year():
    lines = _labor_lines(_contract(direct_hours=2080))
    assert all(line["est_hours"] % 2080 == 0 for line in lines)


def test_the_2080_assumption_overstated_every_clin_by_a_tenth():
    realistic = _contract()["total_ceiling"]
    calendar_max = _contract(direct_hours=2080)["total_ceiling"]
    ratio = realistic / calendar_max
    assert abs(ratio - 1880 / 2080) < 0.001


def test_base_salary_stays_a_calendar_figure():
    """A salaried employee is paid for the holidays and the PTO. Dividing a salary
    by direct hours would over-state the wage by the same margin."""
    for line in _labor_lines(_contract()):
        assert line["base_salary"] == round(line["direct_rate"] * 2080)


def test_roster_backs_out_the_fte_count_it_was_priced_at():
    """Dividing est_hours by the calendar year here would under-staff every line."""
    sc = presets.build_scenario(5, {"staffing": 1.0})
    lines = [
        line
        for clin in presets._active_period(sc["contract"])["clins"]
        for line in clin["labor_rates"]
    ]
    expected = sum(
        max(1, round(line["est_hours"] / line["direct_hours_per_year"]))
        for line in lines
    )
    assert len(sc["roster"]) == expected


# --- Problem 3: holidays and a leave schedule --------------------------------


def test_federal_holidays_are_the_eleven_on_their_observed_dates():
    days = calendars.federal_holidays(2026)
    assert len(days) == 11
    by_name = {name: day for day, name in days}
    # Independence Day 2026 falls on a Saturday: observed the preceding Friday.
    assert by_name["Independence Day"] == datetime.date(2026, 7, 3)
    # Weekday-defined holidays never move.
    assert by_name["Memorial Day"] == datetime.date(2026, 5, 25)
    assert by_name["Thanksgiving Day"] == datetime.date(2026, 11, 26)
    # Juneteenth has been federal since 2021; omitting it is the usual tell.
    assert "Juneteenth National Independence Day" in by_name


def test_sunday_holidays_observe_the_following_monday():
    by_name = dict((name, day) for day, name in calendars.federal_holidays(2027))
    assert by_name["Independence Day"] == datetime.date(2027, 7, 5)


def test_a_week_straddling_the_year_boundary_still_finds_new_years_day():
    assert calendars.holiday_hours("2026-01-02") == 8.0


def test_a_holiday_hits_the_whole_roster_in_the_same_week():
    rows = _timesheet(pop_in_progress=True)
    by_week = {}
    for r in rows:
        by_week.setdefault(r["week_ending"], set()).add(r["holiday_hours"])
    holiday_weeks = [w for w, hrs in by_week.items() if hrs != {0.0}]
    assert holiday_weeks
    for week in holiday_weeks:
        assert len(by_week[week]) == 1, f"{week} disagrees across the roster"
        assert calendars.holiday_hours(week) == next(iter(by_week[week]))


def test_leave_clusters_into_blocks_rather_than_isolated_days():
    weeks = [
        (datetime.date(2026, 1, 2) + datetime.timedelta(weeks=i)).isoformat()
        for i in range(52)
    ]
    multi = 0
    for i in range(40):
        plan = calendars.leave_plan(random.Random(f"seed{i}"), weeks)
        taken = sorted(weeks.index(w) for w in plan)
        assert taken, "a year should carry at least one vacation block"
        if any(b - a == 1 for a, b in zip(taken, taken[1:])):
            multi += 1
    # Two-week blocks are the point; they should be common, not a freak event.
    assert multi > 5


def test_vacation_is_a_full_week_not_a_single_day():
    weeks = [
        (datetime.date(2026, 1, 2) + datetime.timedelta(weeks=i)).isoformat()
        for i in range(52)
    ]
    plan = calendars.leave_plan(random.Random("block"), weeks)
    assert all(hours >= 24.0 for hours in plan.values())


def test_leave_is_seed_locked_to_the_person():
    a = presets.build_scenario(19, {"staffing": 1, "pop_in_progress": True})
    b = presets.build_scenario(19, {"staffing": 1, "pop_in_progress": True})
    assert a["leave_plans"] == b["leave_plans"]
    assert len(a["leave_plans"]) == len(a["roster"])


def test_a_week_never_exceeds_its_target():
    for r in _timesheet():
        assert r["reg_hours"] + r["holiday_hours"] + r["leave_hours"] <= 40.0


# --- Problem 3, the piece with no substitute: dated future absence -----------


def test_planned_leave_is_dated_ahead_of_the_timesheet():
    opts = {"staffing": 1, "pop_in_progress": True}
    sheet = presets.generate_preset("govcon_timesheet", rows=400, seed=19, opts=opts)
    plan = presets.generate_preset("govcon_planned_leave", rows=100, seed=19, opts=opts)
    assert plan
    last_actual = max(r["week_ending"] for r in sheet)
    assert all(r["week_ending"] > last_actual for r in plan)
    assert all(r["leave_hours"] > 0 for r in plan)
    assert {r["status"] for r in plan} <= {"Approved", "Requested"}


def test_planned_leave_covers_the_same_roster():
    opts = {"staffing": 1, "pop_in_progress": True}
    sheet = presets.generate_preset("govcon_timesheet", rows=400, seed=19, opts=opts)
    plan = presets.generate_preset("govcon_planned_leave", rows=100, seed=19, opts=opts)
    assert {r["employee_id"] for r in plan} <= {r["employee_id"] for r in sheet}
    assert {r["contract_no"] for r in plan} == {r["contract_no"] for r in sheet}


def test_planned_leave_never_repeats_an_absence():
    plan = presets.generate_preset(
        "govcon_planned_leave",
        rows=5000,
        seed=19,
        opts={"staffing": 1, "pop_in_progress": True},
    )
    keys = [(r["employee_id"], r["week_ending"]) for r in plan]
    assert len(keys) == len(set(keys))
