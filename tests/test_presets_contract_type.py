"""Tests that a labor CLIN's type agrees with the award's contract type.

The bug these pin down: the labor CLIN type used to be drawn at random for every
non-FFP award, so a pinned CPFF header stamped `T&M` on its own line items about
two thirds of the time. A consumer that resolves pricing per CLIN (which is the
correct reading — mixed-type awards are normal) then reported a CPFF contract as
T&M, and the fixture was what was wrong.

IDIQ is the deliberate exception: orders under a vehicle are priced individually
(FAR 16.504), so a per-order draw is a real answer there.
"""

from __future__ import annotations

from testgen.presets import build_scenario


def _award(seed, **opts):
    return build_scenario(seed, opts)["contract"]


def _labor_types(contract):
    return {
        c["type"] for p in contract["periods"] for c in p["clins"] if c.get("is_labor")
    }


def test_pinned_contract_type_is_stamped_on_every_labor_clin():
    for ctype in ("FFP", "T&M", "CPFF"):
        for seed in range(40):
            c = _award(seed, contract_type=ctype)
            assert c["contract_type"] == ctype
            assert _labor_types(c) == {ctype}, f"seed {seed}: {ctype} award"


def test_idiq_draws_its_order_pricing_per_award():
    seen = set()
    for seed in range(40):
        c = _award(seed, contract_type="IDIQ")
        assert c["contract_type"] == "IDIQ"
        types = _labor_types(c)
        assert types <= {"T&M", "CPFF"}, f"seed {seed}: {types}"
        seen |= types
    # The draw is intentional, so both outcomes have to be reachable.
    assert seen == {"T&M", "CPFF"}


def test_unpinned_awards_agree_with_their_own_header():
    for seed in range(60):
        c = _award(seed)
        if c["contract_type"] == "IDIQ":
            continue
        assert _labor_types(c) == {c["contract_type"]}, f"seed {seed}"


def test_non_labor_lines_stay_cost_reimbursable():
    # A cost travel/ODC line on a fixed-price award is real; it must not be
    # relabelled to the header's type.
    for ctype in ("FFP", "T&M", "CPFF", "IDIQ"):
        for seed in range(20):
            c = _award(seed, contract_type=ctype)
            for p in c["periods"]:
                for cl in p["clins"]:
                    if not cl.get("is_labor"):
                        assert cl["type"] == "COST", f"seed {seed}: {ctype}"
