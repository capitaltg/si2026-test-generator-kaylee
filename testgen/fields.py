"""The field-type registry: the list of value kinds a schema can ask for.

Each field type is a small function that produces ONE value for ONE row. They
all share the same signature so the engine can call any of them the same way:

    fn(field, index, rng, faker) -> value

    field  the field spec dict, so the function can read options (min, max, ...)
    index  which row we are on (0-based), used for sequential ids
    rng    a *seeded* random.Random. Use this for ALL randomness so output stays
           reproducible. Never use the global `random` module here.
    faker  a *seeded* Faker instance, for realistic values (names, companies...)

To add a new field type later, write a function with this signature and add it
to the FIELD_TYPES dict at the bottom. Nothing else needs to change.
"""

from __future__ import annotations

import datetime
import string


def _name(field, index, rng, faker):
    return faker.name()


def _company(field, index, rng, faker):
    return faker.company()


def _email(field, index, rng, faker):
    return faker.email()


def _city(field, index, rng, faker):
    return faker.city()


def _state(field, index, rng, faker):
    return faker.state()


def _int(field, index, rng, faker):
    """Whole number between min and max (inclusive). Options: min, max."""
    low = field.get("min", 0)
    high = field.get("max", 100)
    return rng.randint(low, high)


def _float(field, index, rng, faker):
    """Decimal between min and max. Options: min, max, round (decimal places)."""
    low = field.get("min", 0.0)
    high = field.get("max", 1.0)
    value = rng.uniform(low, high)
    return round(value, field.get("round", 2))


def _choice(field, index, rng, faker):
    """Pick one from a list. Options: choices or values (one required),
    weights (optional).

    choices is a real list ["A", "B"]. values is the same thing as a single
    comma-separated string "A, B" (this is how Fixtura's "enum" type sends it).
    weights lets some options show up more often than others, e.g.
    choices=["A", "B"], weights=[9, 1] makes "A" roughly 9x as common.
    """
    choices = field.get("choices")
    if not choices and field.get("values") is not None:
        choices = [c.strip() for c in str(field["values"]).split(",") if c.strip()]
    if not choices:
        raise ValueError(
            "choice/enum needs 'choices' (a list) or 'values' (a comma string)."
        )
    weights = field.get("weights")
    if weights:
        return rng.choices(choices, weights=weights, k=1)[0]
    return rng.choice(choices)


def _bool(field, index, rng, faker):
    """True/False. Options: true_chance (0..1, default 0.5)."""
    return rng.random() < field.get("true_chance", 0.5)


def _date(field, index, rng, faker):
    """A date between start and end. Options: start, end (as 'YYYY-MM-DD')."""
    start = _as_date(field.get("start", "2000-01-01"))
    end = _as_date(field.get("end", "2025-12-31"))
    span_days = (end - start).days
    return start + datetime.timedelta(days=rng.randint(0, span_days))


def _sequence(field, index, rng, faker):
    """A counting id like GS-1000, GS-1001. Options: start (int), prefix (str).

    Not random at all: it just counts up with the row index, so ids are unique
    and predictable.
    """
    start = field.get("start", 1)
    prefix = field.get("prefix", "")
    return f"{prefix}{start + index}"


def _uuid(field, index, rng, faker):
    """A random-looking unique id. Seeded via faker, so still reproducible."""
    return faker.uuid4()


def _first_name(field, index, rng, faker):
    return faker.first_name()


def _last_name(field, index, rng, faker):
    return faker.last_name()


def _phone(field, index, rng, faker):
    return faker.phone_number()


def _job(field, index, rng, faker):
    return faker.job()


def _country(field, index, rng, faker):
    return faker.country()


def _address(field, index, rng, faker):
    """Full multi-line mailing address."""
    return faker.address()


def _street_address(field, index, rng, faker):
    """Single-line street address (no city/state/zip)."""
    return faker.street_address()


def _zipcode(field, index, rng, faker):
    return faker.postcode()


def _url(field, index, rng, faker):
    return faker.url()


def _word(field, index, rng, faker):
    return faker.word()


def _sentence(field, index, rng, faker):
    return faker.sentence()


def _paragraph(field, index, rng, faker):
    return faker.paragraph()


def _money(field, index, rng, faker):
    """Dollar amount with cents. Options: min, max (defaults 1000..1_000_000)."""
    low = field.get("min", 1000.0)
    high = field.get("max", 1_000_000.0)
    return round(rng.uniform(low, high), 2)


def _constant(field, index, rng, faker):
    """Always the same value on every row. Options: value (required).
    Handy for a tag column like {"type": "constant", "value": "FY2024"}."""
    return field["value"]


def _pattern(field, index, rng, faker):
    """A value matching a template. Options: pattern (required).

    In the pattern, '#' becomes a random digit and '?' a random uppercase
    letter. Perfect for custom id formats, e.g. 'CAGE-#####', 'DUNS-#########',
    or '??-####'. Seeded via faker, so reproducible.
    """
    return faker.bothify(text=field["pattern"], letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _as_date(value):
    """Accept either a date object or a 'YYYY-MM-DD' string."""
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(value)


def make_record(schema, index, rng, faker):
    """Build one record dict from a (sub)schema, honouring per-field null_pct.

    This is the shared per-row logic used both by the engine's top-level loop
    and by the nested `list` field type below, so a child record is generated
    exactly the way a top-level row is. Kept here (not in core.py) so the nested
    field type can reach it without a circular import.
    """
    record = {}
    for field in schema:
        null_pct = field.get("null_pct", 0)
        if null_pct and rng.random() * 100 < null_pct:
            record[field["name"]] = None
        else:
            record[field["name"]] = FIELD_TYPES[field["type"]](field, index, rng, faker)
    return record


def _list(field, index, rng, faker):
    """A nested list of child records — e.g. the CLIN line items on a contract.

    Options:
      fields     the child schema (a list of field specs), same shape as the
                 top-level schema
      count      a fixed number of children, OR
      min, max   a random child count in this inclusive range (defaults 1..3)

    Returns a list of dicts. Children are generated with the same seeded rng and
    faker, so the whole nested structure stays reproducible. Each child's own
    row index is used, so a `sequence` inside the list counts 0,1,2,... per
    parent.
    """
    child_schema = field.get("fields") or []
    if "count" in field:
        n = int(field["count"])
    else:
        n = rng.randint(int(field.get("min", 1)), int(field.get("max", 3)))
    return [make_record(child_schema, i, rng, faker) for i in range(max(0, n))]


# --- Fixtura parity: extra generators to match the designed type menu --------
# Each still follows the standard fn(field, index, rng, faker) signature. Where
# Faker offers a good realistic value we use it (all seeded, so reproducible);
# for a few we roll our own with the seeded rng.

_PRODUCTS = [
    "Standard License",
    "Pro Subscription",
    "Data Pack",
    "Support Plan",
    "Analytics Add-on",
    "Enterprise Seat",
    "API Credits",
    "Storage Tier",
    "Onboarding Kit",
    "Security Module",
]
_GENDERS = ["Female", "Male", "Non-binary"]


def _full_name(field, index, rng, faker):
    return faker.name()


def _gender(field, index, rng, faker):
    return rng.choice(_GENDERS)


def _age(field, index, rng, faker):
    """Whole number age. Options: min (default 18), max (default 80)."""
    return rng.randint(field.get("min", 18), field.get("max", 80))


def _username(field, index, rng, faker):
    return faker.user_name()


def _latitude(field, index, rng, faker):
    return float(faker.latitude())


def _longitude(field, index, rng, faker):
    return float(faker.longitude())


def _domain(field, index, rng, faker):
    return faker.domain_name()


def _ipv4(field, index, rng, faker):
    return faker.ipv4()


def _mac_address(field, index, rng, faker):
    return faker.mac_address()


def _color(field, index, rng, faker):
    """A hex color like #3fa9c2. Built from the seeded rng so it is reproducible
    regardless of the installed Faker version."""
    return f"#{rng.randint(0, 0xFFFFFF):06x}"


def _product(field, index, rng, faker):
    return rng.choice(_PRODUCTS)


def _currency(field, index, rng, faker):
    return faker.currency_code()


def _credit_card(field, index, rng, faker):
    return faker.credit_card_number()


def _datetime(field, index, rng, faker):
    """A datetime between start and end. Options: start, end ('YYYY-MM-DD')."""
    start = _as_date(field.get("start", "2000-01-01"))
    end = _as_date(field.get("end", "2025-12-31"))
    return faker.date_time_between(start_date=start, end_date=end)


def _time(field, index, rng, faker):
    """A clock time like 14:37:05."""
    return faker.time()


# The registry: type name -> the function that generates it. This is the whole
# menu of field types the tool currently understands, grouped for readability.
# --- GovCon identifiers -------------------------------------------------------
# These generators (and their reference data) are the single source of truth for
# GovCon identifiers across the whole tool: the Builder field types below AND the
# GovCon presets, which import the rng-only helpers so a preset contract and a
# hand-built schema mint identifiers the same way.

# A real UEI (Unique Entity Identifier) is exactly 12 chars, excludes the letters
# I and O (to avoid confusion with 1 and 0), and never starts with 0.
_UEI_ALPHABET = (
    "".join(c for c in string.ascii_uppercase if c not in "IO") + string.digits
)

# (code, description, small-business size standard) — a small, realistic slice of
# the NAICS codes common on professional-services contracts.
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

# (code, description) Product/Service Codes — the "what is being bought" taxonomy.
# A mix of service (letter-led) and product (digit-led) codes across the areas a
# GovCon dataset actually touches; wider than a token list so generated data has
# real variety.
_PSC = [
    ("R425", "Support - Professional: Engineering/Technical"),
    ("R408", "Support - Professional: Program Management/Support"),
    ("R499", "Support - Professional: Other"),
    ("R707", "Support - Management: Contract/Procurement/Acquisition"),
    ("R706", "Support - Management: Logistics Support"),
    ("B505", "Special Studies/Analysis - Cost Benefit"),
    ("B506", "Special Studies/Analysis - Data (Other Than Scientific)"),
    ("D307", "IT and Telecom - IT Systems Development Services"),
    ("D302", "IT and Telecom - Systems Analysis Services"),
    ("D310", "IT and Telecom - Cyber Security and Data Backup"),
    ("D316", "IT and Telecom - Telecommunications Network Management"),
    ("D399", "IT and Telecom - Other IT and Telecommunications"),
    ("DA01", "IT and Telecom - Business Application/Software"),
    ("DE02", "IT and Telecom - Systems Development Support"),
    ("AR11", "R&D - Space: Applied Research/Exploratory Development"),
    ("AJ11", "R&D - General Science/Technology: Applied Research"),
    ("U008", "Education/Training - Training/Curriculum Development"),
    ("U099", "Education/Training - Other"),
    ("H170", "Quality Control - Inspection: ADP Equipment/Software"),
    ("J070", "Maintenance/Repair - ADP Equipment/Software"),
    ("7010", "ADP System Configuration"),
    ("7021", "ADP Central Processing Units (CPU)"),
    ("7025", "ADP Input/Output and Storage Devices"),
    ("7030", "ADP Software"),
    ("7035", "ADP Support Equipment"),
    ("5810", "Communications Security Equipment and Components"),
    ("5820", "Radio and Television Communication Equipment"),
    ("5895", "Miscellaneous Communication Equipment"),
    ("5998", "Electrical and Electronic Assemblies, Boards, Cards"),
    ("6110", "Electrical Control Equipment"),
    ("5985", "Antennas, Waveguides, and Related Equipment"),
    ("7050", "ADP Components"),
    ("R413", "Support - Professional: Specifications Development"),
    ("R410", "Support - Professional: Program Evaluation/Review/Development"),
    ("D306", "IT and Telecom - Data Centers and Storage"),
    ("R497", "Support - Professional: Personal Services Contracts"),
]

# PIID (contract/award number) shapes across a few common issuing patterns.
# `#`=digit, `?`=uppercase letter (via faker.bothify). A standalone PIID is a
# plausible-looking number; the internally-coherent, agency-matched PIID comes
# from the GovCon presets.
_PIID_PATTERNS = [
    "FA####-##-?-####",
    "W##Q??-##-D-####",
    "N#####-##-C-####",
    "HSHQDC-##-?-#####",
    "47QTC?##D####",
    "GS-##F-####?",
]


def gen_uei(rng):
    """A syntactically valid 12-char UEI."""
    first = rng.choice([c for c in _UEI_ALPHABET if c != "0"])
    rest = "".join(rng.choice(_UEI_ALPHABET) for _ in range(11))
    return first + rest


def gen_alnum(rng, n):
    """An n-char uppercase alphanumeric token (CAGE style)."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(n))


def _uei(field, index, rng, faker):
    return gen_uei(rng)


def _cage_code(field, index, rng, faker):
    """A 5-char CAGE (Commercial and Government Entity) code."""
    return gen_alnum(rng, 5)


def _naics(field, index, rng, faker):
    """A 6-digit NAICS industry code."""
    return rng.choice(_NAICS)[0]


def _psc(field, index, rng, faker):
    """A 4-char Product/Service Code."""
    return rng.choice(_PSC)[0]


def _piid(field, index, rng, faker):
    """A plausible contract/award number (PIID)."""
    pattern = rng.choice(_PIID_PATTERNS)
    return faker.bothify(text=pattern, letters=string.ascii_uppercase)


FIELD_TYPES = {
    # people
    "name": _name,
    "first_name": _first_name,
    "last_name": _last_name,
    "email": _email,
    "phone": _phone,
    "job": _job,
    # places
    "city": _city,
    "state": _state,
    "country": _country,
    "address": _address,
    "street_address": _street_address,
    "zipcode": _zipcode,
    # organizations / web
    "company": _company,
    "url": _url,
    # free text
    "word": _word,
    "sentence": _sentence,
    "paragraph": _paragraph,
    # numbers / money
    "int": _int,
    "float": _float,
    "money": _money,
    # logic / choices / dates
    "choice": _choice,
    "bool": _bool,
    "date": _date,
    # ids / custom
    "sequence": _sequence,
    "uuid": _uuid,
    "pattern": _pattern,
    "constant": _constant,
    # --- Fixtura parity: new generators ---
    "full_name": _full_name,
    "gender": _gender,
    "age": _age,
    "username": _username,
    "latitude": _latitude,
    "longitude": _longitude,
    "domain": _domain,
    "ipv4": _ipv4,
    "mac_address": _mac_address,
    "color": _color,
    "product": _product,
    "currency": _currency,
    "credit_card": _credit_card,
    "datetime": _datetime,
    "time": _time,
    # --- Fixtura aliases: the camelCase names the designed dropdown sends,
    # pointed at the same generator as our snake_case names so both work. ---
    "firstName": _first_name,
    "lastName": _last_name,
    "fullName": _full_name,
    "jobTitle": _job,
    "streetAddress": _street_address,
    "zip": _zipcode,
    "macAddress": _mac_address,
    "creditCard": _credit_card,
    "autoIncrement": _sequence,
    "price": _money,
    "enum": _choice,
    # --- GovCon identifiers ---
    "uei": _uei,
    "cageCode": _cage_code,
    "cage_code": _cage_code,
    "naics": _naics,
    "psc": _psc,
    "piid": _piid,
    # nested: a field whose value is a list of child records (e.g. CLIN line
    # items). Used by document presets; aliases cover the likely spellings.
    "list": _list,
    "subtable": _list,
    "lineItems": _list,
}


def register_field_type(name, fn):
    """Add (or override) a field type at runtime, without editing this file.

    This is the public extension point. `fn` must have the standard signature
    used by every generator here:

        fn(field, index, rng, faker) -> value

    After registering, any schema can use {"type": name, ...} and both the
    library and the CLI will understand it. Example:

        from testgen import register_field_type, generate

        def cage_code(field, index, rng, faker):
            return faker.bothify("#####??###")

        register_field_type("cage_code", cage_code)
        generate([{"name": "cage", "type": "cage_code"}], rows=5, seed=1)
    """
    FIELD_TYPES[name] = fn


def available_field_types():
    """Return the sorted list of field type names the tool currently knows."""
    return sorted(FIELD_TYPES)


# Presentation metadata: how the type menu is grouped and labelled in the UI.
# Living here (not in the UI) keeps it the single source of truth, so every
# front door shows the same organized dropdown. Each type name below is a real
# key in FIELD_TYPES. This mirrors Fixtura's grouped dropdown.
FIELD_TYPE_GROUPS = [
    ("Identity", [("uuid", "UUID"), ("autoIncrement", "Auto-increment")]),
    (
        "Personal",
        [
            ("firstName", "First name"),
            ("lastName", "Last name"),
            ("fullName", "Full name"),
            ("gender", "Gender"),
            ("age", "Age"),
            ("jobTitle", "Job title"),
            ("company", "Company"),
        ],
    ),
    (
        "Contact",
        [
            ("email", "Email"),
            ("phone", "Phone"),
            ("username", "Username"),
        ],
    ),
    (
        "Location",
        [
            ("streetAddress", "Street address"),
            ("address", "Full address"),
            ("city", "City"),
            ("state", "State"),
            ("zip", "Zip code"),
            ("country", "Country"),
            ("latitude", "Latitude"),
            ("longitude", "Longitude"),
        ],
    ),
    (
        "Internet",
        [
            ("url", "URL"),
            ("domain", "Domain"),
            ("ipv4", "IPv4"),
            ("macAddress", "MAC address"),
            ("color", "Hex color"),
        ],
    ),
    (
        "Commerce",
        [
            ("price", "Price"),
            ("product", "Product"),
            ("currency", "Currency"),
            ("creditCard", "Credit card"),
        ],
    ),
    ("Numbers", [("int", "Integer"), ("float", "Float"), ("bool", "Boolean")]),
    ("Dates", [("date", "Date"), ("datetime", "Datetime"), ("time", "Time")]),
    (
        "GovCon",
        [
            ("uei", "UEI"),
            ("cageCode", "CAGE code"),
            ("naics", "NAICS code"),
            ("psc", "PSC (product/service)"),
            ("piid", "PIID (contract no.)"),
        ],
    ),
    (
        "Text",
        [
            ("word", "Word"),
            ("sentence", "Sentence"),
            ("paragraph", "Paragraph"),
            ("pattern", "Pattern (custom code)"),
            ("enum", "Enum (custom)"),
            ("constant", "Constant"),
        ],
    ),
]


def field_type_groups():
    """Return the grouped, labelled type menu for building a dropdown:
    a list of (group_name, [(type_name, label), ...])."""
    return FIELD_TYPE_GROUPS
