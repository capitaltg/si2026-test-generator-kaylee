"""Tests that fee is a generated structure and not a percentage on the end of a
ceiling.

Before this, a cost-type CLIN carried one fee figure and nothing that said how it
gets paid. That is enough for CPFF, where the fee is fixed at award and never
moves, and it is not enough for either type where the fee is the interesting part:
a CPAF pool is earned period by period by determination and forfeited when it is
not earned, and a CPIF fee slides with cost between a minimum and a maximum. Both
are structures with their own invariants, and a consumer validates against exactly
those.

What these pin down is the part a reader cannot check by eye:

  * the elements each type's spec declares are actually generated on its CLINs;
  * the identities — cost + fee == the CLIN total, base fee + pool == the fee,
    the evaluation shares == the pool, min <= target <= max fee;
  * that obligation is allocated against cost *and* fee, which is the one that
    makes every downstream funded-position figure wrong if it is missed;
  * that a live award has determined periods behind it and undetermined fee ahead
    of it, because fee earned vs. fee at risk is the state that cannot be faked
    from a static award document.
"""

from __future__ import annotations

import datetime
import io
import random

from pypdf import PdfReader

from testgen import contract_types as ct
from testgen.fillable import render_fillable
from testgen.presets import PRESETS, build_scenario, generate_preset
from testgen.schedule import sf26_section_b

_COST_PLUS = ("CPFF", "CPIF", "CPAF")


def _award(seed, **opts):
    return build_scenario(seed, opts)["contract"]


def _labor_clins(contract):
    return [c for p in contract["periods"] for c in p["clins"] if c.get("is_labor")]


def _plan(contract):
    return (contract.get("pricing") or {}).get("award_fee")


def _live_cpaf(seed, active_period=2, **opts):
    return _award(
        seed,
        contract_type="CPAF",
        pop_in_progress=True,
        active_period=active_period,
        **opts,
    )


def _sheet_text(contract):
    reader = PdfReader(io.BytesIO(sf26_section_b(contract)))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_each_type_generates_the_elements_its_spec_declares():
    """#52 declared what each type carries; the point of this ticket is that the
    declaration and the generated CLIN cannot drift apart."""
    for ctype in ("CPFF", "CPIF", "CPAF", "FPI"):
        declared = [e for e in ct.cost_elements(ctype) if e != "total"]
        for seed in range(10):
            for clin in _labor_clins(_award(seed, contract_type=ctype)):
                for element in declared:
                    assert clin.get(element) is not None, f"{ctype} lacks {element}"


def test_a_cpff_fixed_fee_is_a_dollar_amount_the_clin_total_includes():
    """FAR 16.306: the fee is a dollar figure set at award — stated under its own
    name so nothing downstream reads it as a rate to reapply to actual cost."""
    for seed in range(20):
        for clin in _labor_clins(_award(seed, contract_type="CPFF")):
            assert clin["fixed_fee"] == clin["fee"] > 0
            assert (
                abs(clin["estimated_cost"] + clin["fixed_fee"] - clin["ceiling"]) < 0.01
            )


def test_cpaf_base_fee_and_pool_split_the_fee_without_moving_it():
    for agency in ("Department of the Army", "General Services Administration"):
        for seed in range(15):
            c = _award(seed, contract_type="CPAF", agency=agency)
            pricing = c["pricing"]
            assert (
                abs(
                    pricing["base_fee_rate"]
                    + pricing["award_fee_rate"]
                    - pricing["fee_rate"]
                )
                < 1e-9
            )
            # Most of the fee is at risk, or the arrangement is CPFF wearing a
            # different name.
            assert pricing["award_fee_rate"] >= pricing["base_fee_rate"] - 0.001
            for clin in _labor_clins(c):
                assert (
                    abs(clin["base_fee"] + clin["award_fee_pool"] - clin["fee"]) < 0.01
                )
                assert (
                    abs(clin["estimated_cost"] + clin["fee"] - clin["ceiling"]) < 0.01
                )


def test_dod_base_fee_respects_the_dfars_cap():
    """DFARS 215.404-74 caps base fee at 3% of estimated cost on a DoD award-fee
    contract. A civilian agency is not bound by it, which is why the agency is an
    input to the split at all."""
    for seed in range(40):
        c = _award(seed, contract_type="CPAF", agency="Department of the Navy")
        assert c["pricing"]["dod"] is True
        assert c["pricing"]["base_fee_rate"] <= 0.03 + 1e-9
    civilian = _award(3, contract_type="CPAF", agency="General Services Administration")
    assert civilian["pricing"]["dod"] is False


def test_an_award_fee_plan_is_generated_for_cpaf_and_nothing_else():
    assert _plan(_live_cpaf(4)) is not None
    for ctype in ("FFP", "T&M", "CPFF", "CPIF", "FPI", "IDIQ"):
        c = _award(4, contract_type=ctype, pop_in_progress=True)
        assert _plan(c) is None, f"{ctype} generated an award fee plan"


def test_evaluation_periods_tile_the_exercised_pop_contiguously():
    """The CPAF half of invariant 6: the evaluation periods are contiguous, do not
    overlap, and stay inside the period of performance they divide."""
    for seed in range(15):
        c = _live_cpaf(seed)
        exercised = [p for p in c["periods"] if p["exercised"]]
        evaluations = _plan(c)["evaluations"]
        assert evaluations
        assert evaluations[0]["start"] == exercised[0]["pop_start"]
        assert evaluations[-1]["end"] == exercised[-1]["pop_end"]
        for prev, nxt in zip(evaluations, evaluations[1:]):
            assert prev["end"] < nxt["start"]
            assert nxt["start"] - prev["end"] == datetime.timedelta(days=1)
        for e in evaluations:
            assert e["start"] <= e["end"]
            # Inside the contract period it belongs to, not merely inside the PoP.
            period = next(p for p in exercised if p["name"] == e["contract_period"])
            assert period["pop_start"] <= e["start"]
            assert e["end"] <= period["pop_end"]


def test_evaluation_shares_sum_to_the_pool_the_clins_state():
    for seed in range(15):
        c = _live_cpaf(seed)
        plan = _plan(c)
        pool = sum(
            cl.get("award_fee_pool") or 0
            for p in c["periods"]
            if p["exercised"]
            for cl in p["clins"]
        )
        assert abs(plan["award_fee_pool"] - pool) < 0.02
        shares = sum(e["available_fee"] for e in plan["evaluations"])
        assert abs(shares - plan["award_fee_pool"]) < 0.02
        # An option nobody has exercised has no evaluation periods: its fee is not
        # under contract until the SF-30 that exercises it.
        assert {e["contract_period"] for e in plan["evaluations"]} == {
            p["name"] for p in c["periods"] if p["exercised"]
        }


def test_a_live_award_has_determined_fee_behind_it_and_undetermined_fee_ahead():
    """The single most valuable piece of test data in the ticket: fee earned
    versus fee still at risk, which no award document states because it is signed
    before any determination exists."""
    today = datetime.date.today()
    seen_determined = seen_open = seen_forfeit = False
    for seed in range(15):
        plan = _plan(_live_cpaf(seed))
        determined = [e for e in plan["evaluations"] if e["status"] == "determined"]
        open_periods = [e for e in plan["evaluations"] if e["status"] != "determined"]
        assert determined, "a mid-flight award with no determination behind it"
        seen_determined = seen_determined or bool(determined)
        seen_open = seen_open or bool(open_periods)
        for e in determined:
            assert e["end"] < today and e["determination_date"] <= today
            assert e["rating"] and e["score"] is not None
            # Earned is a percentage of what the period had available, so it is
            # strictly below it unless the score is a perfect 100 (never drawn).
            assert 0 <= e["fee_earned"] < e["available_fee"]
            assert abs(e["fee_earned"] + e["fee_forfeited"] - e["available_fee"]) < 0.01
            seen_forfeit = seen_forfeit or e["fee_forfeited"] > 0
        for e in open_periods:
            assert e["score"] is None and e["fee_earned"] is None
            assert e["determination_date"] is None
            # Closed but not yet signed off is its own state, and it is the one a
            # consumer most often folds into one of the other two.
            assert e["status"] == ("in_evaluation" if e["end"] < today else "pending")
    assert seen_determined and seen_open and seen_forfeit


def test_the_plans_totals_reconcile_to_its_periods():
    for seed in range(15):
        plan = _plan(_live_cpaf(seed))
        determined = [e for e in plan["evaluations"] if e["status"] == "determined"]
        assert abs(plan["fee_earned"] - sum(e["fee_earned"] for e in determined)) < 0.01
        assert (
            abs(plan["fee_determined"] - sum(e["available_fee"] for e in determined))
            < 0.01
        )
        assert (
            abs(plan["fee_forfeited"] - (plan["fee_determined"] - plan["fee_earned"]))
            < 0.01
        )
        # What the contractor still has a chance to earn.
        assert (
            abs(plan["fee_at_risk"] - (plan["award_fee_pool"] - plan["fee_determined"]))
            < 0.01
        )
        assert plan["periods_determined"] == len(determined)
        assert plan["periods_total"] == len(plan["evaluations"])
        assert (
            abs(plan["total_fee"] - (plan["base_fee"] + plan["award_fee_pool"])) < 0.02
        )


def test_the_rating_table_earns_nothing_below_the_unsatisfactory_threshold():
    assert ct.award_fee_rating(97) == ("Excellent", 0.97)
    assert ct.award_fee_rating(76)[0] == "Very Good"
    assert ct.award_fee_rating(61)[0] == "Good"
    assert ct.award_fee_rating(55) == ("Satisfactory", 0.55)
    # The part that makes award fee a real risk rather than a discount schedule.
    assert ct.award_fee_rating(50) == ("Unsatisfactory", 0.0)
    assert ct.award_fee_rating(0)[1] == 0.0


def test_cpif_brackets_are_ordered_and_reachable_under_the_share_ratio():
    for seed in range(20):
        c = _award(seed, contract_type="CPIF")
        ratio = tuple(c["pricing"]["share_ratio"])
        assert ratio in ((80, 20), (85, 15))
        for clin in _labor_clins(c):
            assert clin["min_fee"] < clin["target_fee"] < clin["max_fee"]
            assert tuple(clin["share_ratio"]) == ratio
            # The brackets are where the share-ratio adjustment stops, not free
            # figures: the contractor's share of the cost swing, either way.
            adjustment = clin["target_cost"] * clin["incentive_swing"] * ratio[1] / 100
            assert abs(clin["max_fee"] - clin["target_fee"] - adjustment) < 0.02
            assert abs(clin["target_fee"] - clin["min_fee"] - adjustment) < 0.02
            # Maximum fee is reached by underrunning, so it sits at the LOW cost.
            assert (
                clin["max_fee_at_cost"] < clin["target_cost"] < clin["min_fee_at_cost"]
            )


def test_fpi_profit_adjusts_by_a_share_ratio_under_its_price_ceiling():
    for seed in range(20):
        for clin in _labor_clins(_award(seed, contract_type="FPI")):
            assert tuple(clin["share_ratio"]) in ((80, 20), (85, 15))
            assert clin["min_profit"] < clin["target_profit"] < clin["max_profit"]
            assert clin["ceiling_price"] > clin["target_price"]


def test_obligation_is_allocated_against_cost_and_fee():
    """The subtle one. Funding obligates against the CLIN's ceiling, and a cost-type
    ceiling *is* cost + fee — so a fully funded CLIN is obligated for more than its
    estimated cost. Obligating cost alone would leave every funded-position figure
    downstream short by the fee."""
    for ctype in _COST_PLUS:
        for seed in range(10):
            c = _award(seed, contract_type=ctype, funding="full", pop_in_progress=True)
            funded_any = False
            for p in c["periods"]:
                for clin in p["clins"]:
                    if not p["exercised"] or not clin.get("is_labor"):
                        continue
                    funded_any = True
                    cost = clin.get("estimated_cost") or clin.get("target_cost")
                    assert abs(clin["funded"] - clin["ceiling"]) < 0.01
                    assert clin["funded"] > cost
                    assert abs(clin["ceiling"] - (cost + clin["fee"])) < 0.01
            assert funded_any
            assert c["total_obligated"] <= c["total_ceiling"] + 0.01


def test_incremental_funding_still_obligates_within_the_cost_plus_fee_ceiling():
    for ctype in _COST_PLUS:
        for seed in range(10):
            c = _award(seed, contract_type=ctype, funding="incremental")
            for p in c["periods"]:
                for clin in p["clins"]:
                    assert clin["funded"] <= clin["ceiling"] + 0.01


def test_the_fee_clause_follows_the_type():
    expected = {
        "CPFF": ["FAR 52.216-7", "FAR 52.216-8"],
        "CPIF": ["FAR 52.216-7", "FAR 52.216-10"],
        # There is no FAR fee clause for award fee: the arrangement is the plan the
        # contract incorporates, under the FAR 16.401(e) criteria.
        "CPAF": ["FAR 52.216-7"],
        "FPI": ["FAR 52.216-16"],
        # Profit is inside the negotiated rate, so there is no fee clause at all.
        "T&M": [],
        "FFP": [],
    }
    for ctype, cites in expected.items():
        c = _award(6, contract_type=ctype)
        assert [x[0] for x in c["pricing"]["fee_clauses"]] == cites, ctype
    # A vehicle's clauses resolve through the pricing of the order, like its
    # funding clause does.
    idiq = _award(6, contract_type="IDIQ")
    priced_as = idiq["pricing"]["priced_as"]
    assert [x[0] for x in idiq["pricing"]["fee_clauses"]] == expected[priced_as]


def test_the_schedule_prints_the_fee_structure_and_its_clauses():
    cpff = _sheet_text(_award(11, contract_type="CPFF", pop_in_progress=True))
    assert "Fixed Fee" in cpff
    assert "FAR 52.216-8, Fixed Fee" in cpff

    cpaf = _sheet_text(_live_cpaf(11))
    # Two elements, priced separately: what is guaranteed and what is at risk.
    assert "Base Fee" in cpaf and "Award Fee Pool" in cpaf
    assert "Award Fee Plan (Attachment J-1)" in cpaf
    assert "not available for award" in cpaf

    cpif = _sheet_text(_award(11, contract_type="CPIF", pop_in_progress=True))
    assert "Minimum Fee (cost overrun)" in cpif
    assert "Maximum Fee (cost underrun)" in cpif
    assert "FAR 52.216-10, Incentive Fee" in cpif
    assert "share ratio" in cpif

    # A negotiated-rate award has no fee element, so it states no fee clause.
    tm = _sheet_text(_award(11, contract_type="T&M", pop_in_progress=True))
    assert "52.216-8" not in tm and "FEE AND PAYMENT CLAUSES" not in tm


def test_the_award_fee_plan_document_states_earned_forfeited_and_at_risk():
    opts = {"pop_in_progress": True, "active_period": 2}
    records = generate_preset("govcon_award_fee_plan", rows=1, seed=7, opts=opts)
    record = records[0]
    plan = _plan(_live_cpaf(7))
    assert record["contract_no"]
    assert len(record["evaluations"]) == len(plan["evaluations"])
    assert sum(int(x["weight"].rstrip("%")) for x in record["criteria"]) == 100

    fields = PdfReader(
        io.BytesIO(render_fillable(records, PRESETS["govcon_award_fee_plan"]["blocks"]))
    ).get_fields()
    for key in ("fee_earned", "fee_forfeited", "fee_at_risk"):
        assert fields[f"r0_{key}"]["/V"] == record[key]
    # Every evaluation period is a row on the document, determination and all.
    assert fields["r0_evaluations_0_status"]["/V"] == record["evaluations"][0]["status"]


def test_the_plan_preset_generates_a_cpaf_award_whatever_type_is_pinned():
    """An award-fee plan attached to a firm-fixed-price award is not a document
    that exists, so the preset pins the type it documents."""
    record = generate_preset(
        "govcon_award_fee_plan", rows=1, seed=7, opts={"contract_type": "FFP"}
    )[0]
    assert record["contract_type"].startswith("CPAF")
    assert record["evaluations"]
    assert "contract_type" not in dict(
        (k, k) for k in PRESETS["govcon_award_fee_plan"].get("options", [])
    )


def test_pinning_a_type_does_not_shift_the_seeded_stream():
    """Every type reads its structural figures off the same two rolls, so the draw
    count is identical whichever type is pinned. This is what lets the epic move
    seeds once, at the end, instead of per ticket."""
    tails = set()
    for ctype in ct.KNOWN_TYPES:
        rng = random.Random(4)
        ct.build_pricing(rng, ctype, "CPFF", dod=True)
        tails.add(rng.random())
    assert len(tails) == 1


def test_the_award_fee_plan_is_seed_locked():
    """Same seed => same PIID => same plan. The plan draws from a substream of its
    own (derived from the PIID) precisely so that generating one does not move the
    stream every other figure comes from."""
    first, second = _plan(_live_cpaf(9)), _plan(_live_cpaf(9))
    assert first == second
    assert _plan(_live_cpaf(10)) != first
