# Mapping the community-consensus practice landscape in framework-level Stack Overflow communities via multi-answer structure.


> 📄 **Full project report:** [Google Drive](https://drive.google.com/file/d/1Py-pvJbWS3Dh6rLx310gwoCeoD2Mf-Ga/view?usp=sharing)

## Core Idea

On Stack Overflow, the "one question, many answers" structure is itself an implicit aggregator of collective expert opinion. The distribution of practices across answers, combined with the community authority of their authors, can quantitatively describe the consensus state of a community on a given class of technical questions.

This project turns that idea into a working system. Given a natural-language developer question (e.g. *"how should I manage global state in React?"*), it:

1. **Resolves the query to a canonical question group** — using LLM-based canonicalization and embedding retrieval to gather all Stack Overflow questions that ask the same underlying thing, regardless of wording.
2. **Extracts the practices** recommended across the group's answers, and clusters them into distinct technical camps.
3. **Measures the consensus landscape** along two independent axes: community votes reveal the **breakdown shape** — whether the community has converged on one practice (consensus), split between competing camps (polarization), or scattered across many (fragmentation) — while a network-derived **authority score** (PageRank over the answerer–asker interaction graph) shows where the community's most established voices stand.
4. **Renders the result as an interactive dashboard** — a force-field visualization paired with a generated narrative that walks through the landscape.

The whole path is live: submit a new query, and the system resolves, analyzes, and visualizes it end-to-end.


## Architecture

The runtime topology consists of three planes, with **SQLite as the single integration bus**.

- **OFFLINE BAKE** (Python batch; slow / costs LLM $ / deterministic): corpus fetch → authority → canonical group → per-group `extract → relevance → cluster → aggregate (signal-table) → narrative → materialize`, written into SQLite.
- **SERVE** (FastAPI; thin; ms-scale; **no LLM calls at request time**): reads tables / cache, returns `Breakdown` JSON. Endpoints: `GET /breakdown` · `GET /answer` · `POST /queries` (cold-path entry) · `GET /usage`.
- **PRESENT** (SvelteKit SPA): renders `Breakdown`.

The codebase:

```
src/contract/        # Types + ports 
src/canonical/       # query → canonical group: embed + RAG + LLM equivalence gate
src/breakdown/       # answer-side practice extraction → cluster → fusion / layout
src/authority/       # user-network PageRank authority
src/ingest/          # SE API → SQLite collector
src/store/           # SQLite cache + schema 
src/orchestration/   # Idempotent bake driver 
src/serve/           # FastAPI serve plane
web/                 # SvelteKit SPA (force-field visualization + narrative)
data/                # SQLite DB + canonical_index (see "Data" below)
```

## Quick Start

Requirements: Python ≥ 3.11, [`uv`](https://docs.astral.sh/uv/), Node + [`pnpm`](https://pnpm.io/).

```bash
# 1. Backend dependencies
uv sync

# 2. Frontend dependencies
cd web && pnpm install && cd ..

# 3. API keys: copy the template and fill in values 
cp .env.example .env
#   OPENAI_API_KEY — needed to bake new queries / rebuild the canonical index (LLM + embedding)
#   SE_API_KEY     — only needed to re-fetch raw SO data
```

> The code does not auto-load `.env`. Env vars must actually exist in the shell environment: use [`direnv`](https://direnv.net/), or temporarily run `set -a; . ./.env; set +a` before starting.

### Data

The site requires two large data artifacts under `data/` (Not included in this repository. If needed, please contact the author):

- `data/handoff.db` — Cleared the per-query bake layer in SQLite: raw SO data + extracted practices + authority
- `data/canonical_index/` — OpenAI embedding matrix for ~221k questions (`embeddings.npy` + `question_ids.npy`), used by cold-path RAG to resolve free-text queries to canonical groups


### Running the Live Demo

Two processes; the frontend always hits `http://localhost:8000`, and the serve plane's CORS only allows `:5173`:

```bash
# Backend serve plane (warm reads + cold-path entry)
COMMUNITY_DB=data/handoff.db PYTHONPATH=src uv run uvicorn serve.app:app --reload      # → http://localhost:8000

# Frontend (in another terminal)
cd web && pnpm dev                                        # → http://localhost:5173
```

Open `http://localhost:5173`: the landing page lists the known groups — click one to see the force-field visualization + narrative. Submit a new query in the input box to go through the cold path (resolve → bake → dashboard).

