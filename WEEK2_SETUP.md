# Week 2 — Setup Instructions

This adds real passage content + embeddings + retrieval + generation on top
of Week 1's schema. Files to drop into your existing `chronotrust-med`
project folder (matching paths):

```
sql/002_add_passages.sql          # new migration
ingestion/ingest_with_content.py  # replaces Week 1's metadata-only load
retrieval/retrieve.py             # new
generation/generate.py            # new
```

## 1. Add the new tables

```bash
psql -d chronotrust -f sql/002_add_passages.sql
```

If this errors with something like `extension "vector" is not available`,
you need the pgvector extension installed for Postgres first:

```bash
brew install pgvector
```

Then re-run the migration.

## 2. Add new Python dependencies

```bash
pip install anthropic python-dotenv
```

## 3. Get an Anthropic API key

Go to https://console.anthropic.com/settings/keys, create a key, then set it
for your terminal session:

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

(Don't commit this anywhere — it's already covered by your `.env` rule in
`.gitignore`, but exporting it in the terminal like this doesn't touch a
file at all, which is the safest option for now.)

## 4. Re-ingest with real content

This clears Week 1's metadata-only rows and reloads with actual text +
embeddings. Start small — embedding takes real time per source:

```bash
python ingestion/ingest_with_content.py --db-url "postgresql://YOUR_MAC_USERNAME@localhost:5432/chronotrust" --limit 100
```

## 5. Test retrieval alone

```bash
python retrieval/retrieve.py --db-url "postgresql://YOUR_MAC_USERNAME@localhost:5432/chronotrust" --query "What is the first-line treatment for hypertension?"
```

## 6. Test B0 vs B1 generation

```bash
python -m generation.generate --db-url "postgresql://YOUR_MAC_USERNAME@localhost:5432/chronotrust" --question "What is the first-line treatment for hypertension?" --mode both
```

Compare the two outputs — B0 has no citations and may be more generic or
outdated; B1 should cite [Source N] and stay closer to your actual corpus.
This comparison is literally your first real evaluation result.
