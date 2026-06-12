"""Stage ④ — cluster the group's kept practices: k=3 aggregator + co-association consensus (ADR 0007).

Runs the Step-2 aggregator K times; for each PAIR of practices counts how often the runs co-cluster
them; pairs co-occurring in a majority (≥ ⌈K/2⌉) are linked; connected components form the consensus
clustering (label-alignment-free). Each consensus head cluster (≥ min_cluster members) is named from
ITS OWN members via name_clusters — NOT the old run-0 plurality, so distinct clusters never inherit a
colliding umbrella label (the dup-name fix; grill 2026-06-03). A matched-k agglomerative companion on
SBERT-stand-in embeddings is recorded per practice (query_practice.companion_label) as the
cross-method defensibility signal (spec §4). Singletons stay practice_cluster_id = NULL → long-tail.

Writes practice_clusters (per group) + query_practice.practice_cluster_id / companion_label.
CLI: PYTHONPATH=src uv run --no-sync python -m breakdown.pipeline.cluster --group q54069253
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import normalize

import config
from breakdown.pipeline.llm import TIER_MODEL, embed, llm_call, usage_report
from breakdown.pipeline.name_clusters import name_clusters
from breakdown.pipeline.prompts import build_aggregator_messages
from breakdown.pipeline.schemas import AggregatorOutput


def _to_labels(agg: AggregatorOutput, n: int) -> list[int]:
    lab = [-1] * n
    for ci, c in enumerate(agg.clusters):
        for idx in c.member_indices:
            if 0 <= idx < n:
                lab[idx] = ci
    return lab


def _kept_practices(conn: sqlite3.Connection, group_id: str) -> list[tuple[int, str]]:
    return conn.execute(
        "SELECT pe.id, pe.practice_sentence FROM query_practice qp "
        "JOIN practice_extractions pe ON pe.id = qp.extraction_id "
        "WHERE qp.group_id = ? AND qp.relevant = 1 AND qp.substantive = 1 "
        "ORDER BY pe.id", (group_id,)).fetchall()


def cluster_group(
    conn: sqlite3.Connection,
    group_id: str,
    *,
    k: int = 3,
    min_cluster: int = 2,
    model_version: str | None = None,
) -> dict:
    rows = _kept_practices(conn, group_id)
    ext_ids = [r[0] for r in rows]
    sents = [r[1] for r in rows]
    n = len(sents)
    if n < min_cluster:
        raise SystemExit(f"group {group_id!r} has {n} kept practices (< min_cluster={min_cluster})")

    runs = llm_call(build_aggregator_messages(sents), AggregatorOutput, tier="aggregate", k_voting=k)
    runs = runs if isinstance(runs, list) else [runs]
    labelsets = [_to_labels(r, n) for r in runs]

    co = np.zeros((n, n), dtype=int)
    for lab in labelsets:
        for i in range(n):
            if lab[i] == -1:
                continue
            for j in range(i + 1, n):
                if lab[j] == lab[i]:
                    co[i, j] += 1
                    co[j, i] += 1
    thresh = k // 2 + 1
    _, comp = connected_components(csr_matrix((co >= thresh).astype(int)), directed=False)
    groups: dict[int, list[int]] = defaultdict(list)
    for i, c in enumerate(comp):
        groups[c].append(i)
    ordered = sorted(groups.values(), key=len, reverse=True)
    head = [m for m in ordered if len(m) >= min_cluster]

    # matched-k agglomerative companion on embeddings (cross-method agreement signal)
    companion = normalize(np.array(embed(sents)))
    comp_label = AgglomerativeClustering(n_clusters=max(len(ordered), 1)).fit_predict(companion)

    def agreement(members: list[int]) -> float:
        if len(members) < 2:
            return 1.0
        vals = [co[i, j] / k for a, i in enumerate(members) for j in members[a + 1:]]
        return sum(vals) / len(vals)

    # name every head cluster from its OWN members, in one call → mutually distinct labels
    labels = name_clusters([[sents[i] for i in m] for m in head]) if head else []

    cur = conn.cursor()
    cur.execute("DELETE FROM practice_clusters WHERE group_id = ?", (group_id,))
    cur.execute("UPDATE query_practice SET practice_cluster_id = NULL, companion_label = NULL "
                "WHERE group_id = ?", (group_id,))
    model_version = model_version or TIER_MODEL["aggregate"]
    for members, lab in zip(head, labels):
        cur.execute(
            "INSERT INTO practice_clusters (group_id, cluster_name, cluster_description, "
            "aggregator_model_version, voting_agreement) VALUES (?, ?, ?, ?, ?)",
            (group_id, lab["name"], lab["description"], model_version, round(agreement(members), 4)))
        cluster_id = cur.lastrowid
        for i in members:
            cur.execute(
                "UPDATE query_practice SET practice_cluster_id = ?, companion_label = ? "
                "WHERE group_id = ? AND extraction_id = ?",
                (cluster_id, int(comp_label[i]), group_id, ext_ids[i]))
    for members in ordered:  # singletons → long-tail (cluster NULL) but keep their companion label
        if len(members) < min_cluster:
            for i in members:
                cur.execute(
                    "UPDATE query_practice SET companion_label = ? WHERE group_id = ? AND extraction_id = ?",
                    (int(comp_label[i]), group_id, ext_ids[i]))
    conn.commit()

    within = [(i, j) for m in head for a, i in enumerate(m) for j in m[a + 1:]]
    overall = float(np.mean([co[i, j] / k for i, j in within])) if within else 1.0
    return {"practices": n, "consensus_clusters": len(ordered), "head_clusters": len(head),
            "singletons_longtail": sum(len(m) < min_cluster for m in ordered),
            "overall_within_agreement": round(overall, 4)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", required=True)
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        print(json.dumps(cluster_group(conn, args.group)))
    finally:
        conn.close()
    print(usage_report())


if __name__ == "__main__":
    main()
