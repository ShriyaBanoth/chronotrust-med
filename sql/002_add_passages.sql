-- ChronoTrust-Med — Week 2 migration: add pgvector + a passages table.
--
-- Why: `sources` (from Week 1) only stores bibliographic metadata (title,
-- journal, dates). RAG retrieval needs the actual chunked article TEXT with
-- an embedding per chunk, so a query can be matched against real content,
-- not just a title. This adds that missing piece without touching Week 1's
-- schema.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS passages (
    passage_id      SERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,          -- position of this chunk within its source article
    chunk_text      TEXT NOT NULL,
    embedding       vector(384),               -- all-MiniLM-L6-v2 output dimension
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_id, chunk_index)
);

-- Approximate nearest-neighbor index for fast cosine similarity search.
-- ivfflat needs the table to have some rows before this helps much;
-- fine to create now, it'll just do a sequential scan until you have
-- more data, then speed up automatically as it grows.
CREATE INDEX IF NOT EXISTS idx_passages_embedding
    ON passages USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_passages_source ON passages (source_id);
