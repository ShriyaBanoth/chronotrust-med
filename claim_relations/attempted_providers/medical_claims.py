"""
ChronoTrust-Med — B2: extract medical claims from ingested passages.

Uses Groq's free API (Llama 3.3 70B) — no card, no project-review step
(unlike Google AI Studio, which has been flagging fresh projects as
"Restricted" for many users lately).

Groups passages back up by source (one Cochrane abstract = one API call,
not one call per chunk), prompts the model to pull out discrete, checkable
medical claims in SPO-ish form, and inserts them into medical_claims.
valid_from is set to the source's publication_date (the claim is "as of"
that evidence); valid_until is left NULL (unknown / still current) — B3's
temporal filtering and B4's conflict detection are what actually resolve
validity windows and contradictions later, not this step.

This does NOT populate claim_relations — that requires comparing pairs of
claims (supports/contradicts/supersedes), which is B4's job per the build
order, not B2's.

Setup:
    pip install groq psycopg2-binary
    Get a free key (no card): https://console.groq.com/keys
    export GROQ_API_KEY=your-key-here

Run:
    python claim_relations/medical_claims_groq.py --db-url postgresql://user@localhost/chronotrust --limit 300

Safe to re-run: sources that already have rows in medical_claims are
skipped, so a crash partway through (or adding more sources later) doesn't
reprocess or duplicate anything.
"""

import argparse
import json
import time

from groq import Groq  # type: ignore[import-not-found]
import psycopg2  # type: ignore[import-not-found]

# Free-tier Llama model on Groq. If this model id is ever retired, run
# `client.models.list()` once to see current options and swap the string.
MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You extract discrete, checkable medical claims from a systematic review abstract.

For each claim, identify:
- claim_text: a single, self-contained factual claim (subject-predicate-object shape), e.g. "Exercise training improves peak oxygen uptake in people with spinal cord injury"
- population: who the claim applies to, if stated (age range, condition, comorbidity). Use null if not specified.
- evidence_level: one of "systematic_review", "rct", "observational", "expert_opinion", or null if unclear from the text
- confidence: your confidence 0.0-1.0 that this claim is accurately and faithfully extracted from the text (not a claim about the medical evidence itself)

Extract 1-5 claims per abstract — the main, checkable findings, not background or methods statements.

Respond with ONLY a JSON array, no other text:
[{"claim_text": "...", "population": "...", "evidence_level": "...", "confidence": 0.9}, ...]
If no checkable claims are present, respond with an empty array: []
"""


def extract_claims(client, abstract_text):
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": abstract_text},
        ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    # Strip accidental markdown fences, just in case.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [warn] could not parse model output, skipping. Raw: {raw[:200]!r}")
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--limit", type=int, default=300, help="max sources to process")
    args = parser.parse_args()

    client = Groq()  # reads GROQ_API_KEY from env

    conn = psycopg2.connect(args.db_url)
    cur = conn.cursor()

    # Only pull sources that (a) have passages and (b) don't already have
    # claims extracted — makes reruns safe and cheap after a crash or when
    # new sources get ingested later.
    cur.execute(
        """
        SELECT s.source_id, s.title, s.publication_date, s.source_type,
               array_agg(p.chunk_text ORDER BY p.chunk_index) AS chunks
        FROM sources s
        JOIN passages p ON p.source_id = s.source_id
        WHERE NOT EXISTS (
            SELECT 1 FROM medical_claims mc WHERE mc.source_id = s.source_id
        )
        GROUP BY s.source_id, s.title, s.publication_date, s.source_type
        ORDER BY s.source_id
        LIMIT %s
        """,
        (args.limit,),
    )
    sources = cur.fetchall()
    print(f"Found {len(sources)} sources with passages left to process.")

    claims_inserted = 0
    sources_processed = 0
    sources_with_no_claims = 0
    sources_failed = 0

    for source_id, title, pub_date, source_type, chunks in sources:
        abstract_text = f"{title}\n\n" + " ".join(chunks)

        try:
            claims = extract_claims(client, abstract_text)

            if not claims:
                sources_with_no_claims += 1
            else:
                for c in claims:
                    cur.execute(
                        """
                        INSERT INTO medical_claims
                            (claim_text, valid_from, valid_until, source_id, evidence_level, population, confidence)
                        VALUES (%s, %s, NULL, %s, %s, %s, %s)
                        """,
                        (
                            c.get("claim_text"),
                            pub_date,
                            source_id,
                            c.get("evidence_level") or source_type,
                            c.get("population"),
                            c.get("confidence"),
                        ),
                    )
                    claims_inserted += 1

            sources_processed += 1

        except Exception as exc:
            # A single bad source (rate limit, timeout, unexpected response
            # shape) shouldn't kill a run that's already 200+ sources in.
            # Roll back only this source's partial inserts, log it, move on.
            conn.rollback()
            sources_failed += 1
            print(f"  [error] source_id={source_id} ({title[:60]!r}) failed: {exc}")
            continue

        if sources_processed % 25 == 0:
            conn.commit()
            print(f"  ... {sources_processed}/{len(sources)} sources / {claims_inserted} claims so far")

        # Groq's free tier has a per-minute request cap (commonly ~30 RPM
        # for this model). This throttle keeps you comfortably under it —
        # if you see 429 errors in [error] lines, raise this.
        time.sleep(2.0)

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone. Processed {sources_processed} sources, inserted {claims_inserted} claims.")
    print(f"Sources with no extractable claims: {sources_with_no_claims}")
    print(f"Sources that failed and were skipped: {sources_failed}")
    if sources_failed:
        print("Re-run the same command to retry failed/remaining sources — already-claimed sources are skipped.")


if __name__ == "__main__":
    main()