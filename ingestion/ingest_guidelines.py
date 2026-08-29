"""
ChronoTrust-Med — Week 1-2: ingest the public clinical guidelines corpus.

Source: epfl-llm/guidelines on Hugging Face (~38K publicly redistributable
clinical practice guideline / medical text articles from wikidoc, nice,
pubmed, cdc, cma, who, spor, cco, icrc).

REVISED after Week 1 smoke test: this dataset's `source` field is lowercase
(not the title-case originally assumed), and — importantly — the dataset
carries NO publication-date field for any row, for any source. Titles and
URLs ARE populated for the guideline-body sources (nice, cdc, cma, who, cco,
icrc, spor), just not wikidoc/pubmed necessarily.

Consequence for the project: this corpus is a real, usable general evidence
pool for B0-B2 (plain RAG, GraphRAG), but it cannot supply real temporal
validity windows on its own — that would require scraping each source page
for a "last updated" date, which is out of scope for this ingestion pass.
Real temporal test cases come from the hand-curated ADA Standards of Care
yearly editions in docs/temporal_conflict_testset.csv instead. Rows loaded
here get publication_date = NULL and are excluded from B3+ temporal
filtering until/unless dates are added later.

Run:
    pip install -r ../requirements.txt
    python ingest_guidelines.py --db-url postgresql://user@localhost/chronotrust
"""

import argparse
from collections import Counter

import psycopg2
from datasets import load_dataset

GUIDELINE_SOURCES = {"nice", "cdc", "cma", "who", "cco", "icrc", "spor"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--limit", type=int, default=None, help="cap rows per source for a first test run")
    args = parser.parse_args()

    print("Loading epfl-llm/guidelines ...")
    ds = load_dataset("epfl-llm/guidelines", split="train")

    conn = psycopg2.connect(args.db_url)
    cur = conn.cursor()

    inserted = Counter()
    per_source_count = Counter()
    skipped_no_title = 0

    for row in ds:
        source_name = row.get("source")
        if source_name not in GUIDELINE_SOURCES:
            continue

        if args.limit and per_source_count[source_name] >= args.limit:
            continue
        per_source_count[source_name] += 1

        title = row.get("title")
        if not title:
            skipped_no_title += 1
            continue

        cur.execute(
            """
            INSERT INTO sources (title, source_type, journal, publication_date, url, reliability_score)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                title[:500],
                "guideline",
                source_name,
                None,
                row.get("url"),
                0.8,
            ),
        )
        inserted[source_name] += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"Inserted {sum(inserted.values())} sources total. Skipped {skipped_no_title} rows with no title.")
    print("By source:", dict(inserted))
    print("\nNote: publication_date is NULL for all of these — they feed B0-B2 (content pool),")
    print("not temporal filtering. Temporal test cases live in docs/temporal_conflict_testset.csv.")


if __name__ == "__main__":
    main()
