# ChronoTrust-Med — Knowledge Graph Ontology (v0.1, Week 1 lock-in)

Node types, edge types, and required properties. Keep this file as the single
source of truth — the ingestion scripts and the graph DB schema should both be
generated from this, not maintained separately.

## Node types

| Node | Key properties |
|---|---|
| `Disease` | name, icd10_code (optional) |
| `Treatment` | name, drug_class (optional) |
| `Condition` | name (used for contraindications, comorbidities) |
| `Population` | description (e.g. "adults with eGFR < 30", "pediatric") |
| `Claim` | claim_id (FK to `medical_claims.claim_id`), text |
| `Source` | source_id (FK to `sources.source_id`), title, publication_date |
| `TimePeriod` | valid_from, valid_until |

## Edge types

| Edge | From → To | Notes |
|---|---|---|
| `recommended_for` | Treatment → Disease | core recommendation edge |
| `contraindicated_for` | Treatment → Condition | |
| `supported_by` | Claim → Source | |
| `contradicted_by` | Claim → Source | tag with `conflict_type`: `evolution` \| `contradiction` (see below) |
| `applies_to` | Claim → Population | |
| `valid_during` | Claim → TimePeriod | |
| `published_on` | Source → (date literal) | denormalized copy of `Source.publication_date` for fast graph queries |
| `supersedes` | Source → Source | later edition of the same guideline; used to auto-derive `evolution` conflicts |

## The `conflict_type` decision rule (the project's novel mechanism)

Applied whenever two `Claim` nodes about the same `Treatment`/`Disease`/`Population`
triple disagree:

1. Look up each claim's `Source.publication_date` and `TimePeriod`.
2. If one source `supersedes` the other (or is meaningfully later, same guideline
   family) → `conflict_type = evolution`. Prefer the later, higher-reliability claim;
   surface both with a "recommendation changed" note.
3. If the sources are contemporaneous (no supersession relation, overlapping validity
   windows) and still disagree → `conflict_type = contradiction`. Do **not**
   auto-resolve — route to the abstention/warning path.

This rule is implemented once, in `ingestion/conflict_rule.py` (Week 4), and is
the thing the ablation study removes to measure its effect (Section 13 of the
proposal / evaluation plan).
