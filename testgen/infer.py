"""Schema inference: build a schema from something you already have.

Four ways in, all producing the same kind of schema the engine consumes
(a list of {name, type, ...options} dicts):

    parse_ddl(text)          from a CREATE TABLE statement
    from_csv_headers(text)   from a CSV header row
    infer_json_sample(text)  from a sample JSON object or array
    from_description(text)   from a plain-English description (regex rules, NO AI)

This is a Python port of the JavaScript in Toby's Fixtura prototype. Keeping it
in the engine (not the UI) means the CLI, the API, and the front end all share
the exact same inference. The name/type guessing is heuristic: it produces a
sensible starting schema that the user then refines.
"""

from __future__ import annotations

import json
import re

# SQL-type fallback (when the column name gave no clue). regex -> field type.
_SQL_RULES = [
    (r"int|serial|number|numeric|bigint|smallint", "int"),
    (r"decimal|float|double|real|money", "float"),
    (r"bool|bit", "bool"),
    (r"timestamp|datetime", "datetime"),
    (r"date", "date"),
    (r"time", "time"),
    (r"uuid|guid", "uuid"),
]


def sanitize(name):
    """Turn any label into a clean snake_case column name (or 'field')."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower())
    return cleaned.strip("_") or "field"


def guess_type(raw_name, sql_type=None):
    """Best-guess field type from a column name (and optional SQL type).

    Rules are checked top to bottom and the first match wins, so ORDER matters.
    Two ordering choices do most of the work:

    * Short, ambiguous cues ("age", "ip", "time", "type", "card") match on whole
      word TOKENS, not bare substrings, so they don't fire inside longer names
      ("agency", "shipping", "runtime", "prototype", "scorecard").
    * Counts are checked before money (so ``total_count`` is a tally, not
      dollars) and dates before booleans (so ``approved_at`` is a timestamp
      while a bare ``approved`` is a flag).

    The guess is only a starting point — every field stays editable in the UI.
    """
    name = str(raw_name).lower()
    sql = str(sql_type or "").lower()
    tokens = set(re.split(r"[^a-z0-9]+", name))

    def has(*xs):  # substring match — for distinctive, compound cues
        return any(x in name for x in xs)

    def word(*xs):  # whole-token match — for short, ambiguous cues
        return any(x in tokens for x in xs)

    # --- contact / identity ---
    if has("email", "e_mail"):
        return "email"
    if has("phone", "mobile", "fax") or word("tel"):
        return "phone"
    if has("username", "login") or word("handle"):
        return "username"
    if has("uuid", "guid") or "uuid" in sql:
        return "uuid"

    # --- people ---
    if (has("first") and has("name")) or name == "fname":
        return "firstName"
    if (has("last") and has("name")) or name in ("lname", "surname"):
        return "lastName"
    if has("full_name", "fullname", "display_name") or name == "name":
        return "fullName"
    # A "<person> name" column (contact_name, employee_name, poc_name, ...).
    if has("name") and word(
        "customer",
        "contact",
        "person",
        "employee",
        "recipient",
        "poc",
        "member",
        "applicant",
        "owner",
        "author",
        "user",
        "client",
        "representative",
        "official",
        "manager",
    ):
        return "fullName"
    if has("gender", "sex"):
        return "gender"
    if has("job", "occupation") or word("title", "role", "position"):
        return "jobTitle"

    # --- organizations (GovCon leans on these) ---
    if has(
        "subcontractor",
        "contractor",
        "company",
        "organization",
        "vendor",
        "supplier",
        "awardee",
        "offeror",
        "manufacturer",
        "employer",
    ) or word("org", "firm", "prime"):
        return "company"
    if has("agency", "department", "bureau", "directorate"):
        return "enum"  # GovCon agencies are a small fixed set; opts fill values

    # --- network identifiers BEFORE places, so "ip_address" / "mac_address"
    #     aren't swallowed by the "address" street rule below ---
    if has("ip_address", "ip_addr") or word("ip", "ipv4"):
        return "ipv4"
    if has("macaddress", "mac_addr", "mac_address") or word("mac"):
        return "macAddress"

    # --- GovCon identifiers (distinctive tokens; safe this early) ---
    if word("uei") or has("unique_entity"):
        return "uei"
    if has("cage"):
        return "cageCode"
    if has("naics"):
        return "naics"
    if word("psc") or has("product_service", "product_service_code"):
        return "psc"
    if has("piid"):
        return "piid"

    # --- places ---
    if has("street", "address"):
        return "streetAddress"
    if has("city", "town", "municipality"):
        return "city"
    if word("state", "province", "region"):
        return "state"
    if has("zip", "postal"):
        return "zip"
    if has("country", "nation"):
        return "country"
    if name == "lat" or has("latitude"):
        return "latitude"
    if name in ("lng", "lon") or has("longitude"):
        return "longitude"

    # --- web / network / misc typed ---
    if has("url", "website", "homepage") or word("link", "uri"):
        return "url"
    if has("domain"):
        return "domain"
    if has("color", "colour"):
        return "color"
    if has("currency"):
        return "currency"
    if has("creditcard", "credit_card", "debit_card") or word("card"):
        return "creditCard"

    # --- counts BEFORE money, so "total_count" is a tally, not dollars ---
    if word("qty", "quantity", "count", "number", "num", "rank"):
        return "int"
    if word("score", "rating", "votes", "views", "clicks"):
        return "int"
    if word("percent", "percentage", "rate", "ratio", "pct"):
        return "float"
    if "age" in tokens:
        return "age"

    # --- money ---
    if has(
        "price",
        "amount",
        "cost",
        "salary",
        "balance",
        "revenue",
        "budget",
        "subtotal",
        "obligation",
    ) or word("total", "fee", "fees", "wage", "charge", "charges", "spend", "fare"):
        return "price"

    # --- catalogue ---
    if word("product", "item", "items", "sku"):
        return "product"

    # --- dates / times BEFORE booleans, so "approved_at" is a timestamp ---
    if (
        has("created", "updated", "modified", "timestamp")
        or word("datetime")
        or name.endswith("_at")
    ):
        return "datetime"
    if has("birth", "dob") or word(
        "date", "expiry", "expiration", "deadline", "hired", "anniversary"
    ):
        return "date"
    if word("time"):
        return "time"

    # --- booleans ---
    if has("is_", "has_") or word(
        "active",
        "enabled",
        "disabled",
        "verified",
        "premium",
        "deleted",
        "approved",
        "eligible",
        "cancelled",
        "canceled",
        "completed",
        "expired",
        "locked",
        "archived",
        "published",
        "confirmed",
        "subscribed",
        "enrolled",
        "registered",
        "flagged",
    ):
        return "bool"

    # --- choices / enums ---
    if word(
        "status",
        "type",
        "category",
        "tier",
        "level",
        "stage",
        "kind",
        "priority",
        "severity",
        "classification",
    ):
        return "enum"

    # --- free text ---
    if has(
        "description",
        "comment",
        "note",
        "bio",
        "summary",
        "body",
        "message",
        "remarks",
        "justification",
        "abstract",
        "feedback",
    ):
        return "sentence"

    # --- identifiers / SQL-type fallback ---
    if name.endswith("_id") or name == "id" or word("id"):
        return "uuid" if "uuid" in sql else "int"
    for pattern, field_type in _SQL_RULES:
        if re.search(pattern, sql):
            return field_type
    return "word"


def default_opts(field_type, name=""):
    """Sensible starting options for the types that want them.

    `name` lets an enum pick context-appropriate values: an agency column gets
    real GovCon agencies rather than the generic status list.
    """
    lowered = str(name).lower()
    if field_type == "enum":
        if any(k in lowered for k in ("agency", "department", "bureau")):
            return {"values": "Dept of Defense, GSA, NASA, VA, DHS, DOE"}
        return {"values": "active, pending, closed"}
    if field_type == "age":
        return {"min": 18, "max": 80}
    if field_type == "price":
        return {"min": 1, "max": 5000}
    return {}


def _fields_from(pairs):
    """pairs: list of {"name": str, "sql_type"?: str, "type"?: str} -> fields.

    Each field is {name, type, ...default options for that type}, flattened the
    way the engine expects.
    """
    fields = []
    for p in pairs:
        name = sanitize(p["name"])
        field_type = p.get("type") or guess_type(p["name"], p.get("sql_type"))
        fields.append(
            {"name": name, "type": field_type, **default_opts(field_type, name)}
        )
    return fields


def parse_ddl(text):
    """Parse a CREATE TABLE statement into (table_name, fields)."""
    text = text or ""
    table_match = re.search(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?[\"`\[]?([a-z0-9_.]+)",
        text,
        re.IGNORECASE,
    )
    table = sanitize(table_match.group(1).split(".")[-1]) if table_match else None

    open_paren = text.find("(")
    close_paren = text.rfind(")")
    if open_paren < 0 or close_paren < 0:
        raise ValueError("Could not find a column list (no parentheses).")
    body = text[open_paren + 1 : close_paren]

    # Split on top-level commas only, so a type like DECIMAL(10,2) stays whole.
    parts, depth, current = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)

    pairs = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Skip table constraints (PRIMARY KEY, FOREIGN KEY, ...), not columns.
        if re.match(
            r"^(primary|foreign|constraint|unique|key|index|check)\b",
            part,
            re.IGNORECASE,
        ):
            continue
        m = re.match(r'^["`\[]?([a-z0-9_]+)["`\]]?\s+([a-z0-9_]+)', part, re.IGNORECASE)
        if m:
            pairs.append({"name": m.group(1), "sql_type": m.group(2)})
    if not pairs:
        raise ValueError("No columns recognized in the CREATE TABLE statement.")
    return table, _fields_from(pairs)


def from_csv_headers(text):
    """Build fields from the first non-empty line of CSV (the header row)."""
    line = next((ln for ln in str(text or "").splitlines() if ln.strip()), None)
    if not line:
        raise ValueError("No header row found.")
    heads = [h.strip().strip("\"'") for h in line.split(",")]
    heads = [h for h in heads if h]
    if not heads:
        raise ValueError("No column headers found.")
    return _fields_from([{"name": h} for h in heads])


def infer_json_sample(text):
    """Infer fields from a sample JSON object (or the first item of an array)."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("Invalid JSON.")
    obj = data[0] if isinstance(data, list) and data else data
    if not isinstance(obj, dict):
        raise ValueError("Expected a JSON object, or an array of objects.")
    pairs = []
    for key, value in obj.items():
        field_type = guess_type(key)
        if field_type == "word":  # name gave no clue; use the value's type
            if isinstance(value, bool):
                field_type = "bool"
            elif isinstance(value, int):
                field_type = "int"
            elif isinstance(value, float):
                field_type = "float"
        pairs.append({"name": key, "type": field_type})
    return _fields_from(pairs)


# Plain-English phrase -> column NAME. Regex, no AI.
#
# The field TYPE is deliberately NOT stored here — it's derived by
# guess_type(name) in _fields_from, exactly like the DDL/CSV/JSON tabs. That
# makes guess_type the single source of truth: any coverage or accuracy gain
# there flows to the Describe tab for free, and the two can't drift apart.
# Each name below is chosen so guess_type lands the intended type
# (e.g. "is_premium" -> bool, "signup_at" -> datetime, "amount" -> price).
_DESCRIBE_RULES = [
    (r"\bfirst name\b", "first_name"),
    (r"\blast name\b", "last_name"),
    (r"\b(full name|name)\b", "full_name"),
    (r"\b(e-?mail)\b", "email"),
    (r"\b(phone|mobile)\b", "phone"),
    (r"\b(username|handle|login)\b", "username"),
    (r"\b(gender|sex)\b", "gender"),
    (r"\b(job|title|role|position)\b", "job_title"),
    (r"\b(company|organization|employer|vendor|contractor)\b", "company"),
    (r"\b(agency|department|bureau)\b", "agency"),
    (r"(?<!ip )\b(address|street)\b", "address"),
    (r"\bcity\b", "city"),
    (r"\bstate\b", "state"),
    (r"\b(zip|postal)\b", "zip"),
    (r"\bcountry\b", "country"),
    (r"\blatitude\b", "latitude"),
    (r"\blongitude\b", "longitude"),
    (r"\b(url|website|link)\b", "website"),
    (r"\bdomain\b", "domain"),
    (r"\b(ip address|ip)\b", "ip_address"),
    (r"\bcolou?r\b", "color"),
    (r"\bcredit card\b", "credit_card"),
    (r"\bage\b", "age"),
    (r"\b(spend|balance|amount|salary|price|revenue|total|cost|budget)\b", "amount"),
    (r"\b(percent|percentage|ratio|rate)\b", "rate"),
    (r"\b(score|rating|votes|views)\b", "score"),
    (r"\b(quantity|count|number of)\b", "quantity"),
    (r"\b(product|item|sku)\b", "product"),
    (r"\b(signup|sign up|created|registration|register|join)\b", "signup_at"),
    (r"\b(birth|dob)\b", "birth_date"),
    (r"\b(premium|member|active|verified|subscrib)\b", "is_premium"),
    (r"\b(status|stage|tier|category|priority)\b", "status"),
    (r"\b(description|comment|note|bio|summary|remarks)\b", "description"),
    (r"\b(id|identifier)\b", "id"),
]


def from_description(text):
    """Build fields from a plain-English description using keyword rules.

    Each matched phrase contributes a column NAME; the field type is then
    derived by guess_type(name) inside _fields_from — the same guesser the
    DDL/CSV/JSON tabs use — so the Describe tab infers consistently with them.
    """
    lowered = str(text or "").lower()
    names, seen = [], set()
    for pattern, name in _DESCRIBE_RULES:
        if name not in seen and re.search(pattern, lowered):
            seen.add(name)
            names.append(name)
    # Always give it an id column, at the front.
    if "id" not in seen:
        names.insert(0, "id")
    return _fields_from([{"name": n} for n in names])
