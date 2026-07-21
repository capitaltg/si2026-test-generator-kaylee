# P7: Finalized SF-1449 sample design

## Purpose

Runway is intended to manage active contracts and their designated funding after
the award. The SF-1449 sample therefore represents a finalized, accepted
contract record rather than an unissued award form.

## Scope

Extend the Contract Award (SF-1449) preset with internally consistent
government-acceptance data and map it to the existing second-page AcroForm
fields.

The generated contract record will gain a designated government representative:

- name and title
- office mailing address
- phone number and email address
- acceptance date, on or after the award date and no later than today

The SF-1449 mapping will mark the received, inspected, and accepted status and
fill the authorized representative's printed name, title, contact information,
and acceptance date. It will leave signatures blank: a generated signature is
not necessary for Runway's document-ingestion tests and is less appropriate
than populated identity and acceptance metadata.

The page-two continuation line-item grid remains blank whenever the generated
base-year CLINs fit on page one; it is an unused continuation area, not missing
data. Finance-only voucher, payment, certifying-officer, check-number, and
stock-record fields also remain blank because they belong to later payment
processing, which is outside this award/acceptance document's lifecycle stage.

## Implementation boundaries

`testgen/presets.py` remains the single source for generated contract facts and
AcroForm mappings. `testgen/formfill.py` continues to fill only existing PDF
fields; no form layout is redrawn or altered.

## Validation

Tests will verify that the generated acceptance date is in range and that the
SF-1449 mapping contains each completed-acceptance field. A rendered PDF will
be checked visually to confirm the page-two representative section is legible,
the correct status boxes are marked, and the simulated-data footer remains
visible.
