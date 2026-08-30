"""
ChronoTrust-Med — Week 2: re-ingest with actual passage content + embeddings.

Week 1's ingest_guidelines.py only stored source-level metadata (title, url).
This script replaces that data with the same sources PLUS their real text,
chunked and embedded, so retrieval has something to actually match against.

It TRUNCATEs `passages` and `sources` first — safe to do, since Week 1's
2,008 rows were metadata-only test data anyway, not something to preserve.

Run:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...   # not needed for this script, but see generation/
    python ingestion/ingest_with_content.py --db-url postgresql://user@localhost/chronotrust --limit 300
"""

import argparse
from collections import Counter

import psycopg2
from datasets import load_dataset
from sentence_transformers import SentenceTransformer

GUIDELINE_SOURCES = {"nice", "cdc", "cma", "who", "cco", "icrc", "spor"}

# Chunk size in words. Small enough for good retrieval granularity,
# large enough to keep some context per chunk.
CHUNK_WORDS = 250
CHUNK_OVERLAP = 50


def chunk_text(text, chunk_words=CHUNK_WORDS, overlap=CHUNK_OVERLAP):
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_words
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--limit", type=int, default=300, help="cap rows per source (keep small — embedding takes time)")
    args = parser.parse_args()

    print("Loading embedding model (all-MiniLM-L6-v2, local, one-time download) ...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    print("Loading epfl-llm/guidelines ...")
    ds = load_dataset("epfl-llm/guidelines", split="train")

    conn = psycopg2.connect(args.db_url)
    cur = conn.cursor()

    print("Clearing previous Week 1 metadata-only rows (passages cascades automatically) ...")
    cur.execute("TRUNCATE sources RESTART IDENTITY CASCADE;")
    conn.commit()

    per_source_count = Counter()
    sources_inserted = 0
    passages_inserted = 0

    for row in ds:
        source_name = row.get("source")
        if source_name not in GUIDELINE_SOURCES:
            continue
        if per_source_count[source_name] >= args.limit:
            continue

        title = row.get("title")
        text = row.get("clean_text") or row.get("raw_text")
        if not title or not text:
            continue

        per_source_count[source_name] += 1

        cur.execute(
            """
            INSERT INTO sources (title, source_type, journal, publication_date, url, reliability_score)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING source_id
            """,
            (title[:500], "guideline", source_name, None, row.get("url"), 0.8),
        )
        source_id = cur.fetchone()[0]
        sources_inserted += 1

        chunks = chunk_text(text)
        if not chunks:
            continue
        embeddings = model.encode(chunks, show_progress_bar=False)

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                """
                INSERT INTO passages (source_id, chunk_index, chunk_text, embedding)
                VALUES (%s, %s, %s, %s)
                """,
                (source_id, i, chunk, emb.tolist()),
            )
            passages_inserted += 1

        if sources_inserted % 50 == 0:
            conn.commit()
            print(f"  ... {sources_inserted} sources / {passages_inserted} passages so far")

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone. Inserted {sources_inserted} sources and {passages_inserted} passages.")
    print("By source:", dict(per_source_count))


if __name__ == "__main__":
    main()
