"""
ChronoTrust-Med — Week 2b: ingest Cochrane systematic reviews from Europe PMC.

epfl-llm/guidelines has no publication dates at all, which blocks the
temporal-filtering baseline (B3). Europe PMC's REST API is free, needs no
API key, and every record has a real publication date — so this replaces
that corpus with Cochrane Database of Systematic Reviews records instead.

Matches the schema/insert style of ingest_with_content.py:
  sources(title, source_type, journal, publication_date, url, reliability_score) -> source_id
  passages(source_id, chunk_index, chunk_text, embedding)

This ADDS to whatever is already in `sources`/`passages` — it does not
TRUNCATE. Run ingest_with_content.py first (or not at all) as you prefer.

Run:
    pip install requests sentence-transformers psycopg2-binary
    python ingestion/ingest_europepmc.py --db-url postgresql://user@localhost/chronotrust --limit 300
"""

import argparse
import time

import psycopg2
import requests
from sentence_transformers import SentenceTransformer

EUROPEPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
QUERY = 'JOURNAL:"Cochrane Database Syst Rev" AND HAS_ABSTRACT:Y'
PAGE_SIZE = 100

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


def fetch_page(cursor_mark):
    params = {
        "query": QUERY,
        "format": "json",
        "pageSize": PAGE_SIZE,
        "cursorMark": cursor_mark,
        "resultType": "core",  # needed to get abstractText
    }
    resp = requests.get(EUROPEPMC_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--limit", type=int, default=300, help="total records to ingest")
    args = parser.parse_args()

    print("Loading embedding model (all-MiniLM-L6-v2, local) ...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    conn = psycopg2.connect(args.db_url)
    cur = conn.cursor()

    sources_inserted = 0
    passages_inserted = 0
    skipped_no_abstract = 0
    skipped_no_date = 0
    cursor_mark = "*"

    print(f"Querying Europe PMC: {QUERY!r}")

    while sources_inserted < args.limit:
        data = fetch_page(cursor_mark)
        results = data.get("resultList", {}).get("result", [])
        if not results:
            print("No more results from Europe PMC.")
            break

        for row in results:
            if sources_inserted >= args.limit:
                break

            abstract = row.get("abstractText")
            if not abstract:
                skipped_no_abstract += 1
                continue

            # Prefer the full first-publication date; fall back to pubYear-01-01.
            pub_date = row.get("firstPublicationDate")
            if not pub_date:
                pub_year = row.get("pubYear")
                if not pub_year:
                    skipped_no_date += 1
                    continue
                pub_date = f"{pub_year}-01-01"

            title = row.get("title") or "(untitled)"
            doi = row.get("doi")
            url = f"https://doi.org/{doi}" if doi else row.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url")

            cur.execute(
                """
                INSERT INTO sources (title, source_type, journal, publication_date, url, reliability_score)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING source_id
                """,
                (title[:500], "systematic_review", "Cochrane Database of Systematic Reviews", pub_date, url, 0.9),
            )
            source_id = cur.fetchone()[0]
            sources_inserted += 1

            chunks = chunk_text(abstract)
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

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor_mark:
            print("Reached end of result set.")
            break
        cursor_mark = next_cursor
        time.sleep(0.34)  # be polite to the free API (~3 req/sec)

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone. Inserted {sources_inserted} sources and {passages_inserted} passages.")
    print(f"Skipped (no abstract): {skipped_no_abstract}, skipped (no date): {skipped_no_date}")


if __name__ == "__main__":
    main()
