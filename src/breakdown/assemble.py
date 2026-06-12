"""Assemble the practice Breakdown — the integration point where the two seams plug in.

Joins Module D's own offline outputs (practice extraction + clustering) with the answers from a
CanonicalGroupProvider (C) and the authority from an AuthorityProvider (B), collapses singleton
clusters into one long-tail bucket, then lays it out. Extraction file order is authoritative for
the flat practice index, so the frozen clustering (keyed by that index) lines up unchanged.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from contract.types import AnswerCard, Breakdown, ClusterShell, PracticePoint, Signals, year_of
from breakdown.layout import pack
from breakdown.fusion import fuse, vote_percentiles
from contract.ports import AuthorityProvider, CanonicalGroupProvider

TAIL_ID = "long-tail"
TAIL_NAME = "long-tail (idiosyncratic)"


def build_breakdown(
    group_id: str,
    group_provider: CanonicalGroupProvider,
    authority_provider: AuthorityProvider,
    extractions_path: Path,
    clusters_path: Path,
    min_cluster: int = 2,
    narrative: dict | None = None,
) -> Breakdown:
    extractions = json.loads(Path(extractions_path).read_text())
    assignment = json.loads(Path(clusters_path).read_text())["clusters"]

    # flat practice index -> cluster name, in extraction order (the order clustering was run on)
    cluster_of_flat: dict[int, str] = {}
    for c in assignment:
        for i in c["members"]:
            cluster_of_flat[i] = c["name"]
    head = {name for name, n in Counter(cluster_of_flat.values()).items() if n >= min_cluster}

    answers = {a.answer_id: a for a in group_provider.fetch(group_id).answers}

    # answer-level quality Q = vote percentile WITHIN this group, over the answers that contribute a point
    contrib_ids = [ans["answer_id"] for ans in extractions
                   if answers.get(ans["answer_id"]) is not None and ans["practices"]]
    q_of = vote_percentiles(contrib_ids, {aid: answers[aid].vote for aid in set(contrib_ids)})

    points: list[PracticePoint] = []
    flat = 0
    missing = 0
    for ans in extractions:  # extraction order == flat-index order
        a = answers.get(ans["answer_id"])
        for j, p in enumerate(ans["practices"]):
            name = cluster_of_flat.get(flat)
            flat += 1
            if a is None:  # extracted answer not in the group — shouldn't happen for the proxy
                missing += 1
                continue
            cluster = name if name in head else TAIL_ID
            signals = Signals(a.vote, a.is_accepted, a.date, year_of(a.date), a.reputation)
            auth = authority_provider.score(a)  # author-level network authority A (None = out-of-graph)
            # split the null cause: no author id → anonymous (unknowable); author present but not a
            # node in the answerer network (e.g. self-answer) → non_interactive. Renders distinctly.
            status = "scored" if auth is not None else ("anonymous" if a.owner_user_id is None else "non_interactive")
            weight = fuse(q_of.get(ans["answer_id"], 0.0), auth)  # node size W = √(Q·A), or Q if A absent
            points.append(
                PracticePoint(ans["answer_id"], j, p["practice"], cluster,
                              auth, signals,
                              conditions=p.get("conditions", []), evidence_type=p.get("evidence_type", ""),
                              authority_status=status, weight=weight)
            )
    if missing:
        print(f"  warning: {missing} extracted practices had no matching group answer (skipped)")

    placed = Counter(p.cluster for p in points)
    # size desc, then name asc — the name tie-break keeps bake DETERMINISTIC (equal-size clusters
    # would otherwise reorder across processes via set-iteration / PYTHONHASHSEED, breaking the
    # version-stamped idempotency contract and the golden-master diff).
    head_sorted = sorted((nm for nm in head if placed[nm]), key=lambda nm: (-placed[nm], nm))
    shells = [ClusterShell(nm, nm, placed[nm]) for nm in head_sorted]
    if placed[TAIL_ID]:
        shells.append(ClusterShell(TAIL_ID, TAIL_NAME, placed[TAIL_ID]))

    # one detail card per answer that contributed a point (for the click-to-detail panel)
    n_practices = Counter(p.answer_id for p in points)
    cards: list[AnswerCard] = []
    for aid in dict.fromkeys(p.answer_id for p in points):  # first-seen order, deduped
        a = answers[aid]
        cards.append(AnswerCard(aid, a.author, a.reputation, a.vote, a.is_accepted,
                                a.date, year_of(a.date), n_practices[aid]))  # body lazy via /answer/{id}

    breakdown = Breakdown(
        group_id, authority_provider.source_name, group_provider.source_name, shells, points, cards,
        narrative=narrative,
    )
    return pack(breakdown)
