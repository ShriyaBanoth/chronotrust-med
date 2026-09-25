"""
ChronoTrust-Med — B2: extract medical claims from ingested passages.

Uses Groq to extract discrete, checkable medical claims from
ingested medical sources and stores them in medical_claims.

Each source now has a claim_extraction_status:

    pending     -> not processed yet
    completed   -> claims successfully extracted and stored
    no_claims   -> source was processed but contained no extractable claims
    failed      -> extraction failed; can be inspected/retried manually

This prevents protocol/background-only sources from being repeatedly
processed.

This script does NOT populate claim_relations.
That will be handled later by the conflict/evolution pipeline.
"""

import argparse
import json
import re
import time
from collections import deque

from dotenv import load_dotenv
from groq import Groq
import psycopg2

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

MODEL = "openai/gpt-oss-120b"


SYSTEM_PROMPT = """You extract discrete, checkable medical claims from a medical systematic review or guideline source.

For each claim, return EXACTLY these four fields:

- claim_text: a single, self-contained factual medical claim in subject-predicate-object form.
- population: the population the claim applies to, if explicitly stated. Otherwise null.
- evidence_level: MUST be exactly one of:
  "systematic_review",
  "rct",
  "observational",
  "expert_opinion",
  or null if the evidence level cannot be determined.
- confidence: a NUMBER between 0.0 and 1.0 representing your confidence that the claim was accurately extracted from the source.

Rules:
1. Extract 1-5 main, checkable factual claims.
2. Do not invent information that is not present in the source.
3. Do not add explanations outside the JSON.
4. Do not use labels such as "high", "medium", or "low" for confidence.
5. Do not use values such as "Level 1" for evidence_level.
6. evidence_level must match one of the allowed values exactly.
7. If the source only describes a protocol, objectives, methods, or background and contains no actual findings, return [].
8. Return ONLY valid JSON. No Markdown. No ``` fences.

Required output format:

[
  {
    "claim_text": "Example factual claim",
    "population": "Example population",
    "evidence_level": "rct",
    "confidence": 0.95
  }
]

If no checkable factual claims are present, return:
[]
"""


# ============================================================
# TEXT TRUNCATION
# ============================================================

def truncate_abstract(text, max_words=800):
    """
    Keep the most useful portion of long source text.

    Short sources are kept completely.
    Long sources are truncated to the last max_words because
    results/conclusions commonly appear near the end.
    """

    words = text.split()

    if len(words) > max_words:
        return "... " + " ".join(words[-max_words:])

    return text


# ============================================================
# TOKEN BUDGET
# ============================================================

class TokenBudget:

    def __init__(self, tpm_limit=7900):
        # Groq free-tier safety margin
        self.tpm_limit = tpm_limit
        self.window = deque()

    def wait_for_budget(self, estimated_next):

        while True:

            now = time.time()

            # Remove token usage older than 60 seconds
            while self.window and now - self.window[0][0] >= 60.0:
                self.window.popleft()

            used_tokens = sum(t for _, t in self.window)

            if used_tokens + estimated_next <= self.tpm_limit:
                break

            wait_time = 60.0 - (now - self.window[0][0])

            if wait_time > 0:
                print(
                    f"  [throttle] Token budget near limit "
                    f"({used_tokens}/{self.tpm_limit} used). "
                    f"Sleeping {wait_time:.1f}s..."
                )

                time.sleep(wait_time)

    def consume(self, tokens):
        self.window.append((time.time(), tokens))


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def extract_claims(client, abstract_text, max_retries=15):

    tokens_used = 0

    for attempt in range(max_retries):

        try:

            response = client.chat.completions.create(
                model=MODEL,
                temperature=0.0,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": abstract_text,
                    },
                ],
            )

            tokens_used = (
                response.usage.total_tokens
                if response.usage
                else 0
            )

            break

        except Exception as exc:

            exc_str = str(exc)

            # Handle Groq rate limits
            if "429" in exc_str or "rate_limit" in exc_str:

                match = re.search(
                    r"Please try again in ([0-9.]+)s",
                    exc_str
                )

                if match:
                    wait = float(match.group(1)) + 0.1
                else:
                    wait = min(
                        15 * (attempt + 1),
                        65
                    )

                print(
                    f"    [rate-limited] waiting "
                    f"{wait:.2f}s before retry "
                    f"{attempt + 1}/{max_retries}..."
                )

                time.sleep(wait)
                continue

            # Non-rate-limit errors go back to main()
            raise

    else:

        raise RuntimeError(
            f"Gave up after {max_retries} rate-limit retries"
        )

    raw = (
        response.choices[0].message.content or ""
    ).strip()

    # Remove accidental Markdown code fences
    if raw.startswith("```"):

        raw = raw.strip("`")

        if raw.startswith("json"):
            raw = raw[4:]

        raw = raw.strip()

    try:

        claims = json.loads(raw)

    except json.JSONDecodeError:

        print(
            f"  [warn] Could not parse model output. "
            f"Raw: {raw[:300]!r}"
        )

        return [], tokens_used

    # Make sure model returned a list
    if not isinstance(claims, list):

        print(
            "  [warn] Model returned something other than a JSON list."
        )

        return [], tokens_used

    return claims, tokens_used


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db-url",
        required=True
    )

    parser.add_argument(
        "--batch-limit",
        "--limit",
        dest="batch_limit",
        type=int,
        default=300,
        help="Maximum number of sources to process in this run"
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # Groq client
    # --------------------------------------------------------

    client = Groq()

    # --------------------------------------------------------
    # Database connection
    # --------------------------------------------------------

    conn = psycopg2.connect(args.db_url)

    cur = conn.cursor()

    # --------------------------------------------------------
    # Select only PENDING sources
    #
    # This is the important Step 2 change.
    # Protocol sources marked no_claims will NOT be processed
    # again.
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT
            s.source_id,
            s.title,
            s.publication_date,
            s.source_type,
            array_agg(
                p.chunk_text
                ORDER BY p.chunk_index
            ) AS chunks

        FROM sources s

        JOIN passages p
            ON p.source_id = s.source_id

        WHERE s.claim_extraction_status = 'pending'

        GROUP BY
            s.source_id,
            s.title,
            s.publication_date,
            s.source_type

        ORDER BY s.source_id

        LIMIT %s
        """,
        (args.batch_limit,),
    )

    sources = cur.fetchall()

    print(
        f"Found {len(sources)} pending sources with passages."
    )

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    claims_inserted = 0
    sources_processed = 0
    sources_with_no_claims = 0
    sources_failed = 0

    # --------------------------------------------------------
    # Groq token budget
    # --------------------------------------------------------

    budget = TokenBudget(
        tpm_limit=7900
    )

    # ========================================================
    # PROCESS EACH SOURCE
    # ========================================================

    for (
        source_id,
        title,
        pub_date,
        source_type,
        chunks
    ) in sources:

        print(
            f"\nProcessing source_id={source_id}: "
            f"{title[:80]}"
        )

        # ----------------------------------------------------
        # Combine passages
        # ----------------------------------------------------

        full_text = (
            f"{title}\n\n"
            + " ".join(chunks)
        )

        abstract_text = truncate_abstract(
            full_text,
            max_words=800
        )

        # ----------------------------------------------------
        # Estimate token usage
        # ----------------------------------------------------

        estimated_tokens = (
            int(len(abstract_text.split()) * 1.5)
            + 300
        )

        budget.wait_for_budget(
            estimated_tokens
        )

        # ====================================================
        # PROCESS ONE SOURCE ATOMICALLY
        # ====================================================

        try:

            # ------------------------------------------------
            # Call LLM
            # ------------------------------------------------

            claims, tokens_used = extract_claims(
                client,
                abstract_text
            )

            # ------------------------------------------------
            # Update token budget
            # ------------------------------------------------

            if tokens_used > 0:

                budget.consume(
                    tokens_used
                )

            else:

                budget.consume(
                    estimated_tokens
                )

            # =================================================
            # CASE 1: NO CLAIMS
            # =================================================

            if not claims:

                sources_with_no_claims += 1

                cur.execute(
                    """
                    UPDATE sources

                    SET claim_extraction_status = 'no_claims'

                    WHERE source_id = %s
                    """,
                    (source_id,),
                )

                sources_processed += 1

                # Commit this source immediately
                conn.commit()

                print(
                    f"  [no claims] "
                    f"source_id={source_id}"
                )

            # =================================================
            # CASE 2: CLAIMS FOUND
            # =================================================

            else:

                source_claim_count = 0

                for claim in claims:

                    claim_text = claim.get(
                        "claim_text"
                    )

                    population = claim.get(
                        "population"
                    )

                    evidence_level = claim.get(
                        "evidence_level"
                    )

                    confidence = claim.get(
                        "confidence"
                    )

                    # -----------------------------------------
                    # Basic validation
                    # -----------------------------------------

                    if not claim_text:

                        print(
                            "  [warn] Skipping claim "
                            "without claim_text."
                        )

                        continue

                    # -----------------------------------------
                    # Insert claim
                    # -----------------------------------------

                    cur.execute(
                        """
                        INSERT INTO medical_claims
                        (
                            claim_text,
                            valid_from,
                            valid_until,
                            source_id,
                            evidence_level,
                            population,
                            confidence
                        )

                        VALUES
                        (
                            %s,
                            %s,
                            NULL,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            claim_text,
                            pub_date,
                            source_id,
                            evidence_level or source_type,
                            population,
                            confidence,
                        ),
                    )

                    claims_inserted += 1
                    source_claim_count += 1

                # -----------------------------------------
                # If all returned claims were invalid,
                # treat source as no_claims.
                # -----------------------------------------

                if source_claim_count == 0:

                    cur.execute(
                        """
                        UPDATE sources

                        SET claim_extraction_status = 'no_claims'

                        WHERE source_id = %s
                        """,
                        (source_id,),
                    )

                    sources_with_no_claims += 1

                    print(
                        f"  [no valid claims] "
                        f"source_id={source_id}"
                    )

                else:

                    # -------------------------------------
                    # Mark source successfully completed
                    # -------------------------------------

                    cur.execute(
                        """
                        UPDATE sources

                        SET claim_extraction_status = 'completed'

                        WHERE source_id = %s
                        """,
                        (source_id,),
                    )

                    print(
                        f"  [success] "
                        f"{source_claim_count} claims inserted"
                    )

                sources_processed += 1

                # Commit THIS source
                conn.commit()

        # ====================================================
        # CASE 3: SOURCE FAILED
        # ====================================================

        except Exception as exc:

            # Roll back only the current transaction
            conn.rollback()

            # Record failure separately
            try:

                cur.execute(
                    """
                    UPDATE sources

                    SET claim_extraction_status = 'failed'

                    WHERE source_id = %s
                    """,
                    (source_id,),
                )

                conn.commit()

            except Exception as status_error:

                conn.rollback()

                print(
                    f"  [error] Could not mark source "
                    f"{source_id} as failed: {status_error}"
                )

            sources_failed += 1

            print(
                f"  [error] source_id={source_id} "
                f"({title[:60]!r}) failed: {exc}"
            )

            # Continue with next source
            continue

    # ========================================================
    # CHECK REMAINING PENDING SOURCES
    # ========================================================

    cur.execute(
        """
        SELECT COUNT(DISTINCT s.source_id)

        FROM sources s

        JOIN passages p
            ON p.source_id = s.source_id

        WHERE s.claim_extraction_status = 'pending'
        """
    )

    remaining_sources = cur.fetchone()[0]

    # ========================================================
    # STATUS SUMMARY
    # ========================================================

    cur.execute(
        """
        SELECT
            claim_extraction_status,
            COUNT(*)

        FROM sources

        GROUP BY claim_extraction_status

        ORDER BY claim_extraction_status
        """
    )

    status_counts = cur.fetchall()

    # ========================================================
    # CLOSE DATABASE
    # ========================================================

    cur.close()
    conn.close()

    # ========================================================
    # PRINT RESULTS
    # ========================================================

    print(
        "\n========================================"
    )

    print(
        "CLAIM EXTRACTION COMPLETE"
    )

    print(
        "========================================"
    )

    print(
        f"Processed sources: {sources_processed}"
    )

    print(
        f"Claims inserted: {claims_inserted}"
    )

    print(
        f"Sources with no claims: {sources_with_no_claims}"
    )

    print(
        f"Sources failed: {sources_failed}"
    )

    print(
        f"Pending sources remaining: {remaining_sources}"
    )

    print(
        "\nSource status breakdown:"
    )

    for status, count in status_counts:

        print(
            f"  {status}: {count}"
        )

    print(
        "========================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()