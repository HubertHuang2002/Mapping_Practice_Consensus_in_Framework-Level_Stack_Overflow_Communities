# canonical — Module C (query → canonical group)

Resolves a free-text developer query to its **query-equivalent canonical group**: the set of SO
questions that ask the *same specific problem*. This is the cold-path entry behind `POST /queries`.

## Real pipeline (OpenAI)

```
query ──embed──► cosine top-K ──LLM equivalence gate──► canonical_group ──► group_id
       embed.py    index.py            gate.py            (DB)        resolver.py
```

- **`embed.py`** — query/corpus embeddings via OpenAI `text-embedding-3-small` (1536-dim). This is the
  *retrieval* embedder; it is deliberately separate from the breakdown clustering companion (ADR 0002,
  local SBERT for the reproducibility floor).
- **`build_index.py`** — one-time job: embed every react question `(title + body_text)` and persist
  `data/canonical_index/{embeddings.npy, question_ids.npy}`. Resumable; ~$0.66 over ~220k questions.
  `PYTHONPATH=src uv run --no-sync python -m canonical.build_index`
- **`index.py`** — loads the `.npy` matrix **once per process** and serves cosine top-K (no per-query
  table re-parse).
- **`gate.py`** — the equivalence **gate** (set-membership boundary, *not* a reranker): per-candidate
  "same specific question?" judged in concurrent small batches on the `gate` tier (gpt-5.4-nano),
  reading `title + body_text` snippet so it separates *same problem* from *same topic*.
- **`resolver.py`** — `OpenAIRagResolver` (the `QueryResolver` seam): wires the above, writes every
  gated candidate to `canonical_group` (members = `gate_decision='equivalent'`), returns `q{anchor}`.

## Stand-ins (still in use)

- **`proxy.py`** — `DuplicateChainGroups`, the `CanonicalGroupProvider` *fetch* half (group_id → member
  answers) off the SO duplicate chain + snapshot. The bake spine still uses it (driver); a real
  DB-backed fetch is PLAN step 9.
- **`resolve.py`** — `KnownChainResolver`, a zero-dependency `QueryResolver` fallback (curated demo
  queries only). Kept for offline / no-API runs; serve runs the real resolver.
- **`fetch.py`** — one-shot builder for the out-of-window canonical fixture
  (`data/canonical_q54069253.json`), pulled from the StackExchange API.

Needs `OPENAI_API_KEY` (env or `.env`).
