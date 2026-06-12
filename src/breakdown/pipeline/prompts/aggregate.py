"""Stage ⑤ — Cluster aggregator prompt (emergent grouping, no pre-set taxonomy).

Groups a query's extracted practices into clusters where each cluster = the SAME underlying
approach. Topic-neutral and few-shot-free on purpose: the shape is emergent (ADR 0001/0006),
not imposed. Run k times + co-association consensus downstream (cluster.py).
"""
from __future__ import annotations

_AGG_SYSTEM = """\
You are given a numbered list of React implementation practices, each extracted from a
different Stack Overflow answer to the SAME underlying problem. Group them into clusters
where each cluster represents the SAME underlying approach/recommendation, regardless of
wording. Genuinely different approaches go in different clusters. A practice with no sibling
may be its own singleton cluster.

Output:
- reasoning: brief thinking, not stored.
- clusters: list of {name: short label for the approach, description: <=2 sentences,
  member_indices: 0-based indices into the input list}. Assign EVERY index to exactly one
  cluster — do not skip or duplicate an index."""


def build_aggregator_messages(practices: list[str]) -> list[dict]:
    listing = "\n".join(f"{i}: {p}" for i, p in enumerate(practices))
    return [
        {"role": "system", "content": _AGG_SYSTEM},
        {"role": "user", "content": "Practices:\n" + listing},
    ]
