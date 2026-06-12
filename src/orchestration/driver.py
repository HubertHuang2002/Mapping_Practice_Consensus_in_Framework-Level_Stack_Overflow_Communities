"""orchestration.driver — the idempotent bake use case (ADR 0009 §3 + Amendment 2026-06-03).

bake(group_id) composes the spine: stages ①–⑥ (resolve → extract → gate → cluster → authority →
narrative) persist into the normalized §3.2 tables; stage ⑦ materializes the GROUP's Breakdown from
those tables into query_viz_cache. For the seeded backfill (q54069253), stages ①–⑥ are already in
the tables (orchestration.backfill_q54069253), so bake() runs only ⑦. bake_cold() runs the live LLM
stages ②③④⑥ (breakdown.pipeline.extract / cluster / narrative) then ⑦ — exercised end-to-end at
PLAN step 9 on a fresh in-window query; each stage is validated separately. The version-stamped,
dirty-propagating idempotency refines when stages stamp per-row versions; today bake() is COARSE: a
'ready' cache is warm and skipped unless force=True.

Stage ⑦ keys clusters by identity, not label, so the two co-association clusters that collided on one
name surface as the distinct communities the consensus found (see materialize_from_store).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config
from authority.provider import FullPagerankAuthorityProvider, PageRankAuthorityProvider  # noqa: F401 (PageRank = future timeline axis)
from canonical.groups import DbCanonicalGroups
from canonical.proxy import DuplicateChainGroups
from contract.ports import AuthorityProvider, CanonicalGroupProvider
from orchestration.materialize import materialize_from_store
from store.cache import read_cache, write_cache

DB = config.DB_PATH
DB_WINDOW = "2021–2026"   # configured data + authority-graph window; orchestration owns this config
WINDOW_START = 2021       # an answer with year < WINDOW_START is out of the data window

# Fixture-rebuild (proxy) path only. The REAL cold path (bake_cold_group → DbCanonicalGroups) needs
# no per-group snapshot; this dict is retained so the q54069253 demo can be re-baked on demand after
# its cache entry was retired (step 9). A real resolver group never appears here.
SNAPSHOTS = {"q54069253": Path("src/breakdown/data/canonical_q54069253.json")}

_UPSTREAM = {  # stages ①–⑥ presence checks (coarse idempotency until versions are stamped, step 5)
    "canonical_group (①)": "SELECT 1 FROM canonical_group WHERE group_id = ? LIMIT 1",
    "query_practice (②③④)": "SELECT 1 FROM query_practice WHERE group_id = ? LIMIT 1",
    "practice_clusters (④)": "SELECT 1 FROM practice_clusters WHERE group_id = ? LIMIT 1",
    "query_narrative (⑥)": "SELECT 1 FROM query_narrative WHERE group_id = ? LIMIT 1",
}


def _require_seeded(conn: sqlite3.Connection, group_id: str) -> None:
    missing = [n for n, q in _UPSTREAM.items() if conn.execute(q, (group_id,)).fetchone() is None]
    if missing:
        raise SystemExit(
            f"group {group_id!r} has no upstream-stage rows for: {missing}. "
            f"Seed them first (orchestration.backfill_q54069253 --apply, or the live pipeline / PLAN step 5)."
        )


def _narrative(conn: sqlite3.Connection, group_id: str) -> dict | None:
    row = conn.execute(
        "SELECT narrative_json FROM query_narrative WHERE group_id = ?", (group_id,)
    ).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def bake(
    conn: sqlite3.Connection,
    group_id: str,
    group_provider: CanonicalGroupProvider,
    authority_provider: AuthorityProvider,
    *,
    db_window: str = DB_WINDOW,
    window_start: int = WINDOW_START,
    force: bool = False,
    versions: dict | None = None,
) -> dict:
    """Idempotent bake of one group. Today: ①–⑥ are seeded (presence-checked); ⑦ materializes the
    cache unless it is already warm (override with force=True). `versions` stamps the cache's
    provenance — defaults to the fixture seed; the cold path passes its own real stage versions."""
    _require_seeded(conn, group_id)
    hit = read_cache(conn, group_id)
    if hit and hit["status"] == "ready" and not force:
        return {"group_id": group_id, "stage7": "skipped (warm cache; pass force=True to rebake)"}

    narrative = _narrative(conn, group_id)
    bd = materialize_from_store(conn, group_id, group_provider, authority_provider,
                                narrative=narrative, db_window=db_window, window_start=window_start)
    if versions is None:
        versions = {
            "authority": authority_provider.source_name,
            "group": group_provider.source_name,
            "narrative": "fixture-d4",
            "cluster_naming": "name_clusters/gpt-5.4",  # the colliding pair was re-labelled here
            "seed": "fixture",
        }
    write_cache(conn, group_id, bd, datetime.now(timezone.utc).isoformat(timespec="seconds"), versions)
    return {
        "group_id": group_id, "stage7": "materialized",
        "n_clusters": len(bd.clusters), "n_points": len(bd.points),
        "n_out_of_window": bd.n_out_of_window, "narrative": bool(bd.narrative),
    }


def bake_cold(
    conn: sqlite3.Connection,
    group_id: str,
    group_provider: CanonicalGroupProvider,
    authority_provider: AuthorityProvider,
    *,
    canonical_problem: str,
    anchor_title: str,
    snapshot_path: str | None = None,
    db_window: str = DB_WINDOW,
    window_start: int = WINDOW_START,
    on_progress=None,
) -> dict:
    """Live path: run LLM stages ②③ (extract+gate) → ④ (cluster+name) → ⑥ (narrative) into the
    tables, then ⑦ (materialize). Stage ① (resolve → canonical_group) is upstream (Module C / proxy
    seed). Costs real LLM $ over all answers — exercised end-to-end at PLAN step 9. Heavy deps
    (numpy/scipy/sklearn) are imported lazily so the warm bake() path stays light.

    `on_progress(stage, k=None, n=None)` (optional) is fired at each stage boundary so the SERVE
    poll can report which stage is live (extract carries per-answer k/n — the longest stretch)."""
    from breakdown.pipeline.aggregate import aggregate_signals
    from breakdown.pipeline.cluster import cluster_group
    from breakdown.pipeline.extract import extract_and_gate, load_group_answers
    from breakdown.pipeline.llm import TIER_MODEL
    from breakdown.pipeline.narrative import narrative_group

    if conn.execute("SELECT 1 FROM canonical_group WHERE group_id = ? LIMIT 1",
                    (group_id,)).fetchone() is None:
        raise SystemExit(f"group {group_id!r} not resolved (canonical_group empty); run stage ① first")
    answers = load_group_answers(conn, group_id, snapshot_path)
    cold_versions = {  # real per-stage provenance — NOT the fixture seed (honest cache badge)
        "authority": authority_provider.source_name,
        "group": group_provider.source_name,
        "extract_gate": f"{TIER_MODEL['extract']}/d1g-v1",
        "cluster_naming": f"name_clusters/{TIER_MODEL['aggregate']}",
        "narrative": TIER_MODEL["narrative"],
        "seed": "cold_bake",
    }
    if on_progress:
        on_progress("extract", 0, len(answers))
    ex = extract_and_gate(conn, group_id, answers, canonical_problem,
                          progress=(lambda k, n: on_progress("extract", k, n)) if on_progress else None)
    if on_progress:
        on_progress("cluster")
    cl = cluster_group(conn, group_id)
    if on_progress:
        on_progress("aggregate")
    ag = aggregate_signals(conn, group_id, group_provider)  # ⑤ signal-table v2 (pure compute)
    if on_progress:
        on_progress("narrate")
    nar = narrative_group(conn, group_id, anchor_title=anchor_title)
    if on_progress:
        on_progress("materialize")
    mat = bake(conn, group_id, group_provider, authority_provider,
               db_window=db_window, window_start=window_start, force=True, versions=cold_versions)
    return {"extract": ex, "cluster": cl, "aggregate": ag, "narrative": nar, "materialize": mat}


def bake_group(group_id: str, force: bool = False, on_progress=None) -> dict:
    """Fixture-rebuild path: wire the PROXY providers (snapshot group + PageRank) and re-bake a
    seeded demo group from its already-present upstream tables. The real cold path is
    bake_cold_group (live LLM stages, DB-backed group, no snapshot). Kept so the q54069253 fixture
    stays reproducible on demand."""
    if group_id not in SNAPSHOTS:
        raise SystemExit(f"no proxy snapshot configured for {group_id!r} (have: {list(SNAPSHOTS)})")
    conn = sqlite3.connect(DB)
    group_provider = DuplicateChainGroups(SNAPSHOTS[group_id], DB)
    authority_provider = FullPagerankAuthorityProvider(str(DB))
    try:
        if on_progress:  # seeded path only runs ⑦ — a single coarse beat
            on_progress("materialize")
        return bake(conn, group_id, group_provider, authority_provider, force=force)
    finally:
        authority_provider.close()
        conn.close()


def _cold_problem(conn: sqlite3.Connection, group_id: str) -> tuple[str, str]:
    """The relevance gate's canonical_problem + the narrative's anchor_title, both from the group's
    REPRESENTATIVE question ITSELF (group_id = q{medoid}; ADR 0010 — the medoid is the set-central
    member, not the phrasing-dependent top-cosine anchor). No hand-authored landscape: the gate
    sees the REAL question text (title + a body prefix), not a curated spec that pre-lists the
    expected approaches — and the gate's few-shots are now cross-topic, so its judgment is no longer
    useState-anchored (prompts/extract_gate.py)."""
    medoid = int(group_id[1:]) if group_id[1:].isdigit() else None
    row = conn.execute(
        "SELECT title, COALESCE(body_text, '') FROM questions WHERE question_id = ?", (medoid,)
    ).fetchone()
    if not row:
        raise SystemExit(f"representative question {medoid} not in DB for group {group_id!r}")
    title, body = row
    canonical_problem = (title + "\n\n" + body[:600]).strip()  # SO bodies front-load the problem
    return canonical_problem, title


def bake_cold_group(group_id: str, on_progress=None) -> dict:
    """Wire the REAL providers (DB-backed group + PageRank authority), derive the gate's
    canonical_problem + representative title from the group's medoid question, and run the live cold bake
    (extract+gate → cluster → narrative → materialize). Costs real LLM $ — the non-seeded path.
    `on_progress` is forwarded to bake_cold for live stage reporting (SERVE poll)."""
    conn = sqlite3.connect(DB)
    group_provider = DbCanonicalGroups(DB)
    authority_provider = FullPagerankAuthorityProvider(str(DB))
    try:
        canonical_problem, anchor_title = _cold_problem(conn, group_id)
        return bake_cold(conn, group_id, group_provider, authority_provider,
                         canonical_problem=canonical_problem, anchor_title=anchor_title,
                         on_progress=on_progress)
    finally:
        authority_provider.close()
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group", required=True, help="canonical group id to bake (e.g. q54069253)")
    p.add_argument("--force", action="store_true", help="re-materialize even if the cache is warm")
    args = p.parse_args()
    print(json.dumps(bake_group(args.group, force=args.force), ensure_ascii=False))


if __name__ == "__main__":
    main()
