"""query_viz_cache — the materialized serve cache (ADR 0009 bake stage ⑦).

The normalized §3.2 tables (schema.py) are the source of truth; this stores each canonical GROUP's
already-assembled Breakdown (contract JSON) so serve is warm + deterministic — layout/pack runs once
at bake time, not per request. Lives in the shared SQLite integration bus, next to the normalized
D-side tables and the authority/answers tables.

Keyed by group_id (ADR 0009 Amendment 2026-06-03): measurement unit = canonical group, not the
query wording. Multiple phrasings that resolve to the same group share one baked row (group-level
dedup). The serve-facing JSON still labels the id "query_id" for now (list_queries / the
/breakdown/{id} route); that naming aligns to group_id with the serve+frontend pass (PLAN step 6/7).

A `status` column carries the poll model (grill #1): a freshly-submitted cold query is written
'baking' first, flipped to 'ready' when the bake finishes. Pre-baked demo groups are 'ready'.
"""
from __future__ import annotations

import json
import sqlite3

from contract.types import Breakdown

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_viz_cache (
    group_id        TEXT PRIMARY KEY,                -- canonical group id (ADR 0009 Amendment)
    status          TEXT NOT NULL DEFAULT 'ready',   -- 'ready' | 'baking'
    viz_json        TEXT,                            -- Breakdown.to_dict() (answer stubs only; body lazy-loaded)
    baked_at        TEXT,
    source_versions TEXT                             -- model/prompt versions baked in (staleness / idempotency)
);
"""


def init_cache(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def write_cache(
    conn: sqlite3.Connection,
    group_id: str,
    breakdown: Breakdown,
    baked_at: str,
    source_versions: dict | None = None,
) -> None:
    """Materialize one group's Breakdown into the cache as 'ready' (idempotent)."""
    init_cache(conn)
    conn.execute(
        "INSERT OR REPLACE INTO query_viz_cache "
        "(group_id, status, viz_json, baked_at, source_versions) VALUES (?, 'ready', ?, ?, ?)",
        (group_id, json.dumps(breakdown.to_dict()), baked_at, json.dumps(source_versions or {})),
    )
    conn.commit()


def mark_baking(conn: sqlite3.Connection, group_id: str) -> None:
    """Reserve a group_id as in-progress so GET /breakdown can return 202 while it bakes."""
    init_cache(conn)
    conn.execute(
        "INSERT OR REPLACE INTO query_viz_cache (group_id, status) VALUES (?, 'baking')",
        (group_id,),
    )
    conn.commit()


def read_cache(conn: sqlite3.Connection, group_id: str) -> dict | None:
    """Return {'status', 'viz', 'baked_at'} for a known group, or None if never seen."""
    row = conn.execute(
        "SELECT status, viz_json, baked_at FROM query_viz_cache WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        return None
    status, viz_json, baked_at = row
    return {
        "status": status,
        "viz": json.loads(viz_json) if viz_json else None,
        "baked_at": baked_at,
    }


def list_queries(conn: sqlite3.Connection) -> list[dict]:
    """Landing-page list: every known group, its bake status, and a human-readable `title` (the
    canonical question's title — group_id is 'q'+question_id, so a LEFT JOIN to `questions` labels
    every group; falls back to the group_id itself for out-of-window fixtures absent from the table).
    The output key stays "query_id" for serve/frontend compatibility until the naming pass (PLAN step
    6/7); the value is the group_id."""
    init_cache(conn)
    return [
        {"query_id": gid, "status": status, "baked_at": baked_at, "title": title or gid}
        for gid, status, baked_at, title in conn.execute(
            "SELECT v.group_id, v.status, v.baked_at, q.title "
            "FROM query_viz_cache v "
            "LEFT JOIN questions q ON q.question_id = CAST(SUBSTR(v.group_id, 2) AS INTEGER) "
            "ORDER BY v.baked_at IS NULL, v.baked_at DESC"
        )
    ]
