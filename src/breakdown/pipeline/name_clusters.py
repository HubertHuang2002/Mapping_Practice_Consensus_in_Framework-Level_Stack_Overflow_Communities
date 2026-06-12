"""Name consensus clusters from their OWN members — the fix for d2_consensus.py's run-0 plurality
naming (line 86), which let two distinct co-association clusters inherit one umbrella label
("Direct set vs merge/immutability"). The split is the robust signal (3-run consensus); the name was
the weak one (single run). Here each cluster is (re)named from the practices it actually contains.

One LLM call sees ALL the clusters together, so the labels come out mutually distinct; an `avoid`
list keeps them distinct from clusters named elsewhere (e.g. when only re-labelling a colliding
subset, pass the other clusters' names so the new ones don't re-collide).

General mechanism (a future live bake names every consensus cluster this way, replacing the run-0
plurality); for the q54069253 backfill it re-labels only the colliding pair.
"""
from __future__ import annotations

from pydantic import BaseModel

from breakdown.pipeline.llm import llm_call


class _ClusterLabel(BaseModel):
    name: str  # short distinct label for the approach (≤ 6 words)
    description: str  # ≤ 2 sentences: what the practices in this cluster have in common


class _ClusterLabels(BaseModel):
    reasoning: str  # brief CoT over how the clusters differ; generated first, not persisted
    labels: list[_ClusterLabel]  # one per input cluster, SAME order as the input


_SYSTEM = """\
You label clusters of Stack Overflow React *practices*. Each cluster is a group of practices the
community proposes that represent the SAME underlying approach to ONE implementation problem.

You are given several clusters (each a list of its member practice sentences). For EACH cluster, in
the same order, produce:
- name: a short, specific label (≤ 6 words) for the approach the cluster represents. Name it from
  the member practices ONLY — do not add outside React knowledge.
- description: ≤ 2 sentences stating what the members have in common.

Hard requirements:
- The names must be MUTUALLY DISTINCT and must capture how the clusters genuinely DIFFER from one
  another — if two clusters are near-opposite approaches, their names must make that contrast clear.
- The names must also be distinct from any label in the provided "avoid" list.
- Be faithful and concrete; avoid umbrella labels of the form "X vs Y" that could fit either side."""


def _user_message(member_lists: list[list[str]], avoid: list[str]) -> str:
    parts: list[str] = []
    if avoid:
        parts.append("Existing labels to stay distinct from (avoid): " + "; ".join(avoid) + "\n")
    parts.append(f"Label these {len(member_lists)} clusters (return {len(member_lists)} labels, in order):\n")
    for i, members in enumerate(member_lists):
        parts.append(f"[cluster {i}] ({len(members)} practices)")
        parts.extend(f"  - {m}" for m in members)
        parts.append("")
    return "\n".join(parts)


def name_clusters(
    member_lists: list[list[str]],
    avoid: list[str] | None = None,
    tier: str = "aggregate",
) -> list[dict]:
    """Return [{name, description}] — one per input cluster, in input order."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_message(member_lists, avoid or [])},
    ]
    out = llm_call(messages, _ClusterLabels, tier=tier)
    if len(out.labels) != len(member_lists):
        raise RuntimeError(f"expected {len(member_lists)} labels, got {len(out.labels)}")
    return [{"name": lbl.name, "description": lbl.description} for lbl in out.labels]
