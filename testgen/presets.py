"""GovCon document presets: whole-record builders + real-form mappings.

The generic engine (core.py / fields.py) generates each field independently.
That is perfect for flat datasets, but a real federal contract has to satisfy
cross-field invariants a sharp reviewer checks by adding numbers up:

    1. sum(clin.ceiling for a period) == period.ceiling
    2. sum(period.ceiling) == total_ceiling
    3. total_obligated <= total_ceiling (and usually strictly less)
    4. sum(rate * hours) on a labor CLIN == that CLIN's ceiling
    5. option-period CLINs start with the period digit (0xxx, 1xxx, 2xxx ...)
    6. periods of performance are contiguous and non-overlapping
    7. loaded rate ~= base_salary / 2080 * wrap(2.0..2.45)

We honor them by building BOTTOM-UP: labor lines roll up into a labor CLIN
ceiling, CLINs roll up into a period ceiling, periods roll up into the total
ceiling. Because every parent total is defined as the sum of its children, the
invariants hold by construction rather than by after-the-fact reconciliation.

A preset is one entry in PRESETS: a builder that produces one consistent record
per row, plus (where an official form exists) a mapping from that record to the
real AcroForm field names so formfill.py can fill the genuine SF-1449 / SF-30.

Source of the rules: sample-data/NOTES-for-testgen.md (real SF-26 awards +
2026 GSA wrap-rate data), distilled by a companion research pass.
"""

from __future__ import annotations

import datetime
import string

from faker import Faker

# The AcroForm field names on the real forms are all nested under this prefix.
_P = "topmostSubform[0].Page1[0]."


# --- Reference data: labor categories with 2026 fully-burdened bands ---------
# band  = realistic loaded (fully-burdened) $/hr BEFORE any clearance premium.
# edu / yrs / clr = the LCAT's minimum qualification floor (the compliance
# feature downstream cross-checks a resume against these).
_LCATS = [
    {
        "lcat": "Administrative Support",
        "band": (45, 75),
        "edu": "HS Diploma",
        "yrs": 1,
        "clr": None,
    },
    {
        "lcat": "Business Analyst",
        "band": (100, 150),
        "edu": "Bachelor's",
        "yrs": 3,
        "clr": "Secret",
    },
    {
        "lcat": "Systems Engineer",
        "band": (90, 135),
        "edu": "Bachelor's",
        "yrs": 3,
        "clr": "Secret",
    },
    {
        "lcat": "Software Engineer (Mid)",
        "band": (110, 165),
        "edu": "Bachelor's",
        "yrs": 5,
        "clr": "Secret",
    },
    {
        "lcat": "Program Manager (PMP)",
        "band": (130, 190),
        "edu": "Bachelor's",
        "yrs": 8,
        "clr": "Secret",
    },
    {
        "lcat": "Senior Software Engineer",
        "band": (155, 220),
        "edu": "Bachelor's",
        "yrs": 8,
        "clr": "TS/SCI",
    },
    {
        "lcat": "Senior Cyber SME",
        "band": (180, 290),
        "edu": "Master's",
        "yrs": 10,
        "clr": "TS/SCI",
    },
]

_CLEARANCE_PREMIUM = {None: (0, 0), "Secret": (8, 12), "TS/SCI": (20, 30)}

# Agencies and how each one's PIID (contract number, block 2) is shaped. The
# {fy} slot is filled with the 2-digit fiscal year; ?=letter, #=digit via
# Faker's bothify (seeded, so reproducible).
_AGENCIES = [
    {
        "name": "Department of Homeland Security",
        "office": "DHS OPO",
        "piid": "70{fy}?????C000####",
    },
    {
        "name": "General Services Administration",
        "office": "GSA FAS",
        "piid": "GS-##F-####?",
    },
    {
        "name": "Department of the Army",
        "office": "ACC-APG",
        "piid": "W#####-{fy}-C-####",
    },
    {
        "name": "Department of the Navy",
        "office": "NAVSEA",
        "piid": "N#####-{fy}-C-####",
    },
    {
        "name": "Department of Veterans Affairs",
        "office": "VA TAC",
        "piid": "36C{fy}##D####",
    },
]

# Standard boilerplate deliverables (CDRLs) on a services contract.
_CDRLS = [
    "A001 - Monthly Status Report",
    "A002 - Monthly Financial / Burn Report",
    "A003 - Quarterly Progress Review",
]


# --- Small helpers ------------------------------------------------------------


def _alnum(rng, n):
    """An n-char uppercase alphanumeric token (CAGE style)."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(n))


# A real UEI (Unique Entity Identifier) is exactly 12 chars and excludes the
# letters I and O (to avoid confusion with the digits 1 and 0). It also never
# starts with a 0. We honor those rules so the id passes a validator.
_UEI_ALPHABET = (
    "".join(c for c in string.ascii_uppercase if c not in "IO") + string.digits
)


def _uei(rng):
    first = rng.choice([c for c in _UEI_ALPHABET if c != "0"])
    rest = "".join(rng.choice(_UEI_ALPHABET) for _ in range(11))
    return first + rest


def _round_money(value):
    return round(float(value), 2)


def _round_to(value, step):
    """Round to the nearest `step` dollars (keeps travel/ODC ceilings tidy)."""
    return float(int(round(value / step)) * step)


def _period_digit(period_index):
    """Base year -> 0, option year 1 -> 1, ... (the leading CLIN digit)."""
    return str(period_index)


def _piid(rng, faker, agency, effective_date):
    fy = f"{effective_date.year % 100:02d}"
    pattern = agency["piid"].replace("{fy}", fy)
    return faker.bothify(text=pattern, letters=string.ascii_uppercase)


# --- The contract builder -----------------------------------------------------


def build_contract(rng, faker, index, opts=None):
    """Build one internally-consistent contract record (see module docstring).

    opts (all optional): agency (name), option_years (0..4), lcat_lines (per
    labor CLIN). Anything not given is chosen randomly within realistic bounds.
    """
    opts = opts or {}

    # Who and what.
    agency = _pick_agency(rng, opts.get("agency"))
    effective = _effective_date(rng)
    piid = _piid(rng, faker, agency, effective)
    contractor = {
        "name": faker.company(),
        "uei": _uei(rng),
        "cage": _alnum(rng, 5),
        "address": faker.address().replace("\n", ", "),
    }
    contract_type = rng.choice(["T&M", "CPFF", "FFP", "IDIQ"])
    labor_type = "FFP" if contract_type == "FFP" else rng.choice(["T&M", "CPFF"])

    # Periods: 1 base year + N option years, contiguous (invariant 6).
    option_years = int(opts.get("option_years", rng.randint(1, 4)))
    periods = _build_periods(rng, faker, effective, option_years, labor_type, opts)

    # Roll periods up into the total ceiling (invariant 2).
    total_ceiling = _round_money(sum(p["ceiling"] for p in periods))

    # Obligation: only exercised periods can be funded, and usually not fully
    # (incremental funding). total_obligated <= total_ceiling, strictly less
    # here (invariant 3). Build a mod history that sums to it.
    exercised_ceiling = _round_money(
        sum(p["ceiling"] for p in periods if p["exercised"])
    )
    obligated_fraction = rng.uniform(0.55, 0.9)
    total_obligated = _round_money(exercised_ceiling * obligated_fraction)
    obligation_history = _build_obligations(rng, effective, total_obligated, periods)

    return {
        "piid": piid,
        "solicitation_no": faker.bothify(
            text=agency["piid"].split("{fy}")[0] + "####R"
        ),
        "effective_date": effective,
        "agency": agency["name"],
        "issuing_office": agency["office"],
        "contractor": contractor,
        "contracting_officer": faker.name(),
        "co_phone": faker.numerify("(###) ###-####"),
        # A finalized, accepted contract (the lifecycle stage Runway manages):
        # a government acceptance representative and an acceptance date shortly
        # after award, never in the future.
        "gov_representative": faker.name(),
        "gov_rep_address": faker.address().replace("\n", ", "),
        "gov_rep_phone": faker.numerify("(###) ###-####"),
        "signer_title": rng.choice(
            ["President", "Vice President", "CEO", "Contracts Manager", "COO"]
        ),
        "acceptance_date": min(
            effective + datetime.timedelta(days=rng.randint(20, 75)),
            datetime.date.today(),
        ),
        "contract_type": contract_type,
        "total_ceiling": total_ceiling,
        "total_obligated": total_obligated,
        "unfunded_balance": _round_money(total_ceiling - total_obligated),
        "obligation_history": obligation_history,
        "periods": periods,
        "cdrls": list(_CDRLS),
    }


def _pick_agency(rng, name):
    if name:
        for a in _AGENCIES:
            if a["name"] == name:
                return a
    return rng.choice(_AGENCIES)


def _effective_date(rng):
    """A recent award date (deterministic from the seeded rng)."""
    start = datetime.date(2024, 1, 1)
    end = datetime.date(2026, 6, 30)
    return start + datetime.timedelta(days=rng.randint(0, (end - start).days))


def _build_periods(rng, faker, effective, option_years, labor_type, opts):
    """A contiguous list of 12-month periods (base + options). Each period's
    ceiling is the sum of its CLINs (invariant 1); options after the first
    un-exercised one stay un-exercised (you cannot skip an option year)."""
    periods = []
    start = effective
    still_exercised = True
    for pi in range(option_years + 1):
        end = _add_year(start)
        if pi == 0:
            exercised = True  # base year is always exercised
        else:
            # Earlier options are more likely to be exercised; once one lapses,
            # the rest cannot be exercised either.
            still_exercised = still_exercised and (rng.random() < 0.7)
            exercised = still_exercised

        clins = _build_clins(rng, faker, pi, labor_type, opts)
        ceiling = _round_money(sum(c["ceiling"] for c in clins))
        periods.append(
            {
                "name": "Base Year" if pi == 0 else f"Option Year {pi}",
                "pop_start": start,
                "pop_end": end - datetime.timedelta(days=1),
                "exercised": exercised,
                "ceiling": ceiling,
                "clins": clins,
            }
        )
        start = end  # next period starts the day the last one ends (contiguous)
    return periods


# Task-area names used when a period is split across more than one labor CLIN.
_TASK_AREAS = [
    "Engineering Services",
    "Program Management",
    "Cybersecurity Support",
    "Systems Integration",
    "Operations & Maintenance",
    "Data & Analytics Support",
]


def _build_clins(rng, faker, period_index, labor_type, opts):
    """The CLINs for one period, randomized but realistic: one to three labor
    CLINs (a period is sometimes split across task areas) plus the usual — but
    not guaranteed — cost-reimbursable travel and ODC lines. Each labor CLIN's
    ceiling is built from its labor lines so it reconciles (invariant 4); CLINs
    are numbered sequentially with the period digit (invariant 5)."""
    digit = _period_digit(period_index)
    clins = []

    def _next_clin():
        return f"{digit}{len(clins) + 1:03d}"

    # 1-3 labor CLINs (usually 1). Multiple ones are split into task areas.
    n_labor = rng.choices([1, 2, 3], weights=[6, 3, 1])[0]
    areas = rng.sample(_TASK_AREAS, n_labor) if n_labor > 1 else [None]
    labor_total = 0.0
    for area in areas:
        lines = _build_labor_lines(rng, opts.get("lcat_lines"))
        ceiling = _round_money(sum(l["amount"] for l in lines))
        labor_total += ceiling
        title = (
            f"Professional Services - {area}"
            if area
            else "Professional Services (Labor)"
        )
        clins.append(
            {
                "clin": _next_clin(),
                "period_index": period_index,
                "title": title,
                "type": labor_type,
                "is_labor": True,
                "ceiling": ceiling,
                "est_hours": sum(l["est_hours"] for l in lines),
                "labor_rates": lines,
            }
        )

    # Cost-reimbursable support CLINs — common, but not on every contract.
    if rng.random() < 0.85:
        clins.append(
            _cost_clin(
                _next_clin(),
                period_index,
                "Travel (Cost-Reimbursable, No Fee)",
                _round_to(labor_total * rng.uniform(0.02, 0.06), 1000),
            )
        )
    if rng.random() < 0.75:
        clins.append(
            _cost_clin(
                _next_clin(),
                period_index,
                "Other Direct Costs / Materials (Cost, No Fee)",
                _round_to(labor_total * rng.uniform(0.01, 0.05), 1000),
            )
        )
    return clins


def _cost_clin(number, period_index, title, ceiling):
    """A cost-reimbursable, no-fee CLIN (travel / ODC) — no labor lines."""
    return {
        "clin": number,
        "period_index": period_index,
        "title": title,
        "type": "COST",
        "is_labor": False,
        "ceiling": ceiling,
        "labor_rates": [],
    }


def _build_labor_lines(rng, n_lines):
    """A set of labor lines. Each line derives a loaded rate from a base salary
    and a wrap rate (invariant 7), keeps it inside the LCAT's realistic band,
    and books whole-FTE hours (a clean multiple of 2080, invariant 7's partner
    #7 hours rule). The line amount is rate * hours, so the CLIN ceiling that
    sums them reconciles exactly (invariant 4)."""
    n = int(n_lines) if n_lines else rng.randint(2, 4)
    picks = rng.sample(_LCATS, min(n, len(_LCATS)))
    lines = []
    for spec in picks:
        wrap = round(rng.uniform(2.0, 2.45), 2)
        lo, hi = spec["band"]
        base_loaded = round(rng.uniform(lo, hi), 2)  # loaded rate before clearance
        prem_lo, prem_hi = _CLEARANCE_PREMIUM[spec["clr"]]
        premium = round(rng.uniform(prem_lo, prem_hi), 2) if prem_hi else 0.0
        loaded_rate = round(base_loaded + premium, 2)
        # Back-derive the base salary that yields base_loaded at this wrap, so
        # loaded ~= base_salary/2080 * wrap holds (invariant 7).
        base_salary = round(base_loaded / wrap * 2080)
        fte = rng.randint(1, 6)
        est_hours = fte * 2080
        lines.append(
            {
                "lcat": spec["lcat"],
                "clearance": spec["clr"] or "None",
                "min_education": spec["edu"],
                "min_experience_yrs": spec["yrs"],
                "base_salary": base_salary,
                "wrap_rate": wrap,
                "loaded_rate": loaded_rate,
                "est_hours": est_hours,
                "amount": _round_money(loaded_rate * est_hours),
            }
        )
    return lines


def _build_obligations(rng, effective, total_obligated, periods):
    """A mod history (award + P00001, P00002 ...) whose amounts sum EXACTLY to
    total_obligated, with a running cumulative. Mods are dated within the
    exercised periods and after the award."""
    exercised = [p for p in periods if p["exercised"]]
    # One funding action per exercised period, plus 0-2 incremental-funding mods.
    n_actions = max(1, len(exercised) + rng.randint(0, 2))

    # Split total_obligated into n_actions positive increments. Random weights,
    # last increment absorbs the rounding so the sum is exact.
    weights = [rng.uniform(0.5, 1.5) for _ in range(n_actions)]
    wsum = sum(weights)
    increments = [_round_to(total_obligated * w / wsum, 1000) for w in weights[:-1]]
    increments.append(_round_money(total_obligated - sum(increments)))

    # Mods happen over the life of the contract, but a *test* document should
    # not be dated in the future. Cap every action at today so no mod lands
    # after the current date.
    today = datetime.date.today()
    history = []
    cumulative = 0.0
    date = effective
    for i, amount in enumerate(increments):
        cumulative = _round_money(cumulative + amount)
        if i == 0:
            mod, action = "Award", "Initial award / base-period funding"
        else:
            mod = f"P{i:05d}"
            action = rng.choice(
                [
                    "Incremental funding (FAR 52.232-22)",
                    "Exercise option period",
                    "Administrative modification",
                ]
            )
        history.append(
            {
                "mod": mod,
                "date": min(date, today),
                "action": action,
                "amount": amount,
                "cumulative_obligated": cumulative,
            }
        )
        date = min(date + datetime.timedelta(days=rng.randint(60, 180)), today)
    return history


def _add_year(d):
    """One year after d, handling Feb 29 by falling back a day."""
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=d.day - 1)


# --- Mappings: a generated contract -> real form fields -----------------------


def _gov_email(name):
    """A plausible .mil-style government address from a rep's name."""
    parts = [p for p in name.replace(".", "").split() if p.isalpha()]
    handle = (parts[0][0] + parts[-1]).lower() if len(parts) >= 2 else name.lower()
    return f"{handle}@mail.mil"


def _fmt_money(value):
    return f"${value:,.2f}"


def _fmt_date(d):
    return d.strftime("%Y-%m-%d") if isinstance(d, datetime.date) else str(d)


def contract_to_sf1449(contract):
    """Map a contract onto the real SF-1449 (commercial award). The base-year
    CLINs go into the line-item grid (rows 1-8); header blocks carry the PIID,
    solicitation, agency and contractor."""
    c = contract
    contractor = c["contractor"]
    values = {
        _P + "reqnumber[0]": c["solicitation_no"],
        _P + "contractno[0]": c["piid"],
        _P + "solicitationnumber[0]": c["solicitation_no"],
        _P + "issuedbycode[0]": c["issuing_office"],
        _P + "AdministeredBy[0]": c["agency"],
        # 17a code box is narrow; the 5-char CAGE fits, full UEI/CAGE go in 17a's
        # address block below it.
        _P + "contractorcode[0]": contractor["cage"],
        _P + "contractoraddress[0]": f"{contractor['name']}\n{contractor['address']}\n"
        f"UEI {contractor['uei']}  CAGE {contractor['cage']}",
        _P + "paymentbyaddress[0]": c["issuing_office"],
        _P + "pagenumber[0]": "1",
        # Dates, contact, accounting, and the total-award block.
        _P + "AWARDDate[0]": _fmt_date(c["effective_date"]),
        _P + "contactname[0]": c["contracting_officer"],
        _P + "contactphone[0]": c["co_phone"],
        _P + "DeliverTo[0]": c["issuing_office"],
        _P
        + "accountingdata[0]": (
            f"Appropriation FY{c['effective_date'].year % 100:02d}; "
            f"Obligated to date {_fmt_money(c['total_obligated'])} of "
            f"{_fmt_money(c['total_ceiling'])} ceiling."
        ),
        # Block 26 total award = the awarded (base-year) value.
        _P
        + "TOTALAWARD[0]": _fmt_money(
            c["periods"][0]["ceiling"] if c["periods"] else 0.0
        ),
        # --- Finalized award: both parties signed (blocks 30-31). ---
        _P + "signername[0]": f"{contractor['name']} / Authorized Signatory",
        _P + "signertitle[0]": c["signer_title"],
        _P + "offerreference[0]": c["solicitation_no"],
        _P + "Date[3]": _fmt_date(c["effective_date"]),  # contractor signed
        _P + "Date[4]": _fmt_date(c["effective_date"]),  # CO signed / awarded
        # --- Page 2: government acceptance (this is a delivered, accepted
        # contract record). Finance-processing blocks (voucher, amount verified,
        # check number, certifying officer) are intentionally left blank; those
        # belong to a payment record, not an award. ---
        "topmostSubform[0].Page2[0].authorizedname[0]": c["gov_representative"],
        "topmostSubform[0].Page2[0].authorizedtitle[0]": "Contracting Officer's Representative (COR)",
        "topmostSubform[0].Page2[0].authorizedaddress[0]": c["gov_rep_address"],
        "topmostSubform[0].Page2[0].authorizedphone[0]": c["gov_rep_phone"],
        "topmostSubform[0].Page2[0].authorizedemail[0]": _gov_email(
            c["gov_representative"]
        ),
        "topmostSubform[0].Page2[0].INSPECTED[0]": "/1",
        "topmostSubform[0].Page2[0].receivedby[0]": c["gov_representative"],
        "topmostSubform[0].Page2[0].receivedatlocation[0]": c["issuing_office"],
        "topmostSubform[0].Page2[0].RECEIVED[0]": "/1",
        "topmostSubform[0].Page2[0].ACCEPTED[0]": "/1",
        "topmostSubform[0].Page2[0].COMPLETE[0]": "/1",
        "topmostSubform[0].Page2[0].CDATE[0]": _fmt_date(c["acceptance_date"]),
        "topmostSubform[0].Page2[0].DateCDATE[0]": _fmt_date(c["acceptance_date"]),
        "topmostSubform[0].Page2[0].Date[0]": _fmt_date(c["acceptance_date"]),
    }

    # Base-year CLINs into the numbered line-item grid.
    base = c["periods"][0]["clins"] if c["periods"] else []
    for i, clin in enumerate(base[:8], start=1):
        values[_P + f"ITEMNUM{i}[0]"] = clin["clin"]
        values[_P + f"schedule{i}[0]"] = f"{clin['title']} ({clin['type']})"
        values[_P + f"quantity{i}[0]"] = "1"
        values[_P + f"unit{i}[0]"] = "LO"
        values[_P + f"unitprice{i}[0]"] = _fmt_money(clin["ceiling"])
        values[_P + f"amount{i}[0]"] = _fmt_money(clin["ceiling"])
    return values


def contract_to_sf30(contract):
    """Map a contract's latest funding modification onto the real SF-30. The
    SF-30 documents a change to an existing contract, so we render the most
    recent mod from the obligation history (falling back to the award)."""
    c = contract
    contractor = c["contractor"]
    history = c["obligation_history"]
    mod = next((m for m in reversed(history) if m["mod"] != "Award"), history[-1])
    prev_cumulative = _round_money(mod["cumulative_obligated"] - mod["amount"])

    description = (
        "The purpose of this modification is to obligate incremental funding. "
        "Accordingly: (a) Total funds obligated on this contract are increased "
        f"by {_fmt_money(mod['amount'])}, from {_fmt_money(prev_cumulative)} to "
        f"{_fmt_money(mod['cumulative_obligated'])}. (b) The total contract "
        f"ceiling remains {_fmt_money(c['total_ceiling'])}. (c) All other terms "
        "and conditions remain unchanged and in full force and effect."
    )
    accounting = (
        f"Appropriation FY{c['effective_date'].year % 100:02d}; "
        f"Obligated this action {_fmt_money(mod['amount'])}."
    )

    # Block 1 "Contract ID Code" is a single award-instrument letter, not the
    # contract type name; map the type to a plausible code.
    id_code = {"IDIQ": "D", "FFP": "C", "T&M": "C", "CPFF": "C"}.get(
        c["contract_type"], "C"
    )
    return {
        # Block 2 = the amendment/modification number (this action).
        _P + "AmendmentNo[0]": mod["mod"],
        # Block 10A = the CONTRACT/ORDER number being modified (the PIID).
        _P + "ModificationNo[0]": c["piid"],
        _P + "EffectiveDate[0]": _fmt_date(mod["date"]),
        _P + "ContractIDCode[0]": id_code,
        _P + "ReqNumber[0]": c["solicitation_no"],
        _P + "IssuedBy[0]": f"{c['agency']}\n{c['issuing_office']}",
        _P + "AdministeredBy[0]": c["issuing_office"],
        _P + "NameandAddress[0]": f"{contractor['name']}\n{contractor['address']}\n"
        f"UEI {contractor['uei']}  CAGE {contractor['cage']}",
        _P + "AccountingData[0]": accounting,
        _P + "Description[0]": description,
        # Item 13: an incremental-funding action is a unilateral change order
        # issued pursuant to a clause authority (13A), so the contractor is NOT
        # required to sign (item 16). Solicitation-amendment boxes (item 11)
        # stay off — this modifies a contract, not a solicitation.
        _P + "CheckBox13A[0]": "/1",
        _P + "A13[0]": "FAR 52.232-22, Limitation of Funds",
        _P + "IsNot[0]": "/1",
        _P
        + "NameandTitleSigner[0]": f"{contractor['name']} (Authorized Representative)",
        _P
        + "NameandTitleOfficer[0]": f"{c['contracting_officer']}, Contracting Officer",
        _P + "Page[0]": "1",
        _P + "Pages[0]": "1",
    }


# --- Drawn-document presets (no official form exists) -------------------------
# These build a contract, then flatten it into the flat, display-ready row that
# the drawn PDF writer (pdf.py) renders. Money/dates are pre-formatted as strings
# so they read well on the page. Each preset pairs a builder with a `fields`
# column order and a `config` describing the drawn layout.


def _funding_summary_row(rng, faker, index, opts=None):
    c = build_contract(rng, faker, index, opts)
    return {
        "contract_no": c["piid"],
        "contractor": c["contractor"]["name"],
        "agency": c["agency"],
        "total_ceiling": _fmt_money(c["total_ceiling"]),
        "total_obligated": _fmt_money(c["total_obligated"]),
        "unfunded_balance": _fmt_money(c["unfunded_balance"]),
        "obligation_history": [
            {
                "mod": m["mod"],
                "date": _fmt_date(m["date"]),
                "action": m["action"],
                "amount": _fmt_money(m["amount"]),
                "cumulative": _fmt_money(m["cumulative_obligated"]),
            }
            for m in c["obligation_history"]
        ],
    }


def _award_letter_row(rng, faker, index, opts=None):
    c = build_contract(rng, faker, index, opts)
    return {
        "contract_no": c["piid"],
        "contractor": c["contractor"]["name"],
        "agency": c["agency"],
        "award_date": _fmt_date(c["effective_date"]),
        "base_value": _fmt_money(c["periods"][0]["ceiling"] if c["periods"] else 0.0),
        "total_ceiling": _fmt_money(c["total_ceiling"]),
        "contracting_officer": c["contracting_officer"],
    }


def _record_sheet_row(rng, faker, index, opts=None):
    c = build_contract(rng, faker, index, opts)
    return {
        "contract_no": c["piid"],
        "contractor": c["contractor"]["name"],
        "uei": c["contractor"]["uei"],
        "cage": c["contractor"]["cage"],
        "agency": c["agency"],
        "contract_type": c["contract_type"],
        "award_date": _fmt_date(c["effective_date"]),
        "total_ceiling": _fmt_money(c["total_ceiling"]),
        "total_obligated": _fmt_money(c["total_obligated"]),
    }


def _invoice_row(rng, faker, index, opts=None):
    c = build_contract(rng, faker, index, opts)
    base = c["periods"][0] if c["periods"] else {"clins": []}
    # Bill a slice of each base-year CLIN this period (a monthly invoice).
    frac = rng.uniform(0.05, 0.15)
    lines, billed = [], 0.0
    for cl in base["clins"]:
        amt = _round_money(cl["ceiling"] * frac)
        billed += amt
        lines.append(
            {
                "clin": cl["clin"],
                "description": cl["title"],
                "amount_billed": _fmt_money(amt),
            }
        )
    inv_date = min(
        c["effective_date"] + datetime.timedelta(days=rng.randint(30, 120)),
        datetime.date.today(),
    )
    return {
        "invoice_no": faker.bothify(text="INV-####-#####"),
        "invoice_date": _fmt_date(inv_date),
        "contract_no": c["piid"],
        "contractor": c["contractor"]["name"],
        "remit_to": c["contractor"]["address"],
        "agency": c["agency"],
        "contracting_officer": c["contracting_officer"],
        "amount_due": _fmt_money(billed),
        "line_items": lines,
    }


def _short_desc(title):
    """A concise line description for the narrow SF-1034 articles column (the
    long parenthetical cost notes make the field auto-shrink its font)."""
    t = title.replace(" (Cost-Reimbursable, No Fee)", "").replace(" (Cost, No Fee)", "")
    t = t.replace("Professional Services - ", "Labor: ")
    t = t.replace("Professional Services (Labor)", "Professional Services (Labor)")
    t = t.replace("Other Direct Costs / Materials", "ODC / Materials")
    return t[:38]


def invoice_to_sf1034(row):
    """Map a generated invoice onto the real SF-1034 Public Voucher. Billed CLINs
    fill the articles/amount grid (with the CLIN as the order number and the
    invoice date as the service date); the total fills the voucher total."""
    values = {
        _P + "VoucherNumber[0]": row["invoice_no"],
        _P + "DateVoucherPrepared[0]": row["invoice_date"],
        _P + "ContractNoandDate[0]": row["contract_no"],
        _P + "USDepartmentLocation[0]": row["agency"],
        _P + "PayeeNameAddress[0]": f"{row['contractor']}\n{row['remit_to']}",
        _P + "TotalAmount[0]": row["amount_due"],
        _P + "AmountVerified[0]": row["amount_due"],
        _P + "Title[0]": f"{row['contracting_officer']}, Contracting Officer",
    }
    for i, li in enumerate(row["line_items"][:9]):
        values[_P + f"ArticlesServices[{i}]"] = _short_desc(li["description"])
        values[_P + f"NumberDateofOrder[{i}]"] = li["clin"]
        values[_P + f"DateofDelivery[{i}]"] = row["invoice_date"]
        values[_P + f"Amount{i + 1}[0]"] = li["amount_billed"]
        values[_P + f"Quantity{i + 1}[0]"] = "1"
    return values


# --- Fillable-document layouts (record -> title + blocks) ---------------------
# Each returns (title, [block]) for fillable.render_fillable, which draws the
# labels and places editable AcroForm fields pre-filled with the values.


def funding_summary_blocks(r):
    return "Funding & Obligation Summary", [
        {
            "type": "pair",
            "fields": [
                ("contract_no", "Contract No.", r["contract_no"]),
                ("agency", "Agency", r["agency"]),
            ],
        },
        {
            "type": "field",
            "name": "contractor",
            "label": "Contractor",
            "value": r["contractor"],
        },
        {
            "type": "pair",
            "fields": [
                ("total_ceiling", "Total Ceiling", r["total_ceiling"]),
                ("total_obligated", "Total Obligated", r["total_obligated"]),
            ],
        },
        {
            "type": "field",
            "name": "unfunded_balance",
            "label": "Unfunded Balance",
            "value": r["unfunded_balance"],
        },
        {
            "type": "table",
            "name": "obligation_history",
            "label": "Obligation History",
            "rows": r["obligation_history"],
            "columns": [
                ("mod", "Mod", 0.13),
                ("date", "Date", 0.16),
                ("action", "Action", 0.39),
                ("amount", "Amount", 0.16),
                ("cumulative", "Cumulative", 0.16),
            ],
        },
    ]


def record_sheet_blocks(r):
    return "Contract Record", [
        {
            "type": "pair",
            "fields": [
                ("contract_no", "Contract No.", r["contract_no"]),
                ("contract_type", "Contract Type", r["contract_type"]),
            ],
        },
        {
            "type": "field",
            "name": "contractor",
            "label": "Contractor",
            "value": r["contractor"],
        },
        {
            "type": "pair",
            "fields": [
                ("uei", "UEI", r["uei"]),
                ("cage", "CAGE Code", r["cage"]),
            ],
        },
        {"type": "field", "name": "agency", "label": "Agency", "value": r["agency"]},
        {
            "type": "pair",
            "fields": [
                ("award_date", "Award Date", r["award_date"]),
                ("total_ceiling", "Total Ceiling", r["total_ceiling"]),
            ],
        },
        {
            "type": "field",
            "name": "total_obligated",
            "label": "Total Obligated",
            "value": r["total_obligated"],
        },
    ]


def _award_letter_text(r):
    """Award-notice body modeled on real federal contract-award / notice-to-
    proceed letters (award reference, incremental-funding limitation, CO
    authority, acknowledgment request)."""
    return (
        f"{r['agency']}\n\n"
        f"SUBJECT: Notice of Contract Award - Contract No. {r['contract_no']}\n\n"
        f"Dear {r['contractor']}:\n\n"
        f"This letter constitutes official notification that {r['agency']} has awarded "
        f"your firm the above-referenced contract, effective {r['award_date']}, for the "
        f"supplies and services described in the resulting schedule. Your proposal has "
        f"been accepted as submitted.\n\n"
        f"The amount obligated for the base period of performance is {r['base_value']}. "
        f"The total potential value of this contract, inclusive of all option periods, "
        f"is {r['total_ceiling']}. This contract is incrementally funded in accordance "
        f"with FAR 52.232-22, Limitation of Funds; the Government is not obligated to "
        f"reimburse the Contractor for costs incurred in excess of the total amount "
        f"obligated, and additional funds will be provided by separate modification.\n\n"
        f"No work shall be performed and no costs incurred prior to the effective date "
        f"shown above. {r['contracting_officer']} is the Contracting Officer for this "
        f"award and is the only individual authorized to modify the terms of this "
        f"contract or direct any change to the scope of work; all such changes must be "
        f"made in writing by the Contracting Officer.\n\n"
        f"Please acknowledge receipt of this award within five (5) business days.\n\n"
        f"Sincerely,\n\n\n{r['contracting_officer']}\nContracting Officer\n{r['agency']}"
    )


def award_letter_blocks(r):
    return "Notice of Contract Award", [
        {
            "type": "pair",
            "fields": [
                ("contract_no", "Contract No.", r["contract_no"]),
                ("award_date", "Award Date", r["award_date"]),
            ],
        },
        {
            "type": "prose",
            "name": "letter_body",
            "label": "Letter",
            "value": _award_letter_text(r),
            "height": 430,
        },
    ]


# --- The preset registry ------------------------------------------------------
# Each preset: a builder (row -> consistent record) and, where an official form
# exists, the form file + a mapping. kind drives how the front end offers it and
# how export renders it: "form" fills a real AcroForm; "data" is a plain dataset.

PRESETS = {
    "govcon_award_sf1449": {
        "label": "Contract Award (SF-1449)",
        "description": "A commercial-items award on the real SF-1449, with the "
        "base-year CLINs filled into the line-item grid. Ceiling, obligated and "
        "line totals all reconcile.",
        "kind": "form",
        "form": "SF1449.pdf",
        "build": build_contract,
        "mapping": contract_to_sf1449,
    },
    "govcon_mod_sf30": {
        "label": "Contract Modification (SF-30)",
        "description": "An incremental-funding modification on the real SF-30, "
        "generated from a contract's obligation history (obligated < ceiling).",
        "kind": "form",
        "form": "SF30.pdf",
        "build": build_contract,
        "mapping": contract_to_sf30,
    },
    "govcon_invoice": {
        "label": "Contractor Invoice (SF-1034)",
        "description": "A contractor invoice on the real SF-1034 Public Voucher, "
        "billing a slice of each CLIN against the awarded contract.",
        "kind": "form",
        "form": "SF1034.pdf",
        "build": _invoice_row,
        "mapping": invoice_to_sf1034,
    },
    "govcon_funding_summary": {
        "label": "Funding & Obligation Summary",
        "description": "A one-page funding snapshot: ceiling, obligated, unfunded "
        "balance, and the full modification history. Editable fields.",
        "kind": "doc",
        "build": _funding_summary_row,
        "blocks": funding_summary_blocks,
    },
    "govcon_award_letter": {
        "label": "Award Notice Letter",
        "description": "A plain-language award-notice letter (modeled on real "
        "federal award letters) — narrative text for ingestion to parse. Editable.",
        "kind": "doc",
        "build": _award_letter_row,
        "blocks": award_letter_blocks,
    },
    "govcon_record_sheet": {
        "label": "Simple Contract Record",
        "description": "A compact one-page record sheet of the key contract fields "
        "— clean input for a basic extraction test. Editable fields.",
        "kind": "doc",
        "build": _record_sheet_row,
        "blocks": record_sheet_blocks,
    },
    "govcon_contract_data": {
        "label": "Contract Dataset (nested)",
        "description": "The full generated contract as structured data — periods, "
        "CLINs, labor rates, and obligation history.",
        "kind": "data",
        "build": build_contract,
    },
}


def list_presets():
    """Preset metadata for the front-end gallery (no builder functions)."""
    return [
        {
            "key": key,
            "label": p["label"],
            "description": p["description"],
            "kind": p["kind"],
            "form": p.get("form"),
        }
        for key, p in PRESETS.items()
    ]


def generate_preset(key, *, rows=5, seed=None, opts=None):
    """Generate `rows` consistent records for a preset, reproducibly.

    Mirrors core.generate's seeding so the same seed + preset always yields the
    same records. Returns a list of record dicts (the builder's output)."""
    if key not in PRESETS:
        valid = ", ".join(sorted(PRESETS))
        raise ValueError(f"Unknown preset '{key}'. Available: {valid}.")

    import random

    rng = random.Random(seed)
    faker = Faker()
    if seed is not None:
        faker.seed_instance(seed)

    build = PRESETS[key]["build"]
    return [build(rng, faker, i, opts) for i in range(max(0, rows))]


def preset_form_values(key, record):
    """Run a form-backed preset's mapping on one record -> {field: value}."""
    preset = PRESETS.get(key)
    if not preset or preset["kind"] != "form":
        raise ValueError(f"Preset '{key}' is not a form-backed preset.")
    return preset["mapping"](record)
