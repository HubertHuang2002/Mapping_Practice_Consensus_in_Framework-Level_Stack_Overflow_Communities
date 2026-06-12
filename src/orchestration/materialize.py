"""Table-sourced materialize — bake stage ⑦ assembled from the normalized store (ADR 0009),
not from the on-disk JSON. This is the spine's real path: the §3.2 tables are the source of truth.

Differs from breakdown.assemble.build_breakdown (the JSON-sourced bridge) in ONE deliberate way:
clusters are keyed by their persisted IDENTITY (practice_cluster_id), NOT by name. The fixture had
two distinct co-association clusters colliding on one label ("Direct set vs merge/immutability");
name-keying silently merged them. Keying by id surfaces them as the distinct communities the
consensus actually found (grill 2026-06-03). ClusterShell.id stays a readable name (frontend
contract) and is de-duplicated deterministically only when labels still collide — the re-namer
(name_clusters) gives them clean distinct labels so the suffix normally disappears.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from breakdown.layout import pack
from breakdown.fusion import fuse, vote_percentiles
from contract.ports import AuthorityProvider, CanonicalGroupProvider
from contract.types import AnswerCard, Breakdown, ClusterShell, PracticePoint, Signals, year_of

TAIL_ID = "long-tail"
TAIL_NAME = "long-tail (idiosyncratic)"


def _read_practices(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    """Each kept practice for this group + its raw cluster id (NULL = singleton → long-tail)."""
    rows = conn.execute(
        "SELECT pe.answer_id, pe.practice_rank, pe.practice_sentence, pe.conditions, "
        "       pe.evidence_type, qp.practice_cluster_id "
        "FROM query_practice qp JOIN practice_extractions pe ON pe.id = qp.extraction_id "
        "WHERE qp.group_id = ? AND qp.relevant = 1 AND qp.substantive = 1 "
        "ORDER BY pe.answer_id, pe.practice_rank",
        (group_id,),
    )
    return [
        {"answer_id": aid, "practice_index": rank, "text": text,
         "conditions": json.loads(conds or "[]"), "evidence_type": ev, "cluster_id": cid}
        for aid, rank, text, conds, ev, cid in rows
    ]


def _shell_ids(head_ids: list[int], name_of: dict[int, str]) -> dict[int, str]:
    """Map each head cluster id to a UNIQUE readable shell id (its name, deduped on collision)."""
    out: dict[int, str] = {}
    used: Counter = Counter()
    for cid in head_ids:  # caller passes head_ids in the final shell order (stable)
        base = name_of[cid]
        used[base] += 1
        out[cid] = base if used[base] == 1 else f"{base} ·{used[base]}"
    return out


def materialize_from_store(
    conn: sqlite3.Connection,
    group_id: str,
    group_provider: CanonicalGroupProvider,
    authority_provider: AuthorityProvider,
    narrative: dict | None = None,
    db_window: str | None = None,
    window_start: int | None = None,
    min_cluster: int = 2,
) -> Breakdown:
    practices = _read_practices(conn, group_id)
    name_of = dict(conn.execute(
        "SELECT id, cluster_name FROM practice_clusters WHERE group_id = ?", (group_id,)
    ))

    # per-camp signals (aggregate.py stage ⑤), keyed by practice_cluster_id (= head cluster id), so each
    # shell carries its OWN stats and the detail card narrates the SELECTED community, not the query.
    camp_by_cid = {
        r[0]: r for r in conn.execute(
            "SELECT cluster_id, vote_share, prevalence_n, prevalence_share, voting_agreement, "
            "author_pr_share, top_author, top_author_pr_share, authority_coverage, exemplar "
            "FROM camp_signal WHERE group_id = ?", (group_id,))
    }
    _qsig = conn.execute(
        "SELECT vote_leader_cluster, top1_author_cluster FROM query_signal WHERE group_id = ?",
        (group_id,)).fetchone()
    vote_leader_cid, authority_cid = (_qsig[0], _qsig[1]) if _qsig else (None, None)

    # head = clusters with >= min_cluster kept practices; order by size desc, then name, then id.
    placed_by_cid = Counter(p["cluster_id"] for p in practices if p["cluster_id"] is not None)
    head_ids = sorted(
        (cid for cid, n in placed_by_cid.items() if n >= min_cluster),
        key=lambda cid: (-placed_by_cid[cid], name_of[cid], cid),
    )
    shell_id_of = _shell_ids(head_ids, name_of)

    answers = {a.answer_id: a for a in group_provider.fetch(group_id).answers}

    # raw full-period PageRank per answer author (the force-field "size by authority" slider end): the RAW
    # value, NOT the saturated global percentile; None = author absent from the answerer graph.
    _author_ids = {a.owner_user_id for a in answers.values() if a.owner_user_id is not None}
    pr_of: dict[int, float] = {}
    if _author_ids:
        _q = ",".join("?" * len(_author_ids))
        pr_of = {uid: p for uid, p in conn.execute(
            f"SELECT user_id, pagerank_full FROM users WHERE user_id IN ({_q})", tuple(_author_ids)) if p}

    # answer-level quality Q = vote percentile WITHIN this group, over the answers that contribute a point
    contrib_ids = [p["answer_id"] for p in practices if answers.get(p["answer_id"]) is not None]
    q_of = vote_percentiles(contrib_ids, {aid: answers[aid].vote for aid in set(contrib_ids)})

    points: list[PracticePoint] = []
    missing = 0
    for p in practices:
        a = answers.get(p["answer_id"])
        if a is None:  # extracted answer not in the group — shouldn't happen for the proxy
            missing += 1
            continue
        cid = p["cluster_id"]
        cluster = shell_id_of[cid] if cid in shell_id_of else TAIL_ID
        signals = Signals(a.vote, a.is_accepted, a.date, year_of(a.date), a.reputation)
        auth = authority_provider.score(a)  # author-level network authority A (None = out-of-graph)
        # split the null cause: no author id → anonymous (unknowable); author present but not a node
        # in the answerer network (e.g. self-answer) → non_interactive. Renders distinctly.
        status = "scored" if auth is not None else ("anonymous" if a.owner_user_id is None else "non_interactive")
        weight = fuse(q_of.get(p["answer_id"], 0.0), auth)  # node size W = √(Q·A), or Q if A absent
        points.append(
            PracticePoint(p["answer_id"], p["practice_index"], p["text"], cluster,
                          auth, signals,
                          conditions=p["conditions"], evidence_type=p["evidence_type"],
                          authority_status=status, weight=weight,
                          pagerank=pr_of.get(a.owner_user_id))
        )
    if missing:
        print(f"  warning: {missing} kept practices had no matching group answer (skipped)")

    def _shell(cid: int) -> ClusterShell:
        cs = camp_by_cid.get(cid)
        camp_kw: dict = {}
        if cs:
            (_, v_share, prev_n, prev_share, agree, a_prs, t_auth, t_prs, a_cov, exe) = cs
            camp_kw = dict(vote_share=v_share, prevalence_n=prev_n, prevalence_share=prev_share,
                           voting_agreement=agree, author_pr_share=a_prs, top_author=t_auth,
                           top_author_pr_share=t_prs, authority_coverage=a_cov, exemplar=exe)
        return ClusterShell(shell_id_of[cid], name_of[cid], placed_by_cid[cid],
                            is_vote_leader=(cid == vote_leader_cid),
                            is_authority_backed=(cid == authority_cid), **camp_kw)

    shells = [_shell(cid) for cid in head_ids]
    placed = Counter(pt.cluster for pt in points)
    if placed[TAIL_ID]:
        shells.append(ClusterShell(TAIL_ID, TAIL_NAME, placed[TAIL_ID]))

    n_practices = Counter(pt.answer_id for pt in points)
    cards = [
        AnswerCard(aid, answers[aid].author, answers[aid].reputation, answers[aid].vote,
                   answers[aid].is_accepted, answers[aid].date,
                   year_of(answers[aid].date), n_practices[aid])  # body lazy-loaded via /answer/{id}
        for aid in dict.fromkeys(pt.answer_id for pt in points)  # first-seen order, deduped
    ]

    bd = Breakdown(group_id, authority_provider.source_name, group_provider.source_name,
                   shells, points, cards, narrative=narrative)
    if window_start is not None:  # out-of-window is a FIXTURE/snapshot concern: real cold groups are
        bd.db_window = db_window   # fully in-window (every member question is 2021–2026) so this stays 0.
        bd.n_out_of_window = sum(1 for c in cards if c.year is not None and c.year < window_start)
    return pack(bd)
