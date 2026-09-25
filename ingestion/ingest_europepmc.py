"""
ChronoTrust-Med — Week 2b: ingest Cochrane systematic reviews from Europe PMC.

Europe PMC provides publication dates needed for temporal filtering (B3).

This script:
1. Fetches Cochrane systematic reviews from Europe PMC.
2. Skips records that are already present in the database.
3. Inserts genuinely new sources.
4. Chunks abstracts.
5. Generates embeddings.
6. Stores passages in PostgreSQL.

It does NOT TRUNCATE existing sources or passages.

Run:

    pip install requests sentence-transformers psycopg2-binary

    python ingestion/ingest_europepmc.py \
        --db-url postgresql://localhost/chronotrust \
        --limit 1
"""

import argparse
import time

import psycopg2
import requests
from sentence_transformers import SentenceTransformer


# ============================================================
# CONFIGURATION
# ============================================================

EUROPEPMC_BASE = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
)

QUERY = 'JOURNAL:"Cochrane Database Syst Rev" AND HAS_ABSTRACT:Y'

PAGE_SIZE = 100

CHUNK_WORDS = 250
CHUNK_OVERLAP = 50

JOURNAL_NAME = "Cochrane Database of Systematic Reviews"


# ============================================================
# TEXT CHUNKING
# ============================================================

def chunk_text(
    text,
    chunk_words=CHUNK_WORDS,
    overlap=CHUNK_OVERLAP,
):
    words = text.split()

    if not words:
        return []

    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_words

        chunks.append(
            " ".join(words[start:end])
        )

        if end >= len(words):
            break

        start = end - overlap

    return chunks


# ============================================================
# EUROPE PMC API
# ============================================================

def fetch_page(cursor_mark):
    params = {
        "query": QUERY,
        "format": "json",
        "pageSize": PAGE_SIZE,
        "cursorMark": cursor_mark,
        "resultType": "core",
    }

    response = requests.get(
        EUROPEPMC_BASE,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# DUPLICATE CHECK
# ============================================================

def source_already_exists(
    cur,
    doi,
    title,
    pub_date,
):
    """
    Check whether this Europe PMC source already exists.

    Primary identifier:
        DOI

    Fallback when DOI is unavailable:
        title + publication_date + journal
    """

    # --------------------------------------------------------
    # Preferred: DOI
    # --------------------------------------------------------

    if doi:
        cur.execute(
            """
            SELECT source_id
            FROM sources
            WHERE doi = %s
            LIMIT 1
            """,
            (doi,),
        )

    # --------------------------------------------------------
    # Fallback: title + date + journal
    # --------------------------------------------------------

    else:
        cur.execute(
            """
            SELECT source_id
            FROM sources
            WHERE title = %s
              AND publication_date = %s
              AND journal = %s
            LIMIT 1
            """,
            (
                title[:500],
                pub_date,
                JOURNAL_NAME,
            ),
        )

    return cur.fetchone()


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db-url",
        required=True,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=300,
        help="Maximum number of NEW sources to ingest",
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # Load embedding model
    # --------------------------------------------------------

    print(
        "Loading embedding model "
        "(all-MiniLM-L6-v2, local) ..."
    )

    model = SentenceTransformer(
        "all-MiniLM-L6-v2"
    )

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    conn = psycopg2.connect(
        args.db_url
    )

    cur = conn.cursor()

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    sources_inserted = 0
    passages_inserted = 0

    skipped_existing = 0
    skipped_no_abstract = 0
    skipped_no_date = 0

    # --------------------------------------------------------
    # Europe PMC cursor
    # --------------------------------------------------------

    cursor_mark = "*"

    print(
        f"Querying Europe PMC: {QUERY!r}"
    )

    # ========================================================
    # FETCH PAGES
    # ========================================================

    while sources_inserted < args.limit:

        data = fetch_page(cursor_mark)

        results = (
            data
            .get("resultList", {})
            .get("result", [])
        )

        if not results:
            print(
                "No more results from Europe PMC."
            )
            break

        # ====================================================
        # PROCESS EACH RECORD
        # ====================================================

        for row in results:

            if sources_inserted >= args.limit:
                break

            # ------------------------------------------------
            # Abstract
            # ------------------------------------------------

            abstract = row.get(
                "abstractText"
            )

            if not abstract:
                skipped_no_abstract += 1
                continue

            # ------------------------------------------------
            # Publication date
            # ------------------------------------------------

            pub_date = row.get(
                "firstPublicationDate"
            )

            if not pub_date:

                pub_year = row.get(
                    "pubYear"
                )

                if not pub_year:
                    skipped_no_date += 1
                    continue

                pub_date = f"{pub_year}-01-01"

            # ------------------------------------------------
            # Basic metadata
            # ------------------------------------------------

            title = (
                row.get("title")
                or "(untitled)"
            )

            doi = row.get("doi")

            # ------------------------------------------------
            # URL
            # ------------------------------------------------

            if doi:

                url = (
                    f"https://doi.org/{doi}"
                )

            else:

                full_text_urls = (
                    row
                    .get("fullTextUrlList", {})
                    .get("fullTextUrl", [])
                )

                if full_text_urls:
                    url = full_text_urls[0].get(
                        "url"
                    )
                else:
                    url = None

            # =================================================
            # DUPLICATE CHECK
            # =================================================

            existing_source = source_already_exists(
                cur=cur,
                doi=doi,
                title=title,
                pub_date=pub_date,
            )

            if existing_source:

                skipped_existing += 1

                print(
                    f"  [skip] Already exists: "
                    f"source_id={existing_source[0]} | "
                    f"{title[:70]}"
                )

                continue

            # =================================================
            # INSERT NEW SOURCE
            # =================================================

            cur.execute(
                """
                INSERT INTO sources
                (
                    title,
                    source_type,
                    journal,
                    publication_date,
                    doi,
                    url,
                    reliability_score
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING source_id
                """,
                (
                    title[:500],
                    "systematic_review",
                    JOURNAL_NAME,
                    pub_date,
                    doi,
                    url,
                    0.9,
                ),
            )

            source_id = cur.fetchone()[0]

            sources_inserted += 1

            print(
                f"  [new] source_id={source_id} | "
                f"{title[:70]}"
            )

            # =================================================
            # CHUNK ABSTRACT
            # =================================================

            chunks = chunk_text(
                abstract
            )

            if not chunks:
                continue

            # =================================================
            # EMBEDDINGS
            # =================================================

            embeddings = model.encode(
                chunks,
                show_progress_bar=False,
            )

            # =================================================
            # INSERT PASSAGES
            # =================================================

            for i, (chunk, embedding) in enumerate(
                zip(chunks, embeddings)
            ):

                cur.execute(
                    """
                    INSERT INTO passages
                    (
                        source_id,
                        chunk_index,
                        chunk_text,
                        embedding
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        source_id,
                        i,
                        chunk,
                        embedding.tolist(),
                    ),
                )

                passages_inserted += 1

            # ------------------------------------------------
            # Commit every 50 NEW sources
            # ------------------------------------------------

            if sources_inserted % 50 == 0:

                conn.commit()

                print(
                    f"  ... "
                    f"{sources_inserted} new sources / "
                    f"{passages_inserted} passages so far"
                )

        # ====================================================
        # NEXT EUROPE PMC PAGE
        # ====================================================

        next_cursor = data.get(
            "nextCursorMark"
        )

        if (
            not next_cursor
            or next_cursor == cursor_mark
        ):

            print(
                "Reached end of result set."
            )

            break

        cursor_mark = next_cursor

        # Europe PMC free API courtesy delay
        time.sleep(0.34)

    # ========================================================
    # FINAL COMMIT
    # ========================================================

    conn.commit()

    # ========================================================
    # CLOSE
    # ========================================================

    cur.close()
    conn.close()

    # ========================================================
    # SUMMARY
    # ========================================================

    print(
        "\n========================================"
    )

    print(
        "EUROPE PMC INGESTION COMPLETE"
    )

    print(
        "========================================"
    )

    print(
        f"New sources inserted: {sources_inserted}"
    )

    print(
        f"Passages inserted: {passages_inserted}"
    )

    print(
        f"Existing sources skipped: {skipped_existing}"
    )

    print(
        f"Skipped (no abstract): {skipped_no_abstract}"
    )

    print(
        f"Skipped (no date): {skipped_no_date}"
    )

    print(
        "========================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
    