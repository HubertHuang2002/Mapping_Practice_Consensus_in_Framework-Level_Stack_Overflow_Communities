"""Thin SERVE plane (ADR 0009): warm reads of the materialized cache + the cold-path entry. No LLM
at request time on the warm path — resolve + bake (which may call the LLM) run in a BackgroundTask.

  GET  /queries              landing list of known groups + bake status
  GET  /breakdown/{group_id} baked Breakdown verbatim, or 202 while it bakes (poll model)
  GET  /answer/{answer_id}   lazy answer body (kept out of the cache; group-independent)
  GET  /usage                in-process LLM token tally (per-tier + USD) — diff to cost one bake
  POST /queries              cold-path first line: record submission → resolve (Module C seam) →
                             warm dedup OR enqueue a background bake → {submission_id, group_id, status}

Run: PYTHONPATH=src uvicorn serve.app:app --reload  (restart after a cache-schema migration).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from canonical.resolver import OpenAIRagResolver
from store.cache import list_queries, mark_baking, read_cache

DB = str(config.DB_PATH)  # shared SQLite integration bus (path/index centralized in src/config.py)
SNAPSHOTS = {"q54069253": "src/breakdown/data/canonical_q54069253.json"}  # fixture-only: out-of-window
# answer bodies for the retired q54069253 demo. Real cold groups are fully in-window — the /answer DB
# lookup covers them and this snapshot fallback never fires.

app = FastAPI(title="community-consensus serve", version="0.2.0")
resolver = OpenAIRagResolver()  # QueryResolver seam — Module C 2b (real RAG + LLM equivalence gate)

# Live bake progress, in-process (BackgroundTasks share this process): group_id → {stage, k, n}.
# Written by the bake task's callback, read by the /breakdown poll so the interstitial can follow the
# REAL stage (extract carries k/n — the longest stretch). Popped when the bake finishes.
PROGRESS: dict[str, dict] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@contextmanager
def _conn():
    c = sqlite3.connect(DB)
    try:
        yield c
    finally:
        c.close()


# ── warm reads ───────────────────────────────────────────────────────────────────────────────────

@app.get("/queries")
def get_queries() -> dict:
    with _conn() as c:
        return {"queries": list_queries(c)}


@app.get("/breakdown/{group_id}")
def get_breakdown(group_id: str, response: Response):
    """Warm: the cached Breakdown (contract JSON). Baking: 202 so the client polls."""
    with _conn() as c:
        hit = read_cache(c, group_id)
    if hit is None:
        raise HTTPException(status_code=404, detail=f"unknown group {group_id!r}")
    if hit["status"] != "ready":
        response.status_code = 202
        return {"status": hit["status"], "group_id": group_id, **PROGRESS.get(group_id, {})}
    return hit["viz"]


def _answer_body(conn: sqlite3.Connection, answer_id: int) -> str | None:
    row = conn.execute(
        "SELECT body, body_text FROM answers WHERE answer_id = ?", (answer_id,)).fetchone()
    if row:
        return row[1] or row[0]  # prefer body_text (clean) — matches the cache + the snapshot path
    for snap in SNAPSHOTS.values():  # out-of-window canonical answers live in the proxy snapshot
        for a in json.loads(Path(snap).read_text()).get("canonical_answers", []):
            if a.get("answer_id") == answer_id:
                return a.get("body_text") or a.get("body")
    return None


@app.get("/answer/{answer_id}")
def get_answer(answer_id: int):
    """Lazy answer body — kept out of the breakdown cache (group-independent; fetched on click)."""
    with _conn() as c:
        body = _answer_body(c, answer_id)
    if body is None:
        raise HTTPException(status_code=404, detail=f"unknown answer {answer_id}")
    return {"answer_id": answer_id, "body": body}


@app.get("/usage")
def get_usage() -> dict:
    """Cost visibility: the running LLM token tally since serve start (per-tier tokens + USD).
    In-process and unpersisted — snapshot before a submit and after the bake; the delta is what
    that one query cost. Embeddings (resolve) are not counted (negligible for a short query)."""
    from breakdown.pipeline.llm import usage_cost  # lazy: keep openai off the import-time warm path
    return usage_cost()


# ── cold-path entry ────────────────────────────────────────────────────────────────────────────────

class QuerySubmission(BaseModel):
    query_text: str


def _record_submission(conn: sqlite3.Connection, query_text: str, group_id: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO submissions (query_text, group_id, submitted_at) VALUES (?, ?, ?)",
        (query_text, group_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()
    return cur.lastrowid


def _bake_task(group_id: str) -> None:
    """Background bake (may call the LLM for a cold group). Imported lazily so the warm serve path
    never drags in numpy/openai. A seeded group (proxy snapshot) just materializes (⑦); a truly new
    one resolved by the real resolver runs the live cold bake (extract+gate → cluster → narrative →
    ⑦; PLAN step 9). Publishes per-stage progress into PROGRESS for the /breakdown poll to relay."""
    from orchestration.driver import SNAPSHOTS, bake_cold_group, bake_group

    def on_progress(stage: str, k: int | None = None, n: int | None = None) -> None:
        PROGRESS[group_id] = {"stage": stage, "k": k, "n": n}

    try:
        if group_id in SNAPSHOTS:
            bake_group(group_id, on_progress=on_progress)
        else:
            bake_cold_group(group_id, on_progress=on_progress)
    finally:
        PROGRESS.pop(group_id, None)  # bake done (cache now 'ready') — stop reporting a stale stage


@app.post("/queries")
def submit_query(submission: QuerySubmission, background: BackgroundTasks):
    """First line of the cold path: record the submission, resolve it to a canonical group, then
    either return the warm cache (group already baked → free dedup) or enqueue a background bake."""
    try:
        group_id = resolver.resolve(submission.query_text)
    except FileNotFoundError as e:  # canonical index not built yet (python -m canonical.build_index)
        raise HTTPException(status_code=503, detail=f"resolver unavailable: {e}")
    with _conn() as c:
        sub_id = _record_submission(c, submission.query_text, group_id)
        if group_id is None:
            raise HTTPException(
                status_code=422,
                detail={"submission_id": sub_id, "error": "could not resolve query to a canonical "
                        "group (proxy resolves curated demo queries only; arbitrary text needs Module C 2b)"})
        hit = read_cache(c, group_id)
        if hit and hit["status"] == "ready":
            return {"submission_id": sub_id, "group_id": group_id, "status": "ready"}
        mark_baking(c, group_id)
    background.add_task(_bake_task, group_id)
    return {"submission_id": sub_id, "group_id": group_id, "status": "baking"}
