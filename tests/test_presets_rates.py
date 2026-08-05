"""The indirect rate agreement (#59) — provisional rates, and the final pair."""

import random

from testgen import indirects, presets

from faker import Faker


def _contract(seed=11, **opts):
    rng = random.Random(seed)
    faker = Faker()
    faker.seed_instance(seed)
    return presets.build_contract(rng, faker, 0, opts)


def _agreements(c):
    return c["contractor"].get("rate_agreements")


def test_absent_unless_asked_for():
    assert _agreements(_contract()) is None


def test_knob_does_not_move_the_seeded_stream():
    """The whole point of the PIID-derived substream: turning the knob on adds a
    document and changes nothing else."""
    off = _contract()
    on = _contract(rate_agreement="pair")
    off_rates = off["contractor"].pop("rate_agreements", None)
    on.get("contractor", {}).pop("rate_agreements", None)
    assert off_rates is None
    assert off == on


def test_letter_states_pools_bases_year_and_status():
    letter = _agreements(_contract(rate_agreement="provisional"))[0]
    assert letter["status"] == "provisional"
    assert letter["far_authority"] == "FAR 42.704"
    assert letter["determination_date"] is None
    assert letter["cognisant_agency"] in ("DCMA", "DCAA")
    assert [p["base_label"] for p in letter["pools"]] == [
        "Direct Labor",
        "Direct Labor + Fringe",
        "Total Cost Input",
    ]
    assert all(0.0 < p["rate"] < 1.0 for p in letter["pools"])


def test_rates_are_the_awards_own_rates_not_a_second_draw():
    c = _contract(rate_agreement="pair")
    letter = _agreements(c)[0]
    company = c["contractor"]["indirect_rates"]
    for key in ("fringe", "overhead", "g_and_a"):
        assert letter["rates"][key] == company[key]


def test_pair_is_two_sets_for_one_fiscal_year():
    agreements = _agreements(_contract(rate_agreement="pair"))
    provisional, final = agreements[0], agreements[1]
    assert provisional["fiscal_year"] == final["fiscal_year"]
    assert (provisional["status"], final["status"]) == ("provisional", "final")
    assert final["far_authority"] == "FAR 42.705"
    assert final["determination_date"] > final["fy_end"]
    assert final["rates"] != provisional["rates"]


def test_pool_volatility_ordering_holds():
    """Overhead moves most, G&A less, fringe least — across every seed, because
    the bands are ordered rather than drawn independently."""
    for seed in range(30):
        agreements = _agreements(_contract(seed, rate_agreement="pair"))
        rows = {
            v["key"]: abs(v["delta"])
            for v in indirects.rate_variance(agreements[0], agreements[1])
        }
        assert rows["overhead"] > rows["g_and_a"] > rows["fringe"]


def test_a_set_per_fiscal_year_with_drift():
    agreements = _agreements(_contract(rate_agreement="provisional", option_years=3))
    years = [a["fiscal_year"] for a in agreements]
    assert years == sorted(set(years)) and len(years) > 1
    # Pricing a charge either side of a fiscal year boundary has to give a
    # different answer, or the per-year sets are decoration.
    assert len({a["wrap_rate"] for a in agreements}) == len(agreements)


def test_final_mode_gives_determined_rates_only():
    agreements = _agreements(_contract(rate_agreement="final"))
    assert {a["status"] for a in agreements} == {"final"}
    assert all(a["determination_date"] is not None for a in agreements)


def test_mode_choice_does_not_shift_the_substream():
    """Draws inside the substream are unconditional: the mode selects from what
    was drawn, so a provisional letter is the same letter either way."""
    solo = _agreements(_contract(rate_agreement="provisional"))[0]
    paired = _agreements(_contract(rate_agreement="pair"))[0]
    assert solo == paired


def test_fiscal_year_boundary():
    import datetime

    assert indirects.fiscal_year(datetime.date(2026, 9, 30)) == 2026
    assert indirects.fiscal_year(datetime.date(2026, 10, 1)) == 2027


def test_document_renders_the_pair_and_the_variance():
    rows = presets.generate_preset("govcon_rate_agreement", rows=1, seed=7)
    r = rows[0]
    assert r["final_pools"] and len(r["variance"]) == 3
    assert r["determination_label"].startswith("Final Rate Proposal Due")
    assert all(v["delta"].startswith(("+", "-")) for v in r["variance"])
    title, blocks = presets.rate_agreement_blocks(r)
    assert "42.704" in title
    # One break, and the terms after it.
    assert [b["type"] for b in blocks].count("pagebreak") == 1
