"""Pydantic schemas for Module D LLM roles (OpenAI strict structured outputs).

Strict-mode rules (PLAN ▼Q2): all fields required, additionalProperties:false (SDK sets
it), no defaults. `reasoning` is first so CoT is generated before the answer fields.
Cardinality (conditions ≤ 3) is enforced in Python post-hoc, NOT in the schema — it is the
least portable strict keyword (PLAN ▼Q2).

Framing (grill 2026-05-29): a query is an open implementation problem; the output is the
set of community practices for it. So one answer may propose several practices (votes for
several approaches) — hence `practices` is a list (M3). Empty list = the answer proposes no
implementation practice (pure "why" explanation / "me too" / thanks). Pure-diagnosis
answers are intentionally excluded: diagnosis convergence is correctness, not the
consensus-practice signal.
"""
from typing import Literal

from pydantic import BaseModel


class Practice(BaseModel):
    """One implementation practice (recommended action/approach) proposed by an answer."""

    practice: str  # 1 declarative sentence: the recommended action/approach
    conditions: list[str]  # ONLY scope caveats the answer explicitly states; [] otherwise.
    evidence_type: Literal["prose", "code", "both"]  # how THIS practice is evidenced


class AnswerExtraction(BaseModel):
    """Step-1 output for one SO answer (ADR 0002, CONTEXT.md)."""

    reasoning: str  # brief CoT; generated first, not persisted
    practices: list[Practice]  # 0..N; empty = answer proposes no implementation practice


class PracticeVerdict(BaseModel):
    """Gate verdict for ONE candidate practice (reason first so CoT precedes the booleans)."""

    reason: str  # brief justification; not persisted
    practice_index: int  # echoes the 0-based input index
    relevant: bool  # is it an approach to THE canonical problem (vs a different React issue)?
    substantive: bool  # standalone & generalizable (not a placeholder / one-off per-case fix)?


class RelevanceGate(BaseModel):
    """Step-1b: per-practice relevance + substance gate for one answer (D-owned, complements C)."""

    verdicts: list[PracticeVerdict]  # one per input practice, any order (mapped by practice_index)


class ExtractedPractice(BaseModel):
    """Step-1+1b merged: one extracted practice carrying its inline gate verdict.

    Field order is deliberate: the extraction fields (practice/conditions) come BEFORE the gate
    fields so the model commits the practice first, THEN judges it — extract generously, gate
    second (protects minority-approach recall). gate_reason precedes the two booleans so its CoT
    is generated before the verdict. NOTE: evidence_type is NOT judged here — it is a structural
    property of the source answer (does it contain a code block?), computed deterministically in
    Python at answer level (see d1_extract_gated.evidence_type), not by the LLM.
    """

    practice: str  # 1 declarative sentence: the recommended action/approach
    conditions: list[str]  # ONLY scope caveats the answer explicitly states; [] otherwise.
    gate_reason: str  # brief justification for the two booleans; written before them, not persisted
    relevant: bool  # approach to THE canonical problem (vs a different React subsystem)?
    substantive: bool  # standalone & generalizable (not a placeholder / one-off per-case fix)?


class AnswerExtractionGated(BaseModel):
    """Step-1+1b merged output for one SO answer: extract practices AND gate them in one call.

    Kept practice = relevant AND substantive (deterministic post-filter in Python, so the drop
    stays auditable). Empty list = answer proposes no implementation practice.
    """

    reasoning: str  # brief extraction CoT; generated first, not persisted
    practices: list[ExtractedPractice]  # 0..N, each with its inline gate verdict


class Cluster(BaseModel):
    """A group of practices representing the same underlying approach (Step-2)."""

    name: str  # short label for the approach
    description: str  # <= 2 sentences
    member_indices: list[int]  # 0-based indices into the input practice list


class AggregatorOutput(BaseModel):
    """Step-2: LLM aggregator clusters the practices of one canonical group (ADR 0002)."""

    reasoning: str  # brief CoT; not persisted
    clusters: list[Cluster]


class QueryNarrative(BaseModel):
    """D-4: per-query narrative of the practice-breakdown shape — DESCRIBE-ONLY prose (signal-table v2).

    The machine-readable verdicts (RQ-1 shape, RQ-2 authority overlay, leaders) are computed in Python
    (query_signal) and passed IN via the signal table; the LLM does NOT choose labels — it only writes
    prose faithful to those numbers, never inventing a pattern. reasoning first (CoT), not persisted.
    Vote-shape leads; then the authority overlay (the named most-central voice + its concentration),
    with caveats when the shape is fragile, authority coverage is low, or authority is one-voice-dominated.
    """

    reasoning: str  # CoT over the signal table before writing; not persisted.
    headline: str   # one-line punchline (dashboard hero text), <= 14 words
    body: str       # 2-4 sentences: vote-shape first, then the authority overlay; describe only, never invent
