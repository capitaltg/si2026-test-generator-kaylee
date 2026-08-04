"""Tests that a contract type is a specification, not a label.

Before this, two awards of different types were the same document with a
different string on it: the type changed how often the award funded in full, one
letter in an SF-30 box, and nothing else. A CPFF award printed a single blended
ceiling where a real one states estimated cost and fixed fee as two figures a
Contracting Officer signed off on separately (FAR 16.306), and every award cited
FAR 52.232-22 whether or not it was incrementally funded.

What these pin down is the part a reader cannot check by eye across seven types
and forty seeds: that each type states the elements its FAR subpart requires,
that decomposing a total into cost and fee does not move the total (so the
roll-up invariants survive), and that the printed clause follows the type *and*
the funding profile rather than assuming the incremental case.
"""

from __future__ import annotations

from testgen import contract_types as ct
from testgen.presets import build_scenario


def _award(seed, **opts):
    return build_scenario(seed, opts)["contract"]


def _labor_clins(contract):
    return [c for p in contract["periods"] for c in p["clins"] if c.get("is_labor")]


def test_all_seven_types_generate_and_pin():
    for ctype in ct.KNOWN_TYPES:
        c = _award(3, contract_type=ctype)
        assert c["contract_type"] == ctype
        assert c["pricing"]["type"] == ctype
        assert c["pricing"]["far"], f"{ctype} has no FAR citation"
        assert c["pricing"]["cost_elements"], f"{ctype} states no cost elements"


def test_unpinned_draw_covers_every_type_and_favors_fixed_price():
    seen = {}
    for seed in range(400):
        t = _award(seed)["contract_type"]
        seen[t] = seen.get(t, 0) + 1
    assert set(seen) == set(
        ct.KNOWN_TYPES
    ), f"missing types: {set(ct.KNOWN_TYPES) - set(seen)}"
    # The weights exist so the corpus looks like federal services work: FFP is
    # the most common type and the three exotic variants are each rare.
    assert seen["FFP"] == max(seen.values())
    for rare in ("CPIF", "CPAF", "FPI"):
        assert seen[rare] < seen["CPFF"], f"{rare} should be rarer than CPFF"


def test_cost_type_clin_states_cost_and_fee_summing_to_its_total():
    for ctype in ("CPFF", "CPIF", "CPAF"):
        for seed in range(25):
            for clin in _labor_clins(_award(seed, contract_type=ctype)):
                cost, fee = clin["estimated_cost"], clin["fee"]
                assert fee > 0, f"{ctype} seed {seed}: no fee element"
                assert abs(cost + fee - clin["ceiling"]) < 0.01
                # Fee is a percentage of cost, not of the total.
                assert abs(cost * clin["fee_rate"] - fee) < 0.02


def test_fee_rate_sits_inside_the_types_band():
    for ctype in ("CPFF", "CPIF", "CPAF", "FPI"):
        lo, hi = ct.spec(ctype)["fee_band"]
        for seed in range(25):
            rate = _award(seed, contract_type=ctype)["pricing"]["fee_rate"]
            assert lo <= rate <= hi, f"{ctype} seed {seed}: {rate}"


def test_fixed_price_and_tm_clins_state_their_own_elements_not_fee():
    for seed in range(20):
        for clin in _labor_clins(_award(seed, contract_type="FFP")):
            assert clin["firm_price"] == clin["ceiling"]
            assert "fee" not in clin
        for clin in _labor_clins(_award(seed, contract_type="T&M")):
            # Profit is inside the negotiated hourly rate (FAR 52.232-7(a)), so
            # it is not a separate element; the ceiling price is the limit.
            assert clin["profit_in_rates"] is True
            assert clin["ceiling_price"] == clin["ceiling"]
            assert "fee" not in clin


def test_fpi_price_ceiling_sits_above_target_cost_plus_profit():
    for seed in range(20):
        for clin in _labor_clins(_award(seed, contract_type="FPI")):
            target = clin["target_cost"] + clin["target_profit"]
            assert abs(target - clin["target_price"]) < 0.01
            # FAR 16.403: the contractor bears cost above the price ceiling, so
            # a ceiling at or below the target price would be no incentive.
            assert clin["ceiling_price"] > target


def test_tm_materials_are_a_distinct_element_from_loaded_labor():
    """Under 52.232-7 the hourly rates cover labor and profit; materials are
    reimbursed at cost, which is why they are named separately."""
    found = False
    for seed in range(30):
        for p in _award(seed, contract_type="T&M")["periods"]:
            for clin in p["clins"]:
                if clin.get("cost_element") == "reimbursable_materials":
                    found = True
                    assert clin["is_labor"] is False
                    assert not clin["labor_rates"]
    assert found, "no reimbursable-materials line generated in 30 seeds"


def test_decomposing_cost_and_fee_leaves_every_roll_up_untouched():
    """Invariants 1-4 still hold with fee as a separate element — the elements
    are derived from the stated total, never added on top of it."""
    for ctype in ct.KNOWN_TYPES:
        for seed in range(15):
            c = _award(seed, contract_type=ctype)
            total = 0.0
            for p in c["periods"]:
                assert abs(sum(x["ceiling"] for x in p["clins"]) - p["ceiling"]) < 0.01
                total += p["ceiling"]
                for clin in p["clins"]:
                    lines = clin.get("labor_rates") or []
                    if lines:
                        rolled = sum(l["amount"] for l in lines)
                        assert abs(rolled - clin["ceiling"]) < 0.01
            assert abs(total - c["total_ceiling"]) < 0.01
            assert c["total_obligated"] <= c["total_ceiling"] + 0.01


def test_funding_clause_follows_the_type_and_the_funding_profile():
    # Fixed-price: no limitation clause exists to cite, under any profile.
    for ctype in ("FFP", "FPI"):
        for funding in ("full", "incremental"):
            c = _award(5, contract_type=ctype, funding=funding)
            assert c["pricing"]["funding_clause"] == ""
            assert c["fully_funded"] is True
    # Cost-reimbursement: -20 when fully funded, -22 when incremental.
    for ctype in ("CPFF", "CPIF", "CPAF"):
        full = _award(5, contract_type=ctype, funding="full")
        assert full["pricing"]["funding_clause_cite"] == "FAR 52.232-20"
        incr = _award(5, contract_type=ctype, funding="incremental")
        assert incr["pricing"]["funding_clause_cite"] == "FAR 52.232-22"
        # The ceiling clause is a separate fact and does not follow the profile:
        # reaching the estimated cost and running out of allotted funds are two
        # different hard stops with two different remedies.
        assert incr["pricing"]["ceiling_clause_cite"] == "FAR 52.232-20"
    # T&M: the ceiling price is what 52.232-7 governs, and an incrementally
    # funded T&M award is additionally limited to the funds allotted.
    tm = _award(5, contract_type="T&M", funding="full")
    assert tm["pricing"]["funding_clause_cite"] == "FAR 52.232-7"
    tm_incr = _award(5, contract_type="T&M", funding="incremental")
    assert tm_incr["pricing"]["funding_clause_cite"] == "FAR 52.232-22"
    assert tm_incr["pricing"]["ceiling_clause_cite"] == "FAR 52.232-7"


def test_idiq_resolves_its_clause_through_the_orders_pricing():
    for seed in range(30):
        c = _award(seed, contract_type="IDIQ", funding="full")
        pricing = c["pricing"]
        assert pricing["priced_as"] in ct.ORDER_PRICING_TYPES
        expected = {"T&M": "FAR 52.232-7", "CPFF": "FAR 52.232-20"}[
            pricing["priced_as"]
        ]
        assert pricing["funding_clause_cite"] == expected


def test_full_funding_odds_are_unchanged_for_the_original_four_types():
    """The odds moved into the spec; the values did not. A bundle that turned on
    one of these staying put should not have moved because of this ticket."""
    assert ct.full_funding_odds("FFP") == 1.0
    assert ct.full_funding_odds("T&M") == 0.5
    assert ct.full_funding_odds("IDIQ") == 0.5
    assert ct.full_funding_odds("CPFF") == 0.35


def test_an_unknown_pinned_type_falls_back_to_a_drawn_one():
    c = _award(5, contract_type="Cost Plus Percentage Of Cost")
    assert c["contract_type"] in ct.KNOWN_TYPES
