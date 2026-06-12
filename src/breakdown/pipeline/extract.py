"""Stage ②③ — extract practices from each answer + RELEVANCE-gate them for THIS group (ADR 0007 / 0009).

Two distinct LLM acts merged into one call (the validated D-1g method): (②) EXTRACTION pulls every
practice an answer proposes, then (③) the RELEVANCE GATE tags each relevant/substantive against the
group's canonical problem (it sees the full answer body). This relevance gate is a different gate
from the resolver's EQUIVALENCE gate (canonical/gate.py, which decides group membership); naming them
apart keeps the term clear. evidence_type is deterministic (is a code BLOCK present?), not an LLM
judgment. Writes the answer-keyed practice_extractions cache + the group-keyed query_practice gate
flags; kept = relevant AND substantive (a Python post-filter downstream, so the drop stays auditable).

Reads answers from the real DB (questions/answers) for the group's in-window member questions, plus
the out-of-window canonical answers from the proxy snapshot when given (in-window live queries, step
9, need no snapshot). Single-group plumbing: extraction is re-run per answer (delete+insert); the
answer-keyed cross-group reuse (extract once, gate per group) is deferred to the multi-group pass.

CLI: PYTHONPATH=src uv run --no-sync python -m breakdown.pipeline.extract --group q54069253 --limit 3
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config
from breakdown.pipeline.llm import TIER_MODEL, llm_call, usage_report
from breakdown.pipeline.prompts import build_extraction_gated_messages
from breakdown.pipeline.schemas import AnswerExtractionGated

MAX_CONDITIONS = 3  # cardinality enforced in Python, not in the strict schema
EXTRACT_WORKERS = 8  # concurrent extract calls (mirrors the gate pool; pure latency win, not cost)
_FENCE = re.compile(r"```.*?```", re.S)                      # markdown fenced block (snapshot)
_DBBLOCK = re.compile(r"(^|\n)[ \t]*\[CODE\][ \t]*(\n|$)")   # [CODE] standalone line (DB block)


def evidence_type(body: str) -> str:
    """'prose' (no code block) | 'code' (essentially only code) | 'both' (code + prose)."""
    body = body or ""
    if not (_FENCE.search(body) or _DBBLOCK.search(body)):
        return "prose"
    prose = _DBBLOCK.sub(" ", _FENCE.sub(" ", body)).replace("[CODE]", " ")
    return "code" if len(re.sub(r"\s+", " ", prose).strip()) < 40 else "both"


def load_group_answers(conn: sqlite3.Connection, group_id: str,
                       snapshot_path: str | Path | None = None) -> list[dict]:
    """Group's answers = out-of-window canonical answers from the snapshot (if any) + in-window dup
    answers from the DB, joined to their question title (the extraction prompt's parent context)."""
    answers: list[dict] = []
    if snapshot_path:  # fixture-only: out-of-window canonical answers from the proxy snapshot.
        snap = json.loads(Path(snapshot_path).read_text())  # live cold path passes snapshot_path=None
        cq = snap["canonical_question"]
        title = cq.get("title") if isinstance(cq, dict) else None
        answers += [
            {"answer_id": a["answer_id"], "question_id": a["question_id"], "parent_title": title,
             "source": "canonical", "body_text": a["body_text"], "score": a["score"],
             "is_accepted": a["is_accepted"]}
            for a in snap["canonical_answers"]
        ]
    # Members = the 'equivalent' rows (real resolver); legacy proxy-seed rows have NULL gate_decision
    # and are also members. 'borderline' / 'not_equivalent' rows are provenance, not pooled.
    qids = [r[0] for r in conn.execute(
        "SELECT question_id FROM canonical_group WHERE group_id = ? "
        "AND (gate_decision IS NULL OR gate_decision = 'equivalent')", (group_id,))]
    if qids:
        qmarks = ",".join("?" * len(qids))
        rows = conn.execute(
            f"SELECT a.answer_id, a.question_id, q.title, a.body_text, a.score, a.is_accepted "
            f"FROM answers a JOIN questions q ON q.question_id = a.question_id "
            f"WHERE a.question_id IN ({qmarks}) ORDER BY a.score DESC", qids)
        answers += [
            {"answer_id": aid, "question_id": qid, "parent_title": title,
             "source": "dup", "body_text": body or "", "score": score, "is_accepted": acc}
            for aid, qid, title, body, score, acc in rows
        ]
    return answers


def _extract_one(canonical_problem: str, a: dict) -> AnswerExtractionGated | None:
    """One answer's merged extract+gate LLM call (no DB — safe to run in a worker thread). Returns
    None on failure so a single bad answer never aborts the batch (mirrors the gate's fault tolerance)."""
    try:
        return llm_call(
            build_extraction_gated_messages(canonical_problem, a["parent_title"], a["body_text"]),
            AnswerExtractionGated, tier="extract")
    except Exception:
        return None


def extract_and_gate(
    conn: sqlite3.Connection,
    group_id: str,
    answers: list[dict],
    canonical_problem: str,
    *,
    model_version: str | None = None,
    prompt_version: str = "d1g-v1",
    progress: Callable[[int, int], None] | None = None,
    max_workers: int = EXTRACT_WORKERS,
) -> dict:
    """Extract + gate each answer (merged LLM call) → practice_extractions + query_practice.

    The per-answer calls are INDEPENDENT, so they run CONCURRENTLY in a thread pool (like the
    equivalence gate — LLM latency is output-bound, not CPU-bound); the DB writes then run
    SEQUENTIALLY on this thread because SQLite is single-writer and lastrowid is connection state.
    Pure latency win: same calls, same tokens, same result — rows are answer-keyed so write order is
    irrelevant. `progress(k, n)` fires as each call completes (k climbs to n in completion order)."""
    model_version = model_version or TIER_MODEL["extract"]
    total = len(answers)

    # Phase 1 — concurrent extraction (NO DB inside the threads; just LLM calls)
    extracted: list[tuple[dict, AnswerExtractionGated | None]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_extract_one, canonical_problem, a): a for a in answers}
        for fut in as_completed(futs):
            extracted.append((futs[fut], fut.result()))
            done += 1
            if progress:
                progress(done, total)

    # Phase 2 — sequential DB writes (single-writer; idempotent per-answer delete+insert)
    cur = conn.cursor()
    n_practices = n_kept = 0
    et_counts: Counter = Counter()
    for a, ex in extracted:
        old = [r[0] for r in cur.execute(
            "SELECT id FROM practice_extractions WHERE answer_id = ?", (a["answer_id"],))]
        if old:
            qmarks = ",".join("?" * len(old))
            cur.execute(f"DELETE FROM query_practice WHERE extraction_id IN ({qmarks})", old)
            cur.execute("DELETE FROM practice_extractions WHERE answer_id = ?", (a["answer_id"],))
        if ex is None:  # the call failed — leave this answer with no practices (an auditable gap)
            continue
        et = evidence_type(a["body_text"])
        et_counts[et] += len(ex.practices)
        for rank, p in enumerate(ex.practices):
            conds = json.dumps(p.conditions[:MAX_CONDITIONS])
            cur.execute(
                "INSERT INTO practice_extractions (answer_id, practice_rank, practice_sentence, "
                "conditions, evidence_type, extract_model_version, extract_prompt_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (a["answer_id"], rank, p.practice, conds, et, model_version, prompt_version))
            cur.execute(
                "INSERT INTO query_practice (group_id, extraction_id, relevant, substantive, "
                "gate_model_version, gate_prompt_version, practice_cluster_id, companion_label) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                (group_id, cur.lastrowid, int(p.relevant), int(p.substantive),
                 model_version, prompt_version))
            n_practices += 1
            n_kept += int(p.relevant and p.substantive)
    conn.commit()
    return {"answers": len(answers), "practices": n_practices, "kept": n_kept,
            "evidence_type": dict(et_counts)}


def main() -> None:
    from breakdown.pipeline.prompts import EXAMPLE_PROBLEM

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", required=True)
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--snapshot", default=None, help="proxy snapshot for out-of-window canonical answers")
    ap.add_argument("--limit", type=int, default=0, help="cap answers (0 = all) — plumbing smoke")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        answers = load_group_answers(conn, args.group, args.snapshot)
        if args.limit:
            answers = answers[:args.limit]
        print(f"loaded {len(answers)} answers | tier=extract ({TIER_MODEL['extract']})")
        print(json.dumps(extract_and_gate(conn, args.group, answers, EXAMPLE_PROBLEM)))
    finally:
        conn.close()
    print(usage_report())


if __name__ == "__main__":
    main()
