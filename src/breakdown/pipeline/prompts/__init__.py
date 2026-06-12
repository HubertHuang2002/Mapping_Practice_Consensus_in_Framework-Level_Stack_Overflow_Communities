"""Pipeline LLM prompts, one module per stage (split out of the old monolithic prompts.py).

Map to the consensus pipeline names (CONTEXT Flagged 2026-06-04):
  ③ Extractor + ④ Relevance gate -> extract_gate.py   (the one that was overfit to useState; fixed)
  ⑤ Cluster aggregator           -> aggregate.py
  ⑦ Narrator                     -> narrative.py
(② Equivalence gate lives in canonical/gate.py; ⑥ Cluster namer in name_clusters.py — single-purpose
files already.) Re-exported here so callers keep importing from `breakdown.pipeline.prompts`.
"""
from __future__ import annotations

from breakdown.pipeline.prompts.aggregate import build_aggregator_messages
from breakdown.pipeline.prompts.extract_gate import (
    EXAMPLE_PROBLEM,
    build_extraction_gated_messages,
)
from breakdown.pipeline.prompts.narrative import build_narrative_messages

__all__ = [
    "build_extraction_gated_messages",
    "EXAMPLE_PROBLEM",
    "build_aggregator_messages",
    "build_narrative_messages",
]
