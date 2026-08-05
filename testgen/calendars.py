"""Federal holidays, annual direct hours, and the leave schedule a timesheet
reflects.

Three things live here, and they are the same subject seen three ways: how many
hours of a year are actually available to charge to a contract.

**Annual direct hours.** A staffing plan is not priced at 52 x 40. Eleven federal
holidays and typical PTO take roughly 200 hours out of the calendar year, which
puts *direct* hours near 1,880 — and a proposal's estimated hours reflect that
utilisation assumption, not the calendar maximum. The generator used to price
`fte * 2080`, which over-stated estimated hours by about 10% and, since a CLIN's
amount is rate x hours, over-stated every CLIN by the same margin.

**Holidays.** The eleven of them, on their real observed dates (5 U.S.C. 6103):
a holiday falling on a Saturday is observed the preceding Friday, one falling on
a Sunday the following Monday. They are the single most predictable dip in a
contract's burn — everybody is off, in the same week, and the dates are known
years ahead. Holiday hours are neither billable nor leave: they are their own
category, paid out of the fringe pool like PTO but not drawn from a balance.

**Leave.** A per-person weekly draw of 0-or-8-or-16 hours is noise, not a
schedule. It never clusters into a two-week vacation, so a consumer building a
plan-around-known-absence feature has nothing to plan around. What generates here
instead is a *plan*: contiguous blocks, seasonally skewed toward summer and late
December, seed-locked per person — and, for a live contract, dated forward past
today, which is the one piece historical timesheet data cannot substitute for.
"""

from __future__ import annotations

import datetime

# Direct (chargeable) hours in a year, the figure a staffing plan is actually
# priced at: 2,080 calendar hours less 88 of holiday (11 x 8) and roughly 112 of
# PTO (14 days). Contractors' own utilisation assumptions land in a band around
# this; 1,880 is the middle of it and a far better default than the calendar.
DIRECT_HOURS_PER_YEAR = 1880

# The calendar year. Still the right divisor for an annual salary — a salaried
# employee is paid for the holidays and the PTO — which is why base_salary keeps
# using it even though estimated *direct* hours no longer do.
CALENDAR_HOURS_PER_YEAR = 2080

HOURS_PER_DAY = 8.0

# Fixed-date holidays. These are the ones the observance rule applies to; a
# holiday pinned to a weekday can never land on a weekend.
_FIXED = (
    (1, 1, "New Year's Day"),
    (6, 19, "Juneteenth National Independence Day"),
    (7, 4, "Independence Day"),
    (11, 11, "Veterans Day"),
    (12, 25, "Christmas Day"),
)

# (month, weekday, nth, name) — weekday 0 = Monday.
_NTH = (
    (1, 0, 3, "Birthday of Martin Luther King, Jr."),
    (2, 0, 3, "Washington's Birthday"),
    (9, 0, 1, "Labor Day"),
    (10, 0, 2, "Columbus Day"),
    (11, 3, 4, "Thanksgiving Day"),
)

# Memorial Day is the one holiday defined from the end of a month rather than the
# start of it: the LAST Monday in May, which is the fourth in most years and the
# fifth when May has five.
_MEMORIAL = (5, 0, "Memorial Day")

# Which weeks a vacation block is likely to start in. Summer and the week of
# Christmas dominate, spring break shows a little; nobody plans a vacation for
# the week their fiscal year closes. Any month not listed weighs 1.
_SEASON_WEIGHT = {7: 5, 8: 4, 12: 4, 6: 3, 3: 2, 11: 2, 5: 2}

# Vacation block lengths, in weeks. One week is the common case; two is the real
# reason a plan-around-absence feature exists.
_BLOCK_WEEKS = (1, 1, 1, 2, 2)

# Hours in a vacation week — usually the whole week, sometimes a few days of it
# bracketed by work.
_BLOCK_HOURS = (40.0, 40.0, 40.0, 32.0, 24.0)

# Roughly two weeks of vacation a year, which is what the ~112 PTO hours inside
# DIRECT_HOURS_PER_YEAR amounts to.
_VACATION_WEEKS_PER_YEAR = 2.0

_LEAVE_TYPES = ("Vacation", "Vacation", "Vacation", "Personal", "Family Leave")
_LEAVE_STATUS = ("Approved", "Approved", "Approved", "Requested")


def observed(d):
    """The date a fixed-date holiday is actually observed (5 U.S.C. 6103(b)):
    Saturday moves back to Friday, Sunday forward to Monday."""
    if d.weekday() == 5:
        return d - datetime.timedelta(days=1)
    if d.weekday() == 6:
        return d + datetime.timedelta(days=1)
    return d


def _nth_weekday(year, month, weekday, nth):
    first = datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 7 * (nth - 1))


def _last_weekday(year, month, weekday):
    nxt = (
        datetime.date(year + 1, 1, 1)
        if month == 12
        else datetime.date(year, month + 1, 1)
    )
    last = nxt - datetime.timedelta(days=1)
    return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)


def federal_holidays(year):
    """The eleven federal holidays for one year, as (observed date, name), in
    date order. Juneteenth is included — it has been a federal holiday since
    2021, and omitting it is the usual tell of a hand-built holiday table."""
    days = [(observed(datetime.date(year, m, d)), name) for m, d, name in _FIXED]
    days += [(_nth_weekday(year, m, wd, nth), name) for m, wd, nth, name in _NTH]
    month, weekday, name = _MEMORIAL
    days.append((_last_weekday(year, month, weekday), name))
    return tuple(sorted(days))


def _as_date(value):
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def holidays_in_week(week_ending):
    """The federal holidays falling in the Monday-Friday work week ending on the
    given Friday, as (date, name).

    Both adjacent years are searched, in both directions. A work week can straddle
    a year boundary — New Year's Day 2026 falls in the week beginning 29 December
    2025 — and the observance rule moves dates across the boundary too, since
    1 January on a Saturday is observed the preceding 31 December.
    """
    friday = _as_date(week_ending)
    monday = friday - datetime.timedelta(days=4)
    found = set()
    for year in {monday.year - 1, monday.year, friday.year, friday.year + 1}:
        for day, name in federal_holidays(year):
            if monday <= day <= friday:
                found.add((day, name))
    return tuple(sorted(found))


def holiday_hours(week_ending):
    """Non-billable holiday hours in a work week — eight per observed holiday.

    Everybody in a scenario gets the same figure for the same week, which is the
    point: a holiday dip is correlated across the whole roster, unlike PTO.
    """
    return HOURS_PER_DAY * len(holidays_in_week(week_ending))


def _weighted_pick(rng, items, weights):
    total = sum(weights)
    roll = rng.random() * total
    running = 0.0
    for item, weight in zip(items, weights):
        running += weight
        if roll < running:
            return item
    return items[-1]


def leave_plan(rng, weeks, per_year=_VACATION_WEEKS_PER_YEAR):
    """One person's vacation schedule over a list of week-ending Fridays, as
    {week_ending: hours}.

    Leave lands in contiguous blocks rather than isolated days, and blocks start
    where people actually take them. `rng` is expected to be a per-person
    substream (derived from the PIID and the employee id) so a plan is seed-locked
    without any of it coming out of the shared stream.
    """
    weeks = list(weeks or [])
    if not weeks:
        return {}
    n_blocks = max(1, round(len(weeks) * per_year / 52.0))
    weights = [_SEASON_WEIGHT.get(_as_date(w).month, 1) for w in weeks]
    plan = {}
    for _ in range(n_blocks):
        span = _weighted_pick(rng, _BLOCK_WEEKS, [1] * len(_BLOCK_WEEKS))
        start = weeks.index(_weighted_pick(rng, weeks, weights))
        hours = _weighted_pick(rng, _BLOCK_HOURS, [1] * len(_BLOCK_HOURS))
        for offset in range(span):
            if start + offset < len(weeks):
                # A block overlapping one already planned does not stack: a person
                # cannot take eighty hours of leave in a forty-hour week.
                plan[weeks[start + offset]] = hours
    return plan


def future_weeks(after, count):
    """`count` week-ending Fridays strictly after the given date — the forward
    axis a planned-absence schedule is dated on."""
    day = _as_date(after)
    first = day + datetime.timedelta(days=((4 - day.weekday()) % 7) or 7)
    return [
        (first + datetime.timedelta(weeks=i)).isoformat() for i in range(max(0, count))
    ]


def planned_absences(rng, weeks, per_year=_VACATION_WEEKS_PER_YEAR):
    """A forward-looking leave schedule: the same clustered plan, dated ahead of
    today, with a type and an approval status.

    This is the piece with no substitute. Known future absence is the input to a
    what-if projection ("what happens to the burn if these three people are out
    in August"), and no amount of historical timesheet data provides it — history
    says who WAS out, never who WILL be.
    """
    plan = leave_plan(rng, weeks, per_year)
    rows = []
    for week in weeks:
        if week not in plan:
            continue
        rows.append(
            {
                "week_ending": week,
                "leave_hours": plan[week],
                "leave_type": _weighted_pick(
                    rng, _LEAVE_TYPES, [1] * len(_LEAVE_TYPES)
                ),
                "status": _weighted_pick(rng, _LEAVE_STATUS, [1] * len(_LEAVE_STATUS)),
            }
        )
    return rows
