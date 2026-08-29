# ChronoTrust-Med — Execution Tracker

Temporal and Provenance-Aware Medical RAG with Claim-Level Conflict Verification.
Use this file as the running checklist — tick items off as you go, and add dated
notes under each week so your guide can see actual progress, not just the plan.

## Week 1 — Lock the schema, verify citations, pick the corpus subset

- [ ] Stand up Postgres, run `sql/schema.sql`
- [ ] Review `kg/ontology.md`, adjust node/edge properties if needed, then treat it as frozen
- [ ] Re-verify the 3 unconfirmed citations (ET-RAG, "Provenance Gap in Clinical AI",
      "When Evidence Contradicts") against their actual PDFs/abstracts — fix or drop any
      that don't resolve, in the literature survey doc
- [ ] Decide the exact guideline sub-corpus to ingest (start with WHO + NICE + CDC —
      they have the cleanest per-article dates in `epfl-llm/guidelines`)
- [ ] Get API access sorted: Hugging Face (dataset), NCBI E-utilities (PubMed), and your
      chosen embedding model / LLM endpoint

## Week 2 — B0 + B1: working end-to-end demo

- [ ] Run `ingestion/ingest_guidelines.py --limit 2000` as a first smoke test, inspect
      what actually landed in `sources`
- [ ] Build the embedding index (sentence-transformers + pgvector or FAISS) over the
      ingested passages
- [ ] Wire up plain retrieve → generate (B1) — this is your first real, demoable answer
- [ ] Implement B0 (LLM-only, no retrieval) purely as an evaluation baseline
- [ ] Start filling in `docs/temporal_conflict_testset.csv` with real pairs found while
      reading the corpus (target: 15-20 rows by end of week)

## Week 3 — B2 + B3: graph and temporal filtering

- [ ] Populate the KG from `kg/ontology.md` for whatever slice of the corpus you've
      ingested (start narrow — e.g. just diabetes treatments — and widen later)
- [ ] Add graph-based retrieval alongside vector search (B2)
- [ ] Add `valid_from`/`valid_until` filtering against `queries.requested_date` (B3)
- [ ] Run first factuality / outdated-evidence measurements, log them into
      `evaluation_results`

## Week 4 — Claim verification, conflict classification, trust score

- [ ] Add claim extraction (decompose a generated answer into atomic claims)
- [ ] Add temporal + evidence checks per claim → `verification_results`
- [ ] Implement `ingestion/conflict_rule.py`: the evolution-vs-contradiction rule
      described in `kg/ontology.md`
- [ ] First version of the trust score (simple, interpretable weighted rubric — not
      a learned model yet)
- [ ] Validate the conflict rule against `docs/temporal_conflict_testset.csv`
- [ ] Prepare the mid-term presentation: B0→B3 results table + a live or recorded
      demo of the conflict classifier on 2-3 of the test-set cases

## After mid-term (for the final report)

- [ ] Provenance ranking refinements
- [ ] Abstention path
- [ ] Full ablation study (remove each of temporal / provenance / KG / conflict /
      abstention one at a time, re-measure)
- [ ] Five-view web app: query/answer dashboard, claim-verification panel, evidence
      timeline, provenance/KG explorer, evaluation/trust-score dashboard

---

## Repo layout

```
chronotrust-med/
├── sql/schema.sql              # Postgres schema (Week 1)
├── kg/ontology.md              # KG node/edge types + the conflict rule (Week 1)
├── ingestion/
│   └── ingest_guidelines.py    # loads epfl-llm/guidelines into `sources` (Week 1-2)
├── docs/
│   └── temporal_conflict_testset.csv  # hand-labeled eval set (Week 2, growing weekly)
└── requirements.txt
```
