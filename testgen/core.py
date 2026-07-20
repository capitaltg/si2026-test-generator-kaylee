"""The engine. All real work lives here.

Nothing in this file knows about "GovCon", "award PDFs", or any specific use
case. It takes a *description* of the data you want and produces it. Presets
and document templates (added in later tickets) sit on top of this generic
core, so the engine stays reusable for any dataset or database.
"""
from __future__ import annotations


def generate(schema=None, *, rows=10, seed=None):
    """Generate fake data from a schema description.

    This is the single source of truth for generation. Both the CLI and any
    Python caller funnel through here, so the behavior is identical no matter
    which door you came in.

    Parameters
    ----------
    schema:
        A description of the fields to generate. Not used yet (Ticket 2 fills
        this in); accepted now so the interface is stable from the start.
    rows:
        How many records to produce.
    seed:
        A starting number for the randomness. The same seed always produces the
        same output, which is what makes this tool reproducible rather than
        "AI-flavored random". Not wired up yet (Ticket 2).

    Returns
    -------
    dict
        For now, a scaffold echo describing what was requested. Later tickets
        return the actual generated data.
    """
    return {
        "status": "scaffold-ok",
        "schema": schema,
        "rows": rows,
        "seed": seed,
    }
