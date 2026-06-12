"""LLM equivalence gate for the canonical resolver (Module C 2b).

The gate is the canonical-group BOUNDARY — a per-candidate "is this the same SPECIFIC question?"
decision (set membership), NOT a reranker (which only reorders). It reads title + a body_text snippet
so it can tell "same specific problem" from "merely same topic" (the call cosine alone can't make).

Shape (PLAN step 6b): each candidate is judged INDEPENDENTLY (one per call), fired CONCURRENTLY.
Equivalence to the query is a per-(query, candidate) decision, so it must NOT depend on which other
candidates happen to share the prompt. The old rank-ordered BATCH=10 made membership ORDER-SENSITIVE:
shuffling the same 100-candidate pool churned ~1/3 of members (gate-to-gate Jaccard ~0.65 vs ~0.95
independent) — a design artifact that turned every phrasing-driven retrieval rerank into a different
gated set. Independent judging is order-invariant (validated: shuffle-Jaccard 0.95) at ~2.2x the gate
spend (nano: ~$0.005->$0.009 per resolve, <2% of a bake) and equal/better wall time once concurrency is
widened. Model = the `gate` tier (gpt-5.4-nano) via the shared llm_call seam.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pydantic import BaseModel

from breakdown.pipeline.llm import llm_call  # shared LLM seam (canonical→breakdown edge; see PLAN step 10)

SNIPPET_CHARS = 400  # body_text prefix per candidate — SO questions front-load the actual problem
BATCH = 1            # candidates per gate call — 1 = independent, order-invariant judging (see docstring)
MAX_WORKERS = 16     # concurrent gate calls — batch=1 → ~100 tiny calls/resolve (~7 rounds). Halved from
                     # 32 to cap simultaneous connection exposure / rate-limit pressure. NOTE: this only
                     # lowers how OFTEN a stalled call is hit — one stall still freezes the in-order
                     # ex.map() below; the real guard is the client request timeout in llm.py.


@dataclass
class GateResult:
    question_id: int
    rank: int
    score: float       # cosine retrieval score (carried through for provenance)
    equivalent: bool
    confidence: float  # gate's own [0,1] confidence


class _Verdict(BaseModel):
    num: int
    equivalent: bool
    confidence: float


class _GateBatch(BaseModel):
    results: list[_Verdict]


def _messages(query: str, batch: list[tuple[int, str, str]]) -> list[dict]:
    """batch: list of (display_num, title, snippet)."""
    lines = [f"{num}. {title} | {snip}".strip() for num, title, snip in batch]
    block = "\n".join(lines)
    system = (
        "You are a strict question-equivalence classifier. Two questions are EQUIVALENT only when "
        "they ask the same SPECIFIC problem (same core issue a developer is stuck on), not merely "
        "the same topic or technology. Judge each candidate independently."
    )
    user = (
        f'User question: "{query}"\n\n'
        f"For EACH numbered candidate below, decide whether it is equivalent to the user question "
        f"and give a confidence in [0,1].\n\nCandidates:\n{block}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def gate_candidates(
    query: str,
    candidates: list[tuple[int, float, int, str, str]],  # (qid, score, rank, title, body_text)
    *,
    batch_size: int = BATCH,
    max_workers: int = MAX_WORKERS,
) -> list[GateResult]:
    """Judge every candidate's equivalence to *query* in concurrent small batches. Output order is
    not guaranteed; callers key on question_id. A batch whose call fails / drops a num defaults that
    candidate to not-equivalent (conf 0.0) — the gate never silently inflates membership."""
    if not candidates:
        return []

    batches = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]

    def run(batch: list[tuple[int, float, int, str, str]]) -> list[GateResult]:
        prompt_batch = [
            (j, title, (body or "")[:SNIPPET_CHARS].replace("\n", " ").strip())
            for j, (_, _, _, title, body) in enumerate(batch, 1)
        ]
        try:
            gb: _GateBatch = llm_call(_messages(query, prompt_batch), _GateBatch, tier="gate")
            vmap = {v.num: v for v in gb.results}
        except Exception:
            vmap = {}
        out = []
        for j, (qid, score, rank, _, _) in enumerate(batch, 1):
            v = vmap.get(j)
            out.append(GateResult(
                question_id=qid, rank=rank, score=score,
                equivalent=bool(v.equivalent) if v else False,
                confidence=float(v.confidence) if v else 0.0,
            ))
        return out

    results: list[GateResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for batch_out in ex.map(run, batches):
            results.extend(batch_out)
    return results
