"""
ChronoTrust-Med — Week 2: retrieval (the "R" in RAG for baseline B1).

Given a question, embeds it with the same local model used at ingestion time
and returns the top-k most similar passages via pgvector cosine distance.
"""

import psycopg2
from sentence_transformers import SentenceTransformer

_model = None


def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def retrieve(query, db_url, top_k=5):
    """Return top_k passages as a list of dicts: {chunk_text, title, url, journal, similarity}."""
    model = get_model()
    query_embedding = model.encode(query).tolist()

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.chunk_text, s.title, s.url, s.journal, 1 - (p.embedding <=> %s::vector) AS similarity
        FROM passages p
        JOIN sources s ON s.source_id = p.source_id
        ORDER BY p.embedding <=> %s::vector
        LIMIT %s
        """,
        (query_embedding, query_embedding, top_k),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {"chunk_text": r[0], "title": r[1], "url": r[2], "journal": r[3], "similarity": float(r[4])}
        for r in rows
    ]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    results = retrieve(args.query, args.db_url, args.top_k)
    for i, r in enumerate(results, 1):
        print(f"\n--- Result {i} (similarity {r['similarity']:.3f}) ---")
        print(f"Source: {r['title']}  [{r['journal']}]")
        print(f"URL: {r['url']}")
        print(r["chunk_text"][:300] + "...")
