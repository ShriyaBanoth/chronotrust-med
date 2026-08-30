"""
ChronoTrust-Med — Week 2: generation (B0 and B1 baselines).

B0 = LLM-only, no retrieval at all. Evaluation floor.
B1 = plain retrieve-then-generate. Your first real, demoable system.

Requires: export ANTHROPIC_API_KEY=sk-ant-...
Get a key at https://console.anthropic.com/settings/keys
"""

import argparse
import os

import anthropic

from retrieval.retrieve import retrieve

MODEL = "claude-haiku-4-5-20251001"  # fast + cheap; swap to a stronger model later for final eval runs


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY in your environment before running this.")
    return anthropic.Anthropic(api_key=api_key)


def answer_b0(question):
    """LLM-only, no retrieval. Baseline floor — expect this to hallucinate more."""
    client = _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": question}],
    )
    return response.content[0].text


def answer_b1(question, db_url, top_k=5):
    """Plain RAG: retrieve top_k passages, then generate with them as context."""
    passages = retrieve(question, db_url, top_k=top_k)

    if not passages:
        context = "(no relevant passages found in the corpus)"
    else:
        context = "\n\n".join(
            f"[Source {i+1}: {p['title']} ({p['journal']})]\n{p['chunk_text']}"
            for i, p in enumerate(passages)
        )

    prompt = f"""Answer the medical question using ONLY the evidence below. Cite sources by number (e.g. [Source 1]). If the evidence doesn't answer the question, say so explicitly rather than guessing.

EVIDENCE:
{context}

QUESTION: {question}"""

    client = _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text, passages


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--mode", choices=["b0", "b1", "both"], default="both")
    args = parser.parse_args()

    if args.mode in ("b0", "both"):
        print("=== B0 (LLM-only) ===")
        print(answer_b0(args.question))
        print()

    if args.mode in ("b1", "both"):
        print("=== B1 (retrieve-then-generate) ===")
        answer, passages = answer_b1(args.question, args.db_url)
        print(answer)
        print(f"\n(retrieved {len(passages)} passages)")
