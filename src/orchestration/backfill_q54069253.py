"""Seed the q54069253 fixtures into the normalized §3.2 tables — the bridge that stands in for the
offline bake stages ①–⑥ (resolve / extract / gate / cluster / authority / narrative) that ran in a
previous session and left their outputs as on-disk JSON. Stage ⑦ (materialize → query_viz_cache) is
the driver's job (orchestration.driver.bake), which reads these tables; this script only populates them.

What it does (idempotent):
  - migrate query_viz_cache from the old query_id key to group_id (RENAME COLUMN), once;
  - load canonical_group / practice_extractions / practice_clusters / query_practice / query_narrative
    from the fixtures, keying clusters by ENTRY (so two co-association clusters that collide on one
    label stay DISTINCT — the dup-name gotcha, grill 2026-06-03);
  - apply the cached re-naming sidecar (name_clusters LLM output) so the colliding pair gets clean,
    mutually-distinct labels + descriptions, with no repeat LLM call on re-run.

Usage: PYTHONPATH=src uv run --no-sync python -m orchestration.backfill_q54069253 --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import config
from store import init_store

DB = config.DB_PATH
DATA = Path("src/breakdown/data")
GROUP_ID = "q54069253"

EXTRACTIONS = DATA / "extractions.json"
CLUSTERS = DATA / "clusters.json"
SNAPSHOT = DATA / "canonical_q54069253.json"
NARRATIVE = DATA / "query_narrative.json"
RENAMES = DATA / "cluster_renames_q54069253.json"  # cached one-time name_clusters (LLM) output


def _load_renames() -> dict[tuple[int, ...], tuple[str, str]]:
    """sidecar renames keyed by the cluster's member set (its stable identity)."""
    if not RENAMES.exists():
        return {}
    return {
        tuple(sorted(r["members"])): (r["name"], r["description"])
        for r in json.loads(RENAMES.read_text())["renames"]
    }


def load_normalized_tables(conn: sqlite3.Connection) -> dict:
    """Seed the §3.2 normalized tables from the fixtures (idempotent: clears this group's rows first).
    Single-group backfill — a multi-group loader would scope the deletes by group_id / answer_id."""
    init_store(conn)
    extractions = json.loads(EXTRACTIONS.read_text())
    clusters = json.loads(CLUSTERS.read_text())["clusters"]
    snap = json.loads(SNAPSHOT.read_text())
    narrative = json.loads(NARRATIVE.read_text())
    renames = _load_renames()

    cur = conn.cursor()
    for t in ("query_practice", "query_narrative", "practice_clusters", "canonical_group"):
        cur.execute(f"DELETE FROM {t} WHERE group_id = ?", (GROUP_ID,))
    cur.execute("DELETE FROM practice_extractions")  # answer-keyed; only this group's answers exist

    # canonical_group: membership = canonical question + its SO duplicate chain (proxy grouping).
    canon_q = snap["canonical_question"]
    canon_qid = canon_q["question_id"] if isinstance(canon_q, dict) else canon_q
    member_qids = [canon_qid, *snap["dup_question_ids"]]
    for qid in member_qids:
        cur.execute(
            "INSERT OR REPLACE INTO canonical_group "
            "(group_id, question_id, retrieval_rank, retrieval_score, gate_decision, "
            " gate_confidence, gate_voting_agreement) VALUES (?, ?, NULL, NULL, NULL, NULL, NULL)",
            (GROUP_ID, qid),
        )

    # practice_extractions (answer-keyed) — insert in extraction order; capture flat-index -> id.
    flat_to_extraction_id: dict[int, int] = {}
    flat = 0
    for ans in extractions:
        for rank, p in enumerate(ans["practices"]):
            cur.execute(
                "INSERT INTO practice_extractions "
                "(answer_id, practice_rank, practice_sentence, conditions, evidence_type, "
                " extract_model_version, extract_prompt_version) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (ans["answer_id"], rank, p["practice"],
                 json.dumps(p.get("conditions", [])), p.get("evidence_type", "")),
            )
            flat_to_extraction_id[flat] = cur.lastrowid
            flat += 1

    # practice_clusters (per group) — one row per cluster ENTRY, NOT per name. The fixture holds two
    # DISTINCT co-association clusters that collide on the label "Direct set vs merge/immutability"
    # (disjoint members, different agreement); keying by entry index keeps them distinct, and the
    # rename sidecar gives the colliding pair clean, mutually-distinct labels (name_clusters fix for
    # the d2_consensus run-0 naming artifact). Members/agreement are unchanged.
    cluster_id_by_index: dict[int, int] = {}
    cluster_of_flat: dict[int, int] = {}
    renamed = 0
    for idx, c in enumerate(clusters):
        name, desc = renames.get(tuple(sorted(c["members"])), (c["name"], c.get("description")))
        if tuple(sorted(c["members"])) in renames:
            renamed += 1
        cur.execute(
            "INSERT INTO practice_clusters "
            "(group_id, cluster_name, cluster_description, aggregator_model_version, voting_agreement) "
            "VALUES (?, ?, ?, NULL, ?)",
            (GROUP_ID, name, desc, c.get("agreement")),
        )
        cluster_id_by_index[idx] = cur.lastrowid
        for i in c["members"]:
            cluster_of_flat[i] = idx

    # query_practice (group-dependent) — one row per extraction; raw cluster id (NULL = singleton →
    # long-tail). No relevance gate ran on the fixture → relevant = substantive = 1.
    unclustered = 0
    for flat_idx, extraction_id in flat_to_extraction_id.items():
        idx = cluster_of_flat.get(flat_idx)
        cluster_id = cluster_id_by_index.get(idx) if idx is not None else None
        if cluster_id is None:
            unclustered += 1
        cur.execute(
            "INSERT OR REPLACE INTO query_practice "
            "(group_id, extraction_id, relevant, substantive, gate_model_version, "
            " gate_prompt_version, practice_cluster_id, companion_label) "
            "VALUES (?, ?, 1, 1, NULL, NULL, ?, NULL)",
            (GROUP_ID, extraction_id, cluster_id),
        )

    # query_narrative (group-keyed) — D-4 dict verbatim (query/group_size are flagged C placeholders).
    cur.execute(
        "INSERT OR REPLACE INTO query_narrative "
        "(group_id, narrative_json, narrative_model_version, generation_timestamp) "
        "VALUES (?, ?, NULL, NULL)",
        (GROUP_ID, json.dumps(narrative)),
    )
    conn.commit()
    return {
        "canonical_group_members": len(member_qids),
        "practice_extractions": len(flat_to_extraction_id),
        "practice_clusters": len(cluster_id_by_index),
        "clusters_renamed_via_sidecar": renamed,
        "query_practice": len(flat_to_extraction_id),
        "query_practice_unclustered": unclustered,
    }


def migrate_cache_column(conn: sqlite3.Connection) -> str:
    """Rename query_viz_cache.query_id -> group_id if the table predates the group-keying. Row-
    preserving (SQLite RENAME COLUMN), so there is no window where the cache is empty."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(query_viz_cache)")]
    if not cols:
        return "no query_viz_cache yet (fresh DB)"
    if "group_id" in cols:
        return "already group-keyed"
    if "query_id" in cols:
        conn.execute("ALTER TABLE query_viz_cache RENAME COLUMN query_id TO group_id")
        conn.commit()
        return "renamed query_id -> group_id"
    return f"unexpected columns: {cols}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", required=True, help="migrate cache column + seed tables")
    ap.parse_args()
    conn = sqlite3.connect(DB)
    try:
        print("migrate cache column:", migrate_cache_column(conn))
        print("normalized tables seeded:", json.dumps(load_normalized_tables(conn)))
    finally:
        conn.close()
    print("done. run `python -m orchestration.driver --group q54069253` to bake the cache.")


if __name__ == "__main__":
    main()
