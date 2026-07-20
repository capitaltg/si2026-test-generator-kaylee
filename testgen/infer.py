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

    A faithful linear port of the prototype's guessType: rules are checked top
    to bottom and the first match wins, so their ORDER matters. For example zip
    is checked before ip, so a "zip" column (which contains "ip") stays a zip
    code instead of becoming an IP address.
    """
    n = str(raw_name).lower()
    st = str(sql_type or "").lower()

    def has(*xs):
        return any(x in n for x in xs)

    if has("email"):
        return "email"
    if has("phone", "mobile", "tel"):
        return "phone"
    if n in ("uuid", "guid") or "uuid" in st:
        return "uuid"
    if (has("first") and has("name")) or n == "fname":
        return "firstName"
    if (has("last") and has("name")) or n in ("lname", "surname"):
        return "lastName"
    if has("username", "login", "handle"):
        return "username"
    if has("full_name", "fullname", "display_name") or n == "name":
        return "fullName"
    if has("company", "organization", "org", "vendor"):
        return "company"
    if has("title", "role", "position", "job"):
        return "jobTitle"
    if has("gender", "sex"):
        return "gender"
    if has("street", "address"):
        return "streetAddress"
    if has("city", "town"):
        return "city"
    if has("state", "province", "region"):
        return "state"
    if has("zip", "postal"):
        return "zip"
    if has("country", "nation"):
        return "country"
    if n == "lat" or has("latitude"):
        return "latitude"
    if n in ("lng", "lon") or has("longitude"):
        return "longitude"
    if has("ip"):
        return "ipv4"
    if has("mac"):
        return "macAddress"
    if has("url", "link", "website", "homepage"):
        return "url"
    if has("domain"):
        return "domain"
    if has("color", "colour"):
        return "color"
    if has("currency"):
        return "currency"
    if has("card"):
        return "creditCard"
    if has(
        "price",
        "amount",
        "cost",
        "salary",
        "balance",
        "total",
        "revenue",
        "spend",
        "fee",
    ):
        return "price"
    if has("age"):
        return "age"
    if has("qty", "quantity", "count", "number", "num"):
        return "int"
    if has("product", "item", "sku"):
        return "product"
    if has(
        "active", "enabled", "verified", "premium", "deleted", "is_", "has_", "flag"
    ):
        return "bool"
    if has("status", "state", "type", "category", "tier", "level", "stage"):
        return "enum"
    if has("created", "updated", "timestamp", "_at", "datetime"):
        return "datetime"
    if has("date", "dob", "birth"):
        return "date"
    if has("time"):
        return "time"
    if has("description", "comment", "note", "bio", "summary", "body"):
        return "sentence"
    if n.endswith("_id") or n == "id":
        return "uuid" if "uuid" in st else "int"
    for pattern, field_type in _SQL_RULES:
        if re.search(pattern, st):
            return field_type
    return "word"


def default_opts(field_type):
    """Sensible starting options for the types that want them."""
    if field_type == "enum":
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
        fields.append({"name": name, "type": field_type, **default_opts(field_type)})
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


# Plain-English phrase -> (column name, field type). Regex, no AI.
_DESCRIBE_RULES = [
    (r"\b(full name|name)\b", "full_name", "fullName"),
    (r"\bfirst name\b", "first_name", "firstName"),
    (r"\blast name\b", "last_name", "lastName"),
    (r"\b(e-?mail)\b", "email", "email"),
    (r"\b(phone|mobile)\b", "phone", "phone"),
    (r"\b(address|street)\b", "address", "streetAddress"),
    (r"\bcity\b", "city", "city"),
    (r"\bstate\b", "state", "state"),
    (r"\b(zip|postal)\b", "zip", "zip"),
    (r"\bcountry\b", "country", "country"),
    (r"\b(company|organization|employer)\b", "company", "company"),
    (r"\b(job|title|role|position)\b", "job_title", "jobTitle"),
    (r"\b(age)\b", "age", "age"),
    (r"\b(spend|balance|amount|salary|price|revenue|total|cost)\b", "amount", "price"),
    (r"\b(quantity|count|number of)\b", "quantity", "int"),
    (r"\b(signup|created|registration|join)\b", "signup_date", "datetime"),
    (r"\b(birth|dob)\b", "birth_date", "date"),
    (r"\b(premium|member|active|verified|subscrib)\b", "is_premium", "bool"),
    (r"\b(status|stage|tier|category)\b", "status", "enum"),
    (r"\b(username|handle|login)\b", "username", "username"),
    (r"\b(url|website|link)\b", "website", "url"),
    (r"\b(product|item)\b", "product", "product"),
    (r"\b(id|identifier)\b", "id", "uuid"),
]


def from_description(text):
    """Build fields from a plain-English description using keyword rules."""
    lowered = str(text or "").lower()
    found, seen = [], set()
    for pattern, name, field_type in _DESCRIBE_RULES:
        if re.search(pattern, lowered) and name not in seen:
            seen.add(name)
            found.append({"name": name, "type": field_type})
    # Always give it an id column, at the front.
    if not any(f["name"] == "id" for f in found):
        found.insert(0, {"name": "id", "type": "uuid"})
    return _fields_from(found)
