"""Tests for the PDF writers (testgen/pdf.py).

fpdf2 is a core dependency, so these run as part of the normal suite. We assert
on the raw PDF bytes rather than pulling in a PDF parser: every PDF starts with
"%PDF" and ends with "%%EOF", and the number of pages shows up as a "/Count N"
entry in the page tree — enough to check the shape without extra tooling.
"""

from __future__ import annotations

import re

import pytest

from testgen.pdf import to_pdf_docs_bytes, to_pdf_table_bytes

FIELDS = [{"name": "contract_id", "type": "x"}, {"name": "vendor", "type": "x"}]
ROWS = [
    {"contract_id": "GS-1000", "vendor": "Acme Corp"},
    {"contract_id": "GS-1001", "vendor": "Beta LLC"},
    {"contract_id": "GS-1002", "vendor": "Gamma Inc"},
]


def _is_pdf(data):
    return (
        isinstance(data, (bytes, bytearray))
        and data[:5] == b"%PDF-"
        and b"%%EOF" in data
    )


def _page_count(data):
    counts = re.findall(rb"/Count\s+(\d+)", data)
    return int(counts[0]) if counts else 0


def test_table_pdf_is_a_real_pdf():
    data = to_pdf_table_bytes(FIELDS, ROWS)
    assert _is_pdf(data)
    # A short table fits on a single page.
    assert _page_count(data) == 1


def test_docs_pdf_makes_one_page_per_row():
    data = to_pdf_docs_bytes(FIELDS, ROWS)
    assert _is_pdf(data)
    assert _page_count(data) == len(ROWS)


def test_docs_pdf_respects_the_page_cap():
    # One page per row means the row count is the page count; going over the
    # cap should be a clear error, not a giant document.
    with pytest.raises(ValueError):
        to_pdf_docs_bytes(FIELDS, ROWS, max_pages=2)


def test_docs_pdf_with_no_rows_is_still_valid():
    data = to_pdf_docs_bytes(FIELDS, [])
    assert _is_pdf(data)


def test_table_pdf_with_no_rows_is_still_valid():
    data = to_pdf_table_bytes(FIELDS, [])
    assert _is_pdf(data)


def test_pdf_survives_non_latin1_values():
    # Generated data can contain anything; unsupported characters must not crash
    # the export (they are substituted, not fatal).
    rows = [{"contract_id": "GS-1", "vendor": "Acmé Corp™ — 日本"}]
    assert _is_pdf(to_pdf_table_bytes(FIELDS, rows))
    assert _is_pdf(to_pdf_docs_bytes(FIELDS, rows))


def test_docs_config_drives_layout_without_crashing():
    config = {
        "title": "Contract Award Notice",
        "title_field": "contract_id",
        "header_fields": ["contract_id"],
        "body_fields": ["vendor"],
    }
    data = to_pdf_docs_bytes(FIELDS, ROWS, config=config)
    assert _is_pdf(data)
    assert _page_count(data) == len(ROWS)


def test_column_order_falls_back_to_row_keys_without_fields():
    # No schema passed: the writer should still work off the row dict's keys.
    data = to_pdf_table_bytes([], ROWS)
    assert _is_pdf(data)


def test_docs_prose_template_renders_one_page_per_row():
    config = {
        "title": "Award",
        "body_template": "Contract {contract_id} awarded to {vendor}.",
    }
    data = to_pdf_docs_bytes(FIELDS, ROWS, config=config)
    assert _is_pdf(data)
    assert _page_count(data) == len(ROWS)


def test_docs_prose_template_tolerates_unknown_placeholder():
    # An unknown {token} must not crash; it is left in place, not fatal.
    config = {"body_template": "Hello {vendor}, ref {does_not_exist}."}
    assert _is_pdf(to_pdf_docs_bytes(FIELDS, ROWS, config=config))


def test_docs_form_style_renders_one_page_per_row():
    config = {
        "title": "Contract Award",
        "style": "form",
        "header_fields": ["contract_id"],
        "body_fields": ["vendor"],
    }
    data = to_pdf_docs_bytes(FIELDS, ROWS, config=config)
    assert _is_pdf(data)
    assert _page_count(data) == len(ROWS)


def test_docs_form_style_works_with_no_header_fields():
    # Form should render even if nothing is assigned to the header boxes.
    data = to_pdf_docs_bytes(
        FIELDS, ROWS, config={"style": "form", "header_fields": []}
    )
    assert _is_pdf(data)
