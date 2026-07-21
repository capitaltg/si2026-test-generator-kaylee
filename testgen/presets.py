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

# Services NAICS codes with their 2026 SBA size standard (receipts-based unless
# noted). Used to fill SF-1449 block 5a/5b — the boxes a reviewer expects on any
# commercial-items acquisition.
_NAICS = [
    ("541511", "Custom Computer Programming Services", "$34.0M"),
    ("541512", "Computer Systems Design Services", "$34.0M"),
    ("541519", "Other Computer Related Services", "$34.0M"),
    ("541330", "Engineering Services", "$25.5M"),
    ("541611", "Administrative Management and General Management Consulting", "$24.5M"),
    (
        "541712",
        "Research and Development in the Physical, Engineering and Life Sciences",
        "1,000 employees",
    ),
    ("561210", "Facilities Support Services", "$47.0M"),
]

# Set-aside category and the SF-1449 block-10 checkbox(es) it lights up. The
# on-state for every one of these boxes is "/1". "Unrestricted" checks the lone
# unrestricted box; every set-aside checks the SET ASIDE box plus its category.
_SET_ASIDES = [
    ("Unrestricted", ["UNRESTRICTIONTED[0]"]),
    ("Small Business", ["SETASIDE[0]", "SMALLBUSINESS[2]"]),
    ("8(a)", ["SETASIDE[0]", "ACHECKBOX[0]"]),
    (
        "Service-Disabled Veteran-Owned Small Business",
        ["SETASIDE[0]", "SERVICEDISABLED[0]"],
    ),
    ("Women-Owned Small Business", ["SETASIDE[0]", "SMALLBUSINESS[1]"]),
    ("HUBZone Small Business", ["SETASIDE[0]", "HUBZONESMALL[0]"]),
]
# Favor unrestricted / small-business; the niche categories are less common.
_SET_ASIDE_WEIGHTS = [4, 4, 1, 1, 1, 1]


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
        "phone": faker.numerify("(###) ###-####"),
    }
    contract_type = rng.choice(["T&M", "CPFF", "FFP", "IDIQ"])

    # Acquisition metadata a real award records: the NAICS + size standard the
    # buy was solicited under, the set-aside, and the pre-award solicitation
    # milestones (issued, then offers due, then award — strictly in that order).
    naics = rng.choice(_NAICS)
    set_aside = rng.choices(_SET_ASIDES, weights=_SET_ASIDE_WEIGHTS)[0][0]
    solicitation_issue = effective - datetime.timedelta(days=rng.randint(45, 90))
    offer_due = effective - datetime.timedelta(days=rng.randint(10, 30))
    labor_type = "FFP" if contract_type == "FFP" else rng.choice(["T&M", "CPFF"])

    # Office DoDAAC-style codes (the CODE boxes beside "issued by" / "administered
    # by") and the finance identifiers a processed/paid award record carries.
    issuing_office_code = _alnum(rng, 6)
    admin_office_code = _alnum(rng, 6)
    payment = {
        "paying_office": "Defense Finance and Accounting Service (DFAS)",
        "voucher_no": faker.bothify(text="PV-########"),
        "check_no": faker.numerify("##########"),
        "sr_account_no": faker.bothify(text="SR-####-#####"),
        "disbursing_officer": faker.name(),
    }

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
        "solicitation_issue_date": solicitation_issue,
        "offer_due_date": offer_due,
        "naics_code": naics[0],
        "naics_title": naics[1],
        "size_standard": naics[2],
        "set_aside": set_aside,
        "discount_terms": rng.choice(["Net 30", "Net 30", "1% 10, Net 30", "Net 15"]),
        "agency": agency["name"],
        "issuing_office": agency["office"],
        "issuing_office_code": issuing_office_code,
        "admin_office_code": admin_office_code,
        "payment": payment,
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


def _setaside_boxes(set_aside):
    """The SF-1449 block-10 checkbox field(s) to switch on for a set-aside label,
    each set to its "/1" on-state. Falls back to unrestricted if unrecognized."""
    for label, boxes in _SET_ASIDES:
        if label == set_aside:
            return {_P + b: "/1" for b in boxes}
    return {_P + "UNRESTRICTIONTED[0]": "/1"}


def contract_to_sf1449(contract):
    """Map a contract onto the real SF-1449 (commercial award). The base-year
    CLINs go into the line-item grid (rows 1-8); header blocks carry the PIID,
    solicitation, agency and contractor."""
    c = contract
    contractor = c["contractor"]
    pay = c["payment"]
    base_value = _fmt_money(c["periods"][0]["ceiling"] if c["periods"] else 0.0)
    values = {
        # --- Header: identifiers, dates, offices (blocks 1-9, 15-18). ---
        _P + "reqnumber[0]": c["solicitation_no"],
        _P + "contractno[0]": c["piid"],
        _P + "solicitationnumber[0]": c["solicitation_no"],
        # Block 9 issued-by: the short office code, then the full name/address.
        _P + "issuedbycode[0]": c["issuing_office"],
        _P + "TextField1[4]": f"{c['agency']}\n{c['issuing_office']}",
        _P + "AdministeredBy[0]": c["agency"],
        # 17a code box is narrow; the 5-char CAGE fits, full UEI/CAGE go in 17a's
        # address block below it, with the contractor telephone in 17b.
        _P + "contractorcode[0]": contractor["cage"],
        _P + "contractoraddress[0]": f"{contractor['name']}\n{contractor['address']}\n"
        f"UEI {contractor['uei']}  CAGE {contractor['cage']}",
        _P + "TextField1[1]": contractor["phone"],
        _P + "paymentbyaddress[0]": c["issuing_office"],
        _P + "pagenumber[0]": "1",
        # Block 3 award/effective, plus the pre-award solicitation milestones
        # (block 6 issued, block 8 offers due) that necessarily precede it.
        _P + "AWARDDate[0]": _fmt_date(c["effective_date"]),
        _P + "Date[3]": _fmt_date(c["solicitation_issue_date"]),  # 6. issue date
        _P + "Date[4]": _fmt_date(c["offer_due_date"]),  # 8. offer due date
        _P + "TextField1[2]": "2:00 PM local time",  # 8. offer due local time
        _P + "contactname[0]": c["contracting_officer"],
        _P + "contactphone[0]": c["co_phone"],
        _P + "DeliverTo[0]": c["issuing_office"],
        # Block 5a/5b: the NAICS the buy was solicited under and its size standard.
        _P + "NAICS[0]": c["naics_code"],
        _P + "SIZESTANDARDS[0]": c["size_standard"],
        # Block 12: prompt-payment discount terms.
        _P + "discountterms[0]": c["discount_terms"],
        _P
        + "accountingdata[0]": (
            f"Appropriation FY{c['effective_date'].year % 100:02d}; "
            f"Obligated to date {_fmt_money(c['total_obligated'])} of "
            f"{_fmt_money(c['total_ceiling'])} ceiling."
        ),
        # Block 26 total award = the awarded (base-year) value.
        _P + "TOTALAWARD[0]": base_value,
        # Block 14 method of solicitation: commercial items are bought by RFQ.
        _P + "RFQ[0]": "/1",
        # Block 27b: this contract incorporates FAR 52.212-4 by reference; no
        # addenda attached. Block 28: contractor is required to sign and return.
        _P + "CheckBox1[1]": "/1",
        _P + "arenot2[0]": "/1",
        _P + "CheckBox1[2]": "/1",
        # --- Finalized bilateral award: both parties signed (blocks 30-31). ---
        _P + "signername[0]": f"{contractor['name']} / Authorized Signatory",
        _P + "signertitle[0]": c["signer_title"],
        _P + "Date[1]": _fmt_date(c["effective_date"]),  # 30c. contractor signed
        _P + "contractingofficer[0]": c["contracting_officer"],  # 31b. CO name
        _P + "Date[2]": _fmt_date(c["effective_date"]),  # 31c. CO signed
        # --- Page 2: government acceptance + payment (a delivered, accepted and
        # paid contract record — blocks 32-42). ---
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
        # Payment-processing blocks 34-41: a voucher was cut, verified against the
        # award, charged to a stores/stock-record account, and paid by DFAS.
        "topmostSubform[0].Page2[0].vouchernumber[0]": pay["voucher_no"],  # 34
        "topmostSubform[0].Page2[0].amountverified[0]": base_value,  # 35
        "topmostSubform[0].Page2[0].checknumber[0]": pay["check_no"],  # 37
        "topmostSubform[0].Page2[0].SRAccountNo[0]": pay["sr_account_no"],  # 38
        "topmostSubform[0].Page2[0].SRVoucherNo[0]": pay["voucher_no"],  # 39
        "topmostSubform[0].Page2[0].PaidBy[0]": pay["paying_office"],  # 40
        "topmostSubform[0].Page2[0].TitleCertifyOfficer[0]": "Authorized Certifying Officer",  # 41b
    }

    # Block 10 set-aside: light up the matching checkbox(es), and — when it is a
    # set-aside rather than unrestricted — the 100% set-aside percentage.
    values.update(_setaside_boxes(c["set_aside"]))
    if c["set_aside"] != "Unrestricted":
        values[_P + "setasidepercent[0]"] = "100"

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
    mod_date = _fmt_date(mod["date"])

    accounting = (
        f"Appropriation FY{c['effective_date'].year % 100:02d}; "
        f"Obligated this action {_fmt_money(mod['amount'])}."
    )

    # Block 1 "Contract ID Code" is a single award-instrument letter, not the
    # contract type name; map the type to a plausible code.
    id_code = {"IDIQ": "D", "FFP": "C", "T&M": "C", "CPFF": "C"}.get(
        c["contract_type"], "C"
    )

    values = {
        # Block 2 = the amendment/modification number (this action).
        _P + "AmendmentNo[0]": mod["mod"],
        # Block 10A = the CONTRACT/ORDER number being modified (the PIID); its
        # checkbox marks that this action modifies a contract (not item 9's
        # solicitation), and 10B carries the original contract's effective date.
        # (Item 9's amendment-of-solicitation boxes stay blank on purpose — a
        # single SF-30 is EITHER a solicitation amendment OR a contract mod.)
        _P + "ModificationNo[0]": c["piid"],
        _P + "CheckBox10[0]": "/1",
        _P + "Dated10B[0]": _fmt_date(c["effective_date"]),
        _P + "EffectiveDate[0]": mod_date,
        _P + "ContractIDCode[0]": id_code,
        _P + "ReqNumber[0]": c["solicitation_no"],
        # Blocks 6/7/8: offices with their DoDAAC-style CODE boxes, contractor
        # with its CAGE (CODE) and facility code.
        _P + "IssuedBy[0]": f"{c['agency']}\n{c['issuing_office']}",
        _P + "Code[0]": c["issuing_office_code"],
        _P + "AdministeredBy[0]": c["issuing_office"],
        _P + "Code[2]": c["admin_office_code"],
        _P + "NameandAddress[0]": f"{contractor['name']}\n{contractor['address']}\n"
        f"UEI {contractor['uei']}  CAGE {contractor['cage']}",
        _P + "Code[1]": contractor["cage"],
        _P + "FacilityCode[0]": contractor["cage"],
        _P + "AccountingData[0]": accounting,
        _P
        + "NameandTitleSigner[0]": f"{contractor['name']} (Authorized Representative)",
        _P
        + "NameandTitleOfficer[0]": f"{c['contracting_officer']}, Contracting Officer",
        # 16C: the Contracting Officer's signature date (always present).
        _P + "DateSigned[1]": mod_date,
        _P + "Page[0]": "1",
        _P + "Pages[0]": "1",
    }

    # An option exercise is a BILATERAL supplemental agreement (13C): both parties
    # sign (blocks 15C + 16C) and the contractor returns copies. An incremental-
    # funding or administrative action is a UNILATERAL change order (13A) that the
    # CO alone executes — the contractor is not required to sign (13E).
    if "option" in mod["action"].lower():
        values[_P + "CheckBox13C[0]"] = "/1"
        values[_P + "C13[0]"] = "Mutual agreement of the parties (FAR 43.103(a))"
        values[_P + "Is[0]"] = "/1"
        values[_P + "DateSigned[0]"] = mod_date  # 15C contractor signed
        values[_P + "Copies[0]"] = "3"
        values[_P + "CopiesReturned[0]"] = "3"
        values[_P + "Description[0]"] = (
            f"The purpose of this modification is to exercise {mod['action'].lower()} "
            "in accordance with FAR 52.217-9. Accordingly: (a) The Government "
            f"exercises the option, obligating {_fmt_money(mod['amount'])} "
            f"(cumulative obligated {_fmt_money(mod['cumulative_obligated'])}). "
            f"(b) The total contract ceiling remains {_fmt_money(c['total_ceiling'])}. "
            "(c) All other terms and conditions remain unchanged."
        )
    else:
        values[_P + "CheckBox13A[0]"] = "/1"
        values[_P + "A13[0]"] = "FAR 52.232-22, Limitation of Funds"
        values[_P + "IsNot[0]"] = "/1"
        values[_P + "Description[0]"] = (
            "The purpose of this modification is to obligate incremental funding. "
            "Accordingly: (a) Total funds obligated on this contract are increased "
            f"by {_fmt_money(mod['amount'])}, from {_fmt_money(prev_cumulative)} to "
            f"{_fmt_money(mod['cumulative_obligated'])}. (b) The total contract "
            f"ceiling remains {_fmt_money(c['total_ceiling'])}. (c) All other terms "
            "and conditions remain unchanged and in full force and effect."
        )
    return values


# --- Drawn-document presets (no official form exists) -------------------------
# These build a contract, then flatten it into the flat, display-ready row that
# the drawn PDF writer (pdf.py) renders. Money/dates are pre-formatted as strings
# so they read well on the page. Each preset pairs a builder with a `fields`
# column order and a `config` describing the drawn layout.


def _pop(c):
    """The overall period of performance: base-year start through the last
    period's end (i.e. inclusive of all option years), as 'start to end'."""
    periods = c["periods"]
    if not periods:
        return ""
    return (
        f"{_fmt_date(periods[0]['pop_start'])} to {_fmt_date(periods[-1]['pop_end'])}"
    )


def _funding_summary_row(rng, faker, index, opts=None):
    c = build_contract(rng, faker, index, opts)
    return {
        "contract_no": c["piid"],
        "contractor": c["contractor"]["name"],
        "agency": c["agency"],
        "contract_type": c["contract_type"],
        "pop": _pop(c),
        "contracting_officer": c["contracting_officer"],
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
        "pop": _pop(c),
        "contracting_officer": c["contracting_officer"],
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
        # A voucher schedule number the disbursing office assigns to the invoice.
        "schedule_no": faker.bothify(text="SCH-######"),
        "contract_no": c["piid"],
        "contractor": c["contractor"]["name"],
        "uei": c["contractor"]["uei"],
        "remit_to": c["contractor"]["address"],
        "agency": c["agency"],
        "contracting_officer": c["contracting_officer"],
        "accounting": f"Appropriation FY{inv_date.year % 100:02d}; {c['piid']}",
        "paying_office": c["payment"]["paying_office"],
        "check_no": c["payment"]["check_no"],
        "disbursing_officer": c["payment"]["disbursing_officer"],
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
        _P + "ScheduleNumber[0]": row["schedule_no"],
        _P + "DateVoucherPrepared[0]": row["invoice_date"],
        _P + "DateInvoiceReceived[0]": row["invoice_date"],
        _P + "ContractNoandDate[0]": row["contract_no"],
        _P + "USDepartmentLocation[0]": row["agency"],
        _P + "PayeeNameAddress[0]": f"{row['contractor']}\n{row['remit_to']}",
        # Payee account = the contractor's UEI (how the payee is keyed in SAM).
        _P + "PayeeAccountNo[0]": f"UEI {row['uei']}",
        _P + "AccountingClassification[0]": row["accounting"],
        _P + "TotalAmount[0]": row["amount_due"],
        _P + "AmountVerified[0]": row["amount_due"],
        # Bottom "APPROVED FOR $" line (both the label box and the amount box).
        _P + "approved[0]": row["amount_due"],
        _P + "ApprovedFor[0]": row["amount_due"],
        # An interim monthly voucher against an ongoing contract is a PARTIAL,
        # PROGRESS payment (neither complete nor final).
        _P + "Partial[0]": "/1",
        _P + "Progress[0]": "/1",
        # "Pursuant to the authority vested in me, I certify this voucher is
        # correct and proper for payment" — the certifying-officer block. (The
        # signature boxes are /Sig fields and can't hold text, so only the
        # printed name/title/date are filled.)
        _P + "By2[0]": row["contracting_officer"],
        _P + "Title[1]": "Contracting Officer",
        _P + "DateofCertify[0]": row["invoice_date"],
        _P + "Title[2]": "Authorized Certifying Officer",
        # Disbursing block: paid by Treasury check to the payee.
        _P + "ScheduleNumber[1]": row["paying_office"],  # "Paid By"
        _P + "CheckNo[0]": row["check_no"],
        _P + "TreasurerofUS[0]": "Treasurer of the United States",
        _P + "Date1[0]": row["invoice_date"],
        _P + "Payee3[0]": row["contractor"],
        _P + "For[0]": row["disbursing_officer"],
        _P + "Title[0]": "Disbursing Officer",
    }
    for i, li in enumerate(row["line_items"][:9]):
        values[_P + f"ArticlesServices[{i}]"] = _short_desc(li["description"])
        values[_P + f"NumberDateofOrder[{i}]"] = li["clin"]
        values[_P + f"DateofDelivery[{i}]"] = row["invoice_date"]
        # Quantity 1 at a lot-price unit cost, so unit price (Cost) == amount.
        values[_P + f"Cost{i + 1}[0]"] = li["amount_billed"]
        values[_P + f"Amount{i + 1}[0]"] = li["amount_billed"]
        values[_P + f"Quantity{i + 1}[0]"] = "1"
        values[_P + f"Per[{i}]"] = "LO"
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
                ("contract_type", "Contract Type", r["contract_type"]),
                (
                    "contracting_officer",
                    "Contracting Officer",
                    r["contracting_officer"],
                ),
            ],
        },
        {
            "type": "field",
            "name": "pop",
            "label": "Period of Performance",
            "value": r["pop"],
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
                (
                    "contracting_officer",
                    "Contracting Officer",
                    r["contracting_officer"],
                ),
            ],
        },
        {
            "type": "field",
            "name": "pop",
            "label": "Period of Performance",
            "value": r["pop"],
        },
        {
            "type": "pair",
            "fields": [
                ("total_ceiling", "Total Ceiling", r["total_ceiling"]),
                ("total_obligated", "Total Obligated", r["total_obligated"]),
            ],
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


# --- Flat CSV dataset presets (ERP-style labor exports) -----------------------
# These are the "data" kind: each build() returns one flat, uniform-keyed record,
# and the generic writers serialize the batch to CSV / SQL / JSON. They reuse the
# same _LCATS reference data as the contracts/invoices, so a given labor category
# bills a consistent, realistic loaded rate everywhere it appears. They model the
# tabular exports a GovCon ERP (Unanet / Deltek Costpoint) produces — the system
# of record where labor categories, bill rates, hours and charge codes actually
# live — rather than any government form.


def _loaded_rate(rng, spec):
    """A fully-burdened (loaded) bill rate for an LCAT: a rate inside the LCAT's
    band plus its clearance premium. Same buildup as the contract labor lines."""
    lo, hi = spec["band"]
    base_loaded = round(rng.uniform(lo, hi), 2)
    prem_lo, prem_hi = _CLEARANCE_PREMIUM[spec["clr"]]
    premium = round(rng.uniform(prem_lo, prem_hi), 2) if prem_hi else 0.0
    return round(base_loaded + premium, 2)


def _employee(faker):
    """A (name, employee-id) pair for a labor line."""
    return faker.name(), faker.bothify(text="E-#####")


def _charge_ref(rng, faker):
    """A plausible contract number (PIID) and a base-year labor CLIN to charge
    time against — the charge code an employee books hours to."""
    agency = _pick_agency(rng, None)
    piid = _piid(rng, faker, agency, _effective_date(rng))
    return piid, f"000{rng.randint(1, 4)}"


def _recent_month(rng):
    """A YYYY-MM within roughly the last year (an accounting period)."""
    today = datetime.date.today()
    year, month = today.year, today.month - rng.randint(0, 11)
    while month < 1:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def _recent_week_ending(rng):
    """A recent Friday (a weekly timesheet's week-ending date)."""
    day = datetime.date.today() - datetime.timedelta(weeks=rng.randint(0, 25))
    return _fmt_date(day - datetime.timedelta(days=(day.weekday() - 4) % 7))


def build_scenario(seed, opts=None):
    """A seed-stable 'scenario' the labor exports share: one contract plus a
    billing roster mapped to its base-year labor CLINs.

    The contract is built exactly the way the award preset builds its FIRST row
    (a fresh Random(seed) + seeded Faker at index 0), so for a given seed the
    Contract Award, the Timesheet and the Labor Distribution Export all reference
    the SAME contract number, the SAME CLINs, and (for the two exports) the SAME
    people at the SAME rates — so the generated set analyzes as one coherent
    contract. Consistency holds only when a seed is set (None => not reproducible).
    """
    import random

    rng = random.Random(seed)
    faker = Faker()
    if seed is not None:
        faker.seed_instance(seed)
    contract = build_contract(rng, faker, 0, opts)

    # One roster entry per labor line on each base-year labor CLIN: a named person
    # tied to that CLIN, their LCAT, and the CLIN's actual loaded bill rate.
    roster = []
    base = contract["periods"][0]["clins"] if contract["periods"] else []
    for clin in base:
        for line in clin.get("labor_rates", []):
            name, emp_id = _employee(faker)
            roster.append(
                {
                    "employee": name,
                    "employee_id": emp_id,
                    "labor_category": line["lcat"],
                    "clearance": line["clearance"],
                    "clin": clin["clin"],
                    "bill_rate": line["loaded_rate"],
                }
            )
    return {"contract": contract, "roster": roster}


def _scenario_member(opts, index):
    """The roster member for this row when a shared scenario is in play, else
    None (the builder then falls back to a self-contained random line)."""
    scenario = (opts or {}).get("_scenario")
    if not scenario or not scenario["roster"]:
        return None, None
    roster = scenario["roster"]
    return scenario["contract"]["piid"], roster[index % len(roster)]


def _labor_export_row(rng, faker, index, opts=None):
    """One labor distribution / billing line: an employee charged a labor category
    to a contract CLIN for a period, at a loaded bill rate. hours * bill_rate ==
    billable_amount, so the dollars reconcile (like the contract invariants). When
    a shared scenario is present, the person / CLIN / rate come from it so the line
    ties back to the awarded contract; only the hours and period are re-rolled."""
    piid, member = _scenario_member(opts, index)
    if member:
        name, emp_id = member["employee"], member["employee_id"]
        lcat, clearance, clin, rate = (
            member["labor_category"],
            member["clearance"],
            member["clin"],
            member["bill_rate"],
        )
    else:
        spec = rng.choice(_LCATS)
        rate = _loaded_rate(rng, spec)
        piid, clin = _charge_ref(rng, faker)
        name, emp_id = _employee(faker)
        lcat, clearance = spec["lcat"], spec["clr"] or "None"
    # Most lines are a full month (~150-184 hrs); some are partial.
    hours = round(rng.uniform(150, 184) if rng.random() < 0.7 else rng.uniform(16, 140))
    return {
        "employee": name,
        "employee_id": emp_id,
        "labor_category": lcat,
        "clearance": clearance,
        "contract_no": piid,
        "clin": clin,
        "period": _recent_month(rng),
        "hours": float(hours),
        "bill_rate": rate,
        "billable_amount": _round_money(hours * rate),
    }


def _timesheet_row(rng, faker, index, opts=None):
    """One weekly employee timesheet line: hours booked to a charge code (CLIN),
    split into regular / overtime / leave. A real timesheet carries NO bill rate
    (that is proprietary and lives on the billing side), so this one doesn't. With
    a shared scenario, the person and charge code match the labor export and the
    awarded contract; the hours are re-rolled so each week differs."""
    piid, member = _scenario_member(opts, index)
    if member:
        name, emp_id = member["employee"], member["employee_id"]
        lcat, clin = member["labor_category"], member["clin"]
    else:
        spec = rng.choice(_LCATS)
        piid, clin = _charge_ref(rng, faker)
        name, emp_id = _employee(faker)
        lcat = spec["lcat"]
    # Regular + leave make up a standard 40-hour week; overtime is on top.
    leave = float(rng.choice([0, 0, 0, 0, 0, 8, 16]))
    reg = round(40.0 - leave, 1)
    ot = float(rng.choice([0, 0, 0, 0, 2, 4, 6, 8]))
    return {
        "employee": name,
        "employee_id": emp_id,
        "week_ending": _recent_week_ending(rng),
        "contract_no": piid,
        "charge_code": clin,
        "labor_category": lcat,
        "reg_hours": reg,
        "ot_hours": ot,
        "leave_hours": leave,
        "total_hours": round(reg + ot + leave, 1),
        "approved_by": faker.name(),
    }


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
    "govcon_labor_export": {
        "label": "Labor Distribution Export",
        "description": "A labor billing/cost distribution export: employee, labor "
        "category, contract/CLIN, hours, bill rate and billable amount by period "
        "(hours × rate reconciles). Modeled after a Unanet / Deltek Costpoint "
        "labor export — feeds bill-rate and burn-rate analysis.",
        "kind": "data",
        "build": _labor_export_row,
        "scenario": True,
    },
    "govcon_timesheet": {
        "label": "Timesheet",
        "description": "Weekly employee timesheets: charge code/CLIN with regular, "
        "overtime and leave hours by week-ending date, plus approver. No bill rate "
        "(as on a real timesheet). Modeled after a Unanet / Deltek Costpoint "
        "timesheet export.",
        "kind": "data",
        "build": _timesheet_row,
        "scenario": True,
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

    # Scenario presets (the labor exports) share a seed-stable contract + roster so
    # their rows tie back to the awarded contract. Built from the untouched user
    # opts, then passed to each row via a private opts key.
    opts = dict(opts or {})
    if PRESETS[key].get("scenario"):
        base_opts = {k: v for k, v in opts.items() if not k.startswith("_")}
        opts["_scenario"] = build_scenario(seed, base_opts)

    build = PRESETS[key]["build"]
    return [build(rng, faker, i, opts) for i in range(max(0, rows))]


def preset_form_values(key, record):
    """Run a form-backed preset's mapping on one record -> {field: value}."""
    preset = PRESETS.get(key)
    if not preset or preset["kind"] != "form":
        raise ValueError(f"Preset '{key}' is not a form-backed preset.")
    return preset["mapping"](record)
