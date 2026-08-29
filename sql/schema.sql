-- ChronoTrust-Med — Core SQL schema
-- Postgres. Run this once against a fresh database before any ingestion.
-- Matches Section 8 of the project proposal + Week 1 lock-in.

CREATE TABLE IF NOT EXISTS sources (
    source_id           SERIAL PRIMARY KEY,
    title               TEXT NOT NULL,
    authors             TEXT,
    publication_date    DATE,               -- when this specific version/edition was published
    source_type         TEXT NOT NULL,      -- 'guideline', 'rct', 'systematic_review', 'observational', 'other'
    journal             TEXT,
    doi                 TEXT,
    url                 TEXT,
    reliability_score   NUMERIC(4,3),       -- 0.000-1.000, heuristic weight used in provenance ranking
    superseded_by        INTEGER REFERENCES sources(source_id),  -- self-reference: later edition of the same guideline
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS medical_claims (
    claim_id            SERIAL PRIMARY KEY,
    claim_text          TEXT NOT NULL,
    valid_from          DATE,               -- NULL = unknown/open start
    valid_until         DATE,               -- NULL = still current
    source_id           INTEGER NOT NULL REFERENCES sources(source_id),
    evidence_level      TEXT,               -- e.g. GRADE A/B/C, or RCT/observational/expert-opinion
    population          TEXT,               -- who the claim applies to (age range, comorbidity, etc.)
    confidence          NUMERIC(4,3),
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- Relationships between claims: supports, contradicts, supersedes, applies_to_subset_of, etc.
CREATE TABLE IF NOT EXISTS claim_relations (
    relation_id         SERIAL PRIMARY KEY,
    claim_id            INTEGER NOT NULL REFERENCES medical_claims(claim_id),
    related_claim_id    INTEGER NOT NULL REFERENCES medical_claims(claim_id),
    relation_type       TEXT NOT NULL,      -- 'contradicts_evolution', 'contradicts_genuine', 'supports', 'supersedes'
    confidence          NUMERIC(4,3),
    CHECK (claim_id <> related_claim_id)
);

CREATE TABLE IF NOT EXISTS verification_results (
    result_id           SERIAL PRIMARY KEY,
    claim_id            INTEGER NOT NULL REFERENCES medical_claims(claim_id),
    temporal_status      TEXT,              -- 'valid', 'outdated', 'not_yet_valid', 'unknown'
    support_status       TEXT,              -- 'supported', 'refuted', 'nei'
    conflict_status       TEXT,             -- 'none', 'evolution', 'contradiction'
    verification_score    NUMERIC(4,3),
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS queries (
    query_id            SERIAL PRIMARY KEY,
    user_question       TEXT NOT NULL,
    requested_date       DATE,              -- the "as of" date the user is asking about (defaults to today if unspecified)
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    experiment_id        SERIAL PRIMARY KEY,
    system_version        TEXT NOT NULL,    -- 'B0', 'B1', 'B2', 'B3', 'B4', or 'B4_no_temporal', etc. for ablations
    factuality            NUMERIC(5,2),
    citation_accuracy      NUMERIC(5,2),
    outdated_claim_rate     NUMERIC(5,2),
    contradiction_rate      NUMERIC(5,2),
    abstention_precision    NUMERIC(5,2),
    notes                TEXT,
    run_at               TIMESTAMPTZ DEFAULT now()
);

-- Helpful indexes for the temporal-filtering hot path
CREATE INDEX IF NOT EXISTS idx_claims_validity ON medical_claims (valid_from, valid_until);
CREATE INDEX IF NOT EXISTS idx_claims_source ON medical_claims (source_id);
CREATE INDEX IF NOT EXISTS idx_sources_pubdate ON sources (publication_date);
