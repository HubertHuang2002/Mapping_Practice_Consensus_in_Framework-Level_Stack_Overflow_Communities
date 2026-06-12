"""Stage ⑥ — per-query narrative of the practice-breakdown shape (signal-table v2, spec §1.2).

Renders the precomputed signals (query_signal + camp_signal, written by stage ⑤ aggregate_signals)
into a text SIGNAL TABLE, then asks the narrative LLM for DESCRIBE-ONLY prose. The verdicts (RQ-1
shape, RQ-2 authority overlay) are already decided in Python; the LLM only writes headline + body
faithful to them — it does not choose labels. The persisted record merges the computed verdicts with
the LLM prose. Time is intentionally omitted in this version.

Prereq: aggregate_signals(group_id) must have run (query_signal/camp_signal populated).
CLI: PYTHONPATH=src uv run --no-sync python -m breakdown.pipeline.narrative --group q65926492 --title "..."
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

import config
from breakdown.pipeline.llm import TIER_MODEL, llm_call, usage_report
from breakdown.pipeline.prompts import build_narrative_messages
from breakdown.pipeline.schemas import QueryNarrative

_QS_COLS = (
    "n_answers, n_head_camps, n_longtail, longtail_vote_share, effective_camps, top1_share, top2_share, "
    "shape_label, shape_fragile, vote_leader_cluster, top1_author, top1_pr_share, single_voice_dominated, "
    "top1_author_cluster, authority_diverges, top3_in_vote_leader, authority_coverage_overall"
)


def build_signal_table(conn: sqlite3.Connection, group_id: str, anchor_title: str) -> tuple[str, dict]:
    """Render query_signal + camp_signal into the DESCRIBE-ONLY signal table. Returns (table, qs dict)."""
    row = conn.execute(f"SELECT {_QS_COLS} FROM query_signal WHERE group_id = ?", (group_id,)).fetchone()
    if row is None:
        raise SystemExit(f"no query_signal for {group_id!r} — run stage ⑤ aggregate_signals first")
    qs = dict(zip([c.strip() for c in _QS_COLS.split(",")], row))
    camps = conn.execute(
        "SELECT cluster_id, cluster_name, vote_share, prevalence_share, author_pr_share, voting_agreement, "
        "top_author FROM camp_signal WHERE group_id = ? ORDER BY vote_share DESC", (group_id,)).fetchall()
    name_of = {c[0]: c[1] for c in camps}
    vl_name = camps[0][1] if camps else "(none)"
    pct = lambda x: f"{round(100 * (x or 0))}%"

    lines = [
        f"QUERY: {anchor_title}",
        f"SHAPE: {qs['shape_label']}{' (FRAGILE)' if qs['shape_fragile'] else ''}  —  vote-leader "
        f"〈{vl_name}〉 at {pct(qs['top1_share'])} (runner-up {pct(qs['top2_share'])}); "
        f"effective camps {qs['effective_camps']:.1f}",
        f"TOTALS: {qs['n_answers']} answers · {qs['n_head_camps']} camps + {qs['n_longtail']} long-tail "
        f"(long tail holds {pct(qs['longtail_vote_share'])} of votes)",
        "",
        "CAMPS (by vote share):",
        f"   {'vote%':>5}  {'prev%':>5}  {'authorPR%':>9}  {'agree':>5}  approach  [top author]",
    ]
    for cid, nm, vs, ps, aprs, agr, ta in camps:
        mark = "  <- #1 central" if cid == qs["top1_author_cluster"] else ""
        lines.append(f"   {pct(vs):>5}  {pct(ps):>5}  {pct(aprs):>9}  {agr or 0:>5.2f}  {nm}  [{ta or '—'}]{mark}")

    lines += ["", "AUTHORITY OVERLAY (raw PageRank — concentrated; centrality != correctness):"]
    cov = qs["authority_coverage_overall"] or 0
    if qs["top1_author"] is None or cov < 0.5:
        lines.append(f"  authority coverage only {pct(cov)} — too thin to read authority for this question.")
    else:
        one = " [ONE-VOICE-DOMINATED]" if qs["single_voice_dominated"] else ""
        verdict = "DIVERGES from the vote-leader" if qs["authority_diverges"] else "AGREES with the vote-leader"
        lines += [
            f"  most-central answerer: {qs['top1_author']} — {pct(qs['top1_pr_share'])} of this "
            f"question's network centrality{one}",
            f"  backs 〈{name_of.get(qs['top1_author_cluster'], '?')}〉 -> {verdict}",
            f"  top-3 central authors in vote-leader camp: {qs['top3_in_vote_leader']} of 3 · "
            f"authority coverage: {pct(cov)}",
            "NOTE: if the central voice AGREES with the vote-leader, discount it (endogenous — central "
            "because upvoted).",
        ]
    return "\n".join(lines), qs


def narrative_group(conn: sqlite3.Connection, group_id: str, *, anchor_title: str,
                    model_version: str | None = None) -> dict:
    table, qs = build_signal_table(conn, group_id, anchor_title)
    out: QueryNarrative = llm_call(build_narrative_messages(table), QueryNarrative, tier="narrative")
    camps = conn.execute(
        "SELECT cluster_id, cluster_name, vote_share FROM camp_signal WHERE group_id = ? "
        "ORDER BY vote_share DESC", (group_id,)).fetchall()
    name_of = {c[0]: c[1] for c in camps}
    share_of = {c[0]: c[2] for c in camps}
    # runner-up = highest-vote camp that is NOT the vote-leader (not just camps[1]): on a tie the
    # leader (query_signal max) and camps[1] (SQL order) can pick the same camp → "runner-up == leader".
    runner = next((c for c in camps if c[0] != qs["vote_leader_cluster"]), None)
    record = {
        "query": anchor_title, "group_size": qs["n_answers"],
        # machine-readable verdicts: COMPUTED in Python (query_signal/camp_signal), not chosen by the LLM.
        "shape": qs["shape_label"], "shape_fragile": bool(qs["shape_fragile"]),
        # RQ-1 vote axis: the crowd's leading PRACTICE, its share, and the runner-up
        "dominant_approach": name_of.get(qs["vote_leader_cluster"]),
        "vote_leader_share": qs["top1_share"],
        "runner_up": runner[1] if runner else None,
        "runner_up_share": runner[2] if runner else None,
        "effective_camps": qs["effective_camps"],
        # RQ-2 authority axis: the central voice, the PRACTICE it backs (so both axes name a practice →
        # diverge/agree is just "are the two practices the same"), and the relation
        "authority_backed": name_of.get(qs["top1_author_cluster"]),
        "authority_backed_share": share_of.get(qs["top1_author_cluster"]),
        "top1_author": qs["top1_author"], "top1_pr_share": qs["top1_pr_share"],
        "single_voice_dominated": bool(qs["single_voice_dominated"]),
        "authority_diverges": bool(qs["authority_diverges"]),
        "authority_coverage": qs["authority_coverage_overall"],
        # LLM prose (describe-only over the above)
        "headline": out.headline, "body": out.body,
        "_note": "shape/authority verdicts = Python (query_signal/camp_signal); headline/body = D-6 describe-only LLM",
    }
    conn.execute(
        "INSERT OR REPLACE INTO query_narrative (group_id, narrative_json, narrative_model_version, "
        "generation_timestamp) VALUES (?, ?, ?, ?)",
        (group_id, json.dumps(record, ensure_ascii=False),
         model_version or TIER_MODEL["narrative"], datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()
    return {"group_id": group_id, "shape": qs["shape_label"], "headline": out.headline,
            "dominant_approach": record["dominant_approach"], "group_size": qs["n_answers"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", required=True)
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--title", required=True, help="anchor question title")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        print(json.dumps(narrative_group(conn, args.group, anchor_title=args.title), ensure_ascii=False))
    finally:
        conn.close()
    print(usage_report())


if __name__ == "__main__":
    main()
