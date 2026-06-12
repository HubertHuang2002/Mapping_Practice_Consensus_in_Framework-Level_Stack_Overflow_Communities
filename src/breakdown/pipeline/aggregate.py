"""Stage ⑤ — aggregate_signals (signal-table v2): the SINGLE SOURCE read by narrator AND dashboard.

For one baked group, aggregate the kept practices into PER-CAMP numbers (prevalence / vote-mass +
authority overlay) + QUERY-LEVEL RQ-1 (shape) and RQ-2 (authority) measures, persisted to
camp_signal + query_signal. Pure computation ($0); idempotent. See docs/signal-table-v2.md. Slots
between ④ cluster and ⑥ narrative (narrative reads these tables).

Design (spec §1.2):
- RQ-1 SHAPE: votes own "consensus". Head-normalized vote shares → effective_camps (Laakso-Taagepera
  1/HHI) + top-share decision tree → consensus / polarization / fragmentation (Hegselmann-Krause).
- RQ-2 AUTHORITY = RAW-PageRank OVERLAY (NOT a percentile share): any percentile-then-sum collapses to
  prevalence (signal-table-v2.md §9); only RAW PageRank carries an authority signal distinct from
  prevalence — and that signal is concentrated in 1–2 hubs (real network structure, not a defect). So
  we report WHICH CENTRAL VOICES back which camp, with concentration (top1_pr_share) made explicit and
  gating a singular "one voice" framing when one person holds the majority. Centrality ≠ correctness;
  agreement with votes is partly endogenous (a hub is central BECAUSE upvoted) → divergence is the
  informative case. Each author is attributed once to a PRIMARY camp (their highest-vote answer's camp).
- PREVALENCE (unweighted mention count) is the third, conditional axis.

CLI: PYTHONPATH=src uv run --no-sync python -m breakdown.pipeline.aggregate --group q65926492
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone

import config

# --- RQ-1 shape decision tree (share-based; thresholds calibrated, not validated — §3/§7)
TOP1_CONSENSUS = 0.40
GAP_CONSENSUS = 0.15
TOP2_POLARIZATION = 0.60
AGREE_FRAGILE = 0.60
SINGLE_VOICE = 0.50   # top1 author holding >50% of group raw PR → singular "one voice" framing


def classify_shape(top1: float, top2: float) -> str:
    if top1 >= TOP1_CONSENSUS and (top1 - top2) >= GAP_CONSENSUS:
        return "consensus"
    if top1 + top2 >= TOP2_POLARIZATION:
        return "polarization"
    return "fragmentation"


def _merge_top2_flips(shares: list[float], base_shape: str) -> bool:
    if len(shares) < 2:
        return False
    s = sorted(shares, reverse=True)
    merged = sorted([s[0] + s[1], *s[2:]], reverse=True)
    return classify_shape(merged[0], merged[1] if len(merged) > 1 else 0.0) != base_shape


def aggregate_signals(conn: sqlite3.Connection, group_id: str, group_provider, authority_provider=None) -> dict:
    # authority_provider unused (kept for a stable call signature) — RQ-2 reads raw pagerank_full directly.
    rows = conn.execute(
        "SELECT qp.practice_cluster_id, pe.answer_id, pe.practice_sentence FROM query_practice qp "
        "JOIN practice_extractions pe ON pe.id = qp.extraction_id "
        "WHERE qp.group_id = ? AND qp.relevant = 1 AND qp.substantive = 1 ORDER BY pe.id",
        (group_id,)).fetchall()
    clusters = {cid: (nm, ag) for cid, nm, ag in conn.execute(
        "SELECT id, cluster_name, voting_agreement FROM practice_clusters WHERE group_id = ?", (group_id,))}
    answers = {a.answer_id: a for a in group_provider.fetch(group_id).answers}

    def vote_of(aid: int) -> float:
        # floor at 0: a net-downvoted answer carries no ENDORSEMENT mass (SO scores can be negative).
        # keeps every vote_share in [0,1] and summing to 1, so HHI / effective_camps stay valid.
        a = answers.get(aid)
        return max(float(a.vote), 0.0) if a and a.vote else 0.0

    def author_of(aid: int):
        a = answers.get(aid)
        return a.owner_user_id if a else None

    # raw PageRank per author (the ONLY representation that beats prevalence — §9); None = out of graph
    author_ids = {author_of(aid) for _, aid, _ in rows if author_of(aid) is not None}
    pr: dict[int, float] = {}
    name: dict[int, str] = {}
    if author_ids:
        q = ",".join("?" * len(author_ids))
        for uid, p, dn in conn.execute(
                f"SELECT user_id, pagerank_full, display_name FROM users WHERE user_id IN ({q})",
                tuple(author_ids)):
            if p:
                pr[uid] = p
            name[uid] = dn or str(uid)

    # bucket practices by camp; collect long-tail; per-author (camp, vote) for primary-camp attribution
    camp_members: dict[int, list[tuple[int, str]]] = defaultdict(list)  # cid -> [(aid, sentence)]
    author_camps: dict[int, list[tuple[int, float]]] = defaultdict(list)  # uid -> [(cid, vote)]
    all_aids: set[int] = set()
    head_aids: set[int] = set()
    n_longtail = 0
    for cid, aid, sent in rows:
        all_aids.add(aid)
        if cid is None:
            n_longtail += 1
        else:
            camp_members[cid].append((aid, sent))
            head_aids.add(aid)
            uid = author_of(aid)
            if uid is not None:
                author_camps[uid].append((cid, vote_of(aid)))
    n_practices = len(rows)

    # each author → PRIMARY camp = camp of their highest-vote practice (tie → most-frequent camp)
    primary_camp: dict[int, int] = {}
    for uid, cv in author_camps.items():
        freq = Counter(c for c, _ in cv)
        primary_camp[uid] = max(cv, key=lambda x: (x[1], freq[x[0]]))[0]

    # ---- per-camp: prevalence + vote (RQ-1) ----
    camps: dict[int, dict] = {}
    for cid, members in camp_members.items():
        aids = list({aid for aid, _ in members})
        nm, agree = clusters.get(cid, ("(unnamed)", 0.0))
        camps[cid] = {
            "cluster_id": cid, "cluster_name": nm,
            "exemplar": max(members, key=lambda m: vote_of(m[0]))[1],
            "prevalence_n": len(aids), "vote_sum": sum(vote_of(aid) for aid in aids),
            "voting_agreement": agree or 0.0,
        }
    total_vote = sum(c["vote_sum"] for c in camps.values())
    for c in camps.values():
        c["vote_share"] = (c["vote_sum"] / total_vote) if total_vote else 0.0

    # ---- per-camp: authority OVERLAY (raw PR, authors attributed to primary camp) ----
    group_pr_total = sum(pr.get(uid, 0.0) for uid in primary_camp)
    camp_authors: dict[int, list[int]] = defaultdict(list)
    for uid, cid in primary_camp.items():
        camp_authors[cid].append(uid)
    ranked_authors = sorted(pr, key=lambda u: pr[u], reverse=True)  # by raw PR desc
    top3 = set(ranked_authors[:3])
    for cid, c in camps.items():
        auths = camp_authors.get(cid, [])
        camp_pr = sum(pr.get(u, 0.0) for u in auths)
        top_u = max(auths, key=lambda u: pr.get(u, 0.0)) if auths else None
        c["author_pr_share"] = (camp_pr / group_pr_total) if group_pr_total else 0.0
        c["top_author"] = name.get(top_u) if top_u is not None else None
        c["top_author_pr_share"] = (pr.get(top_u, 0.0) / group_pr_total) if (top_u is not None and group_pr_total) else 0.0
        c["n_top3_authors"] = sum(1 for u in auths if u in top3)
        cov_known = sum(1 for aid in {aid for aid, _ in camp_members[cid]} if author_of(aid) in pr)
        c["authority_coverage"] = (cov_known / c["prevalence_n"]) if c["prevalence_n"] else 0.0

    # ---- query-level RQ-1 (shape) ----
    head = sorted(camps.values(), key=lambda c: c["vote_share"], reverse=True)
    n_head = len(head)
    shares = [c["vote_share"] for c in head]
    top1 = shares[0] if shares else 0.0
    top2 = shares[1] if len(shares) > 1 else 0.0
    ss = sum(s * s for s in shares)
    effective_camps = (1.0 / ss) if ss > 0 else 0.0
    shape_label = classify_shape(top1, top2) if n_head else "fragmentation"
    hs = sum(c["prevalence_n"] for c in camps.values())
    mean_agree = (sum(c["voting_agreement"] * c["prevalence_n"] for c in camps.values()) / hs) if hs else 1.0
    shape_fragile = (n_head == 0) or (mean_agree < AGREE_FRAGILE) or _merge_top2_flips(shares, shape_label)
    vote_leader = max(camps, key=lambda cid: camps[cid]["vote_share"]) if camps else None
    prevalence_leader = max(camps, key=lambda cid: camps[cid]["prevalence_n"]) if camps else None
    prevalence_diverges = (prevalence_leader is not None and prevalence_leader != vote_leader)

    # ---- query-level RQ-2 (authority overlay) ----
    top1_author = top1_pr_share = top1_author_cluster = None
    top3_pr_share = 0.0
    if ranked_authors and group_pr_total:
        u1 = ranked_authors[0]
        top1_author = name.get(u1)
        top1_pr_share = pr[u1] / group_pr_total
        top1_author_cluster = primary_camp.get(u1)
        top3_pr_share = sum(pr[u] for u in ranked_authors[:3]) / group_pr_total
    single_voice_dominated = (top1_pr_share is not None and top1_pr_share > SINGLE_VOICE)
    authority_diverges = (top1_author_cluster is not None and top1_author_cluster != vote_leader)
    top3_in_vote_leader = sum(1 for u in ranked_authors[:3] if primary_camp.get(u) == vote_leader)
    authority_coverage_overall = (len([1 for aid in all_aids if author_of(aid) in pr]) / len(all_aids)) if all_aids else 0.0

    # ---- long tail ----
    grand_vote = sum(vote_of(aid) for aid in all_aids)
    head_vote = sum(vote_of(aid) for aid in head_aids)
    longtail_vote_share = ((grand_vote - head_vote) / grand_vote) if grand_vote else 0.0

    # ---- persist (idempotent) ----
    cur = conn.cursor()
    cur.execute("DELETE FROM camp_signal WHERE group_id = ?", (group_id,))
    cur.execute("DELETE FROM query_signal WHERE group_id = ?", (group_id,))
    for c in camps.values():
        cur.execute(
            "INSERT INTO camp_signal (group_id, cluster_id, cluster_name, exemplar, prevalence_n, "
            "prevalence_share, vote_sum, vote_share, author_pr_share, top_author, top_author_pr_share, "
            "n_top3_authors, authority_coverage, voting_agreement) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (group_id, c["cluster_id"], c["cluster_name"], c["exemplar"], c["prevalence_n"],
             round(c["prevalence_n"] / len(all_aids), 4) if all_aids else 0.0,
             c["vote_sum"], round(c["vote_share"], 4), round(c["author_pr_share"], 4),
             c["top_author"], round(c["top_author_pr_share"], 4), c["n_top3_authors"],
             round(c["authority_coverage"], 4), round(c["voting_agreement"], 4)))
    cur.execute(
        "INSERT INTO query_signal (group_id, n_answers, n_practices, n_head_camps, n_longtail, "
        "longtail_vote_share, effective_camps, top1_share, top2_share, gap, shape_label, shape_fragile, "
        "vote_leader_cluster, top1_author, top1_pr_share, top3_pr_share, single_voice_dominated, "
        "top1_author_cluster, authority_diverges, top3_in_vote_leader, authority_coverage_overall, "
        "prevalence_leader_cluster, prevalence_diverges, computed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (group_id, len(all_aids), n_practices, n_head, n_longtail, round(longtail_vote_share, 4),
         round(effective_camps, 4), round(top1, 4), round(top2, 4), round(top1 - top2, 4), shape_label,
         int(shape_fragile), vote_leader, top1_author,
         round(top1_pr_share, 4) if top1_pr_share is not None else None, round(top3_pr_share, 4),
         int(single_voice_dominated), top1_author_cluster, int(authority_diverges), top3_in_vote_leader,
         round(authority_coverage_overall, 4), prevalence_leader, int(prevalence_diverges),
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()

    return {"group_id": group_id, "shape": shape_label, "effective_camps": round(effective_camps, 2),
            "shape_fragile": bool(shape_fragile), "top1_author": top1_author,
            "top1_pr_share": round(top1_pr_share, 2) if top1_pr_share is not None else None,
            "single_voice": bool(single_voice_dominated), "authority_diverges": bool(authority_diverges),
            "top3_in_vote_leader": top3_in_vote_leader}


def main() -> None:
    from canonical.groups import DbCanonicalGroups

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", required=True)
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        import json
        print(json.dumps(aggregate_signals(conn, args.group, DbCanonicalGroups(args.db))))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
