# Fixtura — Roadmap

Fixtura generates realistic, **deterministic (seeded), self-contained** GovCon test
data — CSV / SQL / SQLite / JSON / PDF and fillable federal forms — so other tools
(e.g. Runway) have believable data to develop and demo against. No AI, no external
services, no network calls at generation time.

This roadmap is the single source of truth for where the tool is headed. Each item
links to its tracking issue.

---

## ✅ Shipped

- **Seeded engine** with ~40 field types, per-field null %, weighted choices, and
  nested `list`/`subtable` records.
- **Schema inference from 4 sources** — DDL (`CREATE TABLE`), CSV headers, JSON
  sample, and plain-English Describe — all routed through one `guess_type`.
- **Exports** — CSV, SQL, SQLite, JSON, and PDF (table + document styles).
- **Fillable federal forms** — SF-1449 / SF-1034 / SF-30, box-filled and watermarked
  as simulated test data.
- **GovCon presets** — internally-consistent contract scenarios (CLIN math, LCAT
  loaded rates, ceiling roll-ups, seed-locked coherence).
- **Template gallery** — save / restore / rename / delete / duplicate-guard, grouped
  by kind, plus **share-a-setup-via-URL**.
- **CSV → custom template** — infer a schema from a CSV and save it to *My Templates*
  (the save path is surfaced from every input tab).

## 🔨 Now

- **Expose GovCon identifier field types in the Builder** — UEI, CAGE, NAICS, PSC,
  PIID as first-class dropdown types (generators already exist inside the presets;
  this lifts them into the shared engine and teaches inference to recognize them).
  → [#39](https://github.com/capitaltg/si2026-test-generator-kaylee/issues/39)

## 🗄️ Backlog

Ordered by value-for-effort. Both came out of a competitive-analysis pass against
Mockaroo / Faker / Tonic / Gretel — the two capabilities those tools have that fit
Fixtura's deterministic, GovCon-focused niche.

- **Derived / formula fields** — let a Builder field be computed from siblings
  (`line_total = quantity * unit_price`, `loaded_rate = salary/2080*2.2`), with a
  safe (no-`eval`) evaluator. Brings preset-style internal consistency to custom
  schemas. → [#37](https://github.com/capitaltg/si2026-test-generator-kaylee/issues/37)
- **Multi-table relational output with foreign keys** — generate related tables
  (`vendors → contracts → clins → invoices`) with valid FKs and cardinality controls;
  export joinable SQL/SQLite/CSV/nested-JSON. Biggest structural change; validate the
  existing nested `list`/`subtable` doesn't already cover demo needs first.
  → [#38](https://github.com/capitaltg/si2026-test-generator-kaylee/issues/38)

## 🚫 Out of scope (deliberate)

Choices, not gaps — these conflict with Fixtura's deterministic / self-contained design:

- **AI statistical modeling of real distributions** (Tonic / Gretel territory).
- **Live-database modeling** (point-at-prod, "Live Connect").
- **REST API mocking / served endpoints** (Mockaroo API, MockHero).
- **Natural-language schema description via an LLM** — cut ([#16]); the deterministic
  Describe tab covers plain-English schemas without AI.
- **Scanned-look PDF + OCR path** — cut ([#6]); clean PDF export covers the sample need.

[#16]: https://github.com/capitaltg/si2026-test-generator-kaylee/issues/16
[#6]: https://github.com/capitaltg/si2026-test-generator-kaylee/issues/6
