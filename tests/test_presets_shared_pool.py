"""#70 — a shared-pool person's labor category follows the person, not the seat.

`_shared_pool()` used to return identity only, a (name, employee_id) pair, and the
roster loop took the category off whatever *seat* the person landed in. One
generated person came out as "Administrative Support · Business Analyst · Senior
Software Engineer", which is not a career: those floors are a HS diploma and a
Master's, and no single set of credentials satisfies both.

That was harmless while the only downstream consumer counted hours. Runway #69 made
a person one global set of credentials, and #66 checks that set against each
contract's billed category — so an incoherent person is unrepresentable, and the
compliance feature would emit flags that are artifacts of data generation rather
than realistic findings. A demo cannot afford a fabricated finding.

The fix binds a shared person to a qualification *lineage* and lets them vary freely
inside it. What these tests pin:

  1. Categories a shared person holds across contracts share one family.
  2. Variation inside a family survives — Mid on one contract, Senior on another is
     career progression, and it is the legitimate case #66 exists to handle.
  3. Nobody holds two categories on the same contract (already true, kept true).
  4. Cross-contract overlap survives, because that overlap is what the portfolio
     conflict detector reads and what gives #66 anything to check.
  5. The family draw is a per-person substream: it does not move any other figure.
"""

from __future__ import annotations

import random
from collections import defaultdict

from faker import Faker

from testgen.presets import (
    _LCATS,
    _POOL_FAMILIES,
    _SHARED_POOL_SEED,
    _SHARED_POOL_SIZE,
    _employee,
    _lcat_family,
    _pool_seat,
    _shared_pool,
    build_scenario,
)


def _portfolio(n_contracts=12, staffing=1.5):
    """A synced set the way a portfolio tool would see one: several contracts
    generated with the shared pool on, keyed by person."""
    contracts = defaultdict(set)
    lcats = defaultdict(set)
    per_contract_lcats = defaultdict(set)
    names = {}
    for seed in range(1, n_contracts + 1):
        scenario = build_scenario(seed, {"shared_pool": True, "staffing": staffing})
        for member in scenario["roster"]:
            eid = member["employee_id"]
            names[eid] = member["employee"]
            contracts[eid].add(seed)
            lcats[eid].add(member["labor_category"])
            per_contract_lcats[(eid, seed)].add(member["labor_category"])
    return contracts, lcats, per_contract_lcats, names


# ----------------------------------------------------------- one person, one career


def test_every_reference_category_declares_a_lineage():
    # A category added without a family would silently never match a pool person,
    # which reads as "the shared pool stopped working" rather than as a missing key.
    for row in _LCATS:
        assert row.get("family"), row["lcat"]


def test_a_shared_person_holds_categories_from_one_family_only():
    # The ticket's headline defect. Before this, 10 of 114 people spanned families —
    # Administrative Support next to Senior Software Engineer.
    _, lcats, _, names = _portfolio()
    for eid, held in lcats.items():
        families = {_lcat_family(lcat) for lcat in held}
        assert (
            len(families) == 1
        ), f"{names[eid]} spans {sorted(families)}: {sorted(held)}"


def test_the_same_holds_at_a_larger_portfolio():
    # The incoherent count grew with the set (38 of 590 at this size before the fix),
    # so the check has to run somewhere the old behaviour would visibly fail.
    _, lcats, _, names = _portfolio(n_contracts=20, staffing=2.0)
    bad = {
        names[eid]: sorted(held)
        for eid, held in lcats.items()
        if len({_lcat_family(lcat) for lcat in held}) > 1
    }
    assert not bad, bad


# --------------------------------------------------- but a career is still a career


def test_level_variation_inside_a_family_is_kept():
    # Explicitly NOT "one person, one LCAT". A senior engineer billed as a Systems
    # Engineer on one award and a Senior Software Engineer on another is normal
    # GovCon, and deleting it would delete the case the compliance check handles.
    _, lcats, _, _ = _portfolio()
    varied = [held for held in lcats.values() if len(held) > 1]
    assert varied, "no one bills more than one category — #66 has nothing to check"


def test_nobody_holds_two_categories_on_the_same_contract():
    # True before the change and kept true: the defect was across contracts, and a
    # person double-billed inside one contract would be a different, worse bug.
    _, _, per_contract, names = _portfolio()
    for (eid, seed), held in per_contract.items():
        assert len(held) == 1, f"{names[eid]} bills {sorted(held)} on contract {seed}"


def test_cross_contract_overlap_survives_the_constraint():
    # Binding people to a lineage costs some overlap — that is the intended trade —
    # but the overlap is the entire reason the shared pool exists. If it collapsed,
    # the portfolio conflict detector would have nothing to detect.
    contracts, _, _, _ = _portfolio()
    shared = [eid for eid, seeds in contracts.items() if len(seeds) > 1]
    assert len(shared) >= 10, len(shared)


def test_the_pool_is_off_by_default():
    # Each contract keeps its own roster unless asked. Sharing everyone would read
    # as "all staff booked 500%".
    plain = {m["employee_id"] for m in build_scenario(1, {"staffing": 1.5})["roster"]}
    other = {m["employee_id"] for m in build_scenario(2, {"staffing": 1.5})["roster"]}
    assert not (plain & other)


# ------------------------------------------------------------ the lineage itself


def test_a_pool_person_carries_a_stable_family():
    pool = _shared_pool()
    assert len(pool) == _SHARED_POOL_SIZE
    again = _shared_pool()
    assert pool == again
    for _name, _eid, family in pool:
        assert family in set(_POOL_FAMILIES)


def test_the_family_draw_does_not_move_anyone_s_identity():
    # The substream discipline `leave_plans` follows: a per-person Random keyed off
    # the employee id, never the pool's own stream. If the family were drawn from
    # that stream instead, every name after the first would shift — and a seed would
    # stop meaning what it meant.
    faker = Faker()
    faker.seed_instance(_SHARED_POOL_SEED)
    identities = [_employee(faker) for _ in range(_SHARED_POOL_SIZE)]
    assert [(n, e) for n, e, _f in _shared_pool()] == identities


def test_a_family_is_stable_for_a_given_person_not_a_position():
    by_id = {eid: fam for _n, eid, fam in _shared_pool()}
    for eid, fam in by_id.items():
        assert random.Random(f"family|{eid}").choice(_POOL_FAMILIES) == fam


def test_engineering_is_reachable_in_proportion_to_its_categories():
    # Weighted by each lineage's share of the categories rather than uniformly over
    # the five families, so engineering seats — three of the seven categories — are
    # not chasing a fifth of the pool. A uniform draw would shed far more overlap
    # than the constraint needs to cost.
    families = [fam for _n, _e, fam in _shared_pool()]
    assert families.count("engineering") > families.count("admin")


# --------------------------------------------------------------- the seat fallback


def test_a_seat_with_no_matching_person_falls_back_rather_than_mis_seating():
    # The one thing that must never happen: a person seated in a category they
    # aren't qualified for. Nobody available is answered with a fresh unique
    # employee, not with the nearest warm body.
    pool = [("Ada", "E-00001", "engineering"), ("Grace", "E-00002", "engineering")]
    name, eid, next_k = _pool_seat(pool, 0, 0, "cyber")
    assert (name, eid) == (None, None)
    assert next_k == 1


def test_a_seat_probes_past_a_mismatch_to_find_its_own_lineage():
    # First-pick-or-give-up would have cut the overlap to roughly a quarter. Probing
    # keeps it while still refusing to seat the wrong person.
    pool = [
        ("Ada", "E-00001", "engineering"),
        ("Grace", "E-00002", "cyber"),
        ("Katherine", "E-00003", "admin"),
    ]
    name, eid, next_k = _pool_seat(pool, 0, 0, "admin")
    assert (name, eid) == ("Katherine", "E-00003")
    assert next_k > 0
