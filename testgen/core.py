"""The engine. All real work lives here.

Nothing in this file knows about "GovCon", "award PDFs", or any specific use
case. It takes a *description* of the data you want (a schema) and produces it.
Presets and document templates (added in later tickets) sit on top of this
generic core, so the engine stays reusable for any dataset or database.
"""
from __future__ import annotations

import random

from faker import Faker

from .fields import FIELD_TYPES


def generate(schema, *, rows=10, seed=None):
    """Generate fake data rows from a schema description.

    This is the single source of truth for generation. Both the CLI and any
    Python caller funnel through here, so the behavior is identical no matter
    which door you came in.

    Parameters
    ----------
    schema:
        A list of field specs describing the columns to generate, e.g.
        [{"name": "vendor", "type": "company"},
         {"name": "amount", "type": "int", "min": 100, "max": 500}]
    rows:
        How many records to produce.
    seed:
        A starting number for the randomness. The same seed with the same schema
        always produces the same output, which is what makes this tool
        reproducible rather than "AI-flavored random". Leave it None for
        different data every run.

    Returns
    -------
    list[dict]
        One dict per row, keyed by field name.
    """
    if not schema:
        raise ValueError(
            "generate() needs a schema: a list of field specs like "
            "[{'name': 'vendor', 'type': 'company'}]."
        )
    _validate_schema(schema)

    # Seed both sources of randomness from the one seed. rng handles numbers,
    # choices, and dates; faker handles realistic text. Seeding both is what
    # makes the whole run reproducible.
    rng = random.Random(seed)
    faker = Faker()
    if seed is not None:
        faker.seed_instance(seed)

    result = []
    for index in range(rows):
        row = {}
        for field in schema:
            make_value = FIELD_TYPES[field["type"]]
            row[field["name"]] = make_value(field, index, rng, faker)
        result.append(row)
    return result


def _validate_schema(schema):
    """Fail early with a clear message if a field spec is malformed, rather than
    letting it blow up cryptically deep in generation."""
    for field in schema:
        if "name" not in field:
            raise ValueError(f"Every field needs a 'name': {field}")
        if "type" not in field:
            raise ValueError(f"Field '{field.get('name')}' needs a 'type'.")
        if field["type"] not in FIELD_TYPES:
            valid = ", ".join(sorted(FIELD_TYPES))
            raise ValueError(
                f"Unknown field type '{field['type']}' for field "
                f"'{field['name']}'. Valid types are: {valid}."
            )
