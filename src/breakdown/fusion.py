"""Node-size weight = fusion of two ORTHOGONAL [0,1] factors:

    W = √(Q · A)        (weighted geometric mean, α = 0.5)

  Q — answer-level quality: the answer's vote percentile WITHIN its canonical group (ranked vs the
      other answers to the same question → age/size-deconfounded).
  A — author-level authority: the author's network-PageRank RANK PERCENTILE (authority.provider).
      Percentile, not log-magnitude: magnitude was dominated by a few hubs (even "top 1%" landed at
      ~0.24) so it barely differentiated authors and decoupled from the legible "top X%"; the rank
      spreads authors uniformly, makes A meaningfully drive size, and keeps size consistent with the
      detail's "top X%" label.

Geometric (not arithmetic) mean so the factors are NON-compensatory — a strong author can't mask a
weak answer and vice-versa; high weight needs BOTH (the reason the UNDP moved HDI to a geometric mean
in 2010). α = 0.5 means there is no hand-picked weight to defend.

Edge handling (the geometric mean's one sharp edge — it hits 0 if any factor is 0):
  * A absent (out-of-graph author) → DROP A and renormalize the exponent → W = Q. Never substitute
    A = 0, which would annihilate a great answer by a deleted/self-answering author.
  * A present → floor it at A_FLOOR so a genuinely low (but real) authority stays distinguishable
    from "no signal" and isn't crushed to 0.
"""
from __future__ import annotations

import math

A_FLOOR = 0.05  # ε: present-but-low authority floor (keeps it distinguishable from absent)


def vote_percentiles(answer_ids: list[int], vote_of: dict[int, int]) -> dict[int, float]:
    """Within-group answer-quality Q ∈ [0,1]: each answer's vote ranked against the others in THIS
    group, with AVERAGE-RANK ties — answers with EQUAL votes share the midpoint of their rank band,
    so equal votes give equal Q (no arbitrary answer_id ordering leaking into node size). Stays
    golden-master stable because the value depends only on the votes, not the iteration order."""
    ids = list(dict.fromkeys(answer_ids))
    n = len(ids)
    if n == 0:
        return {}
    if n == 1:
        return {ids[0]: 1.0}
    ordered = sorted(ids, key=lambda aid: vote_of.get(aid, 0))
    out: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        while j < n and vote_of.get(ordered[j], 0) == vote_of.get(ordered[i], 0):
            j += 1
        pct = ((i + j - 1) / 2) / (n - 1)  # midpoint rank of the tied band [i, j-1], normalized to [0,1]
        for k in range(i, j):
            out[ordered[k]] = pct
        i = j
    return out


def fuse(q: float, a: float | None) -> float:
    """Node weight W. A absent → degrade to Q; else √(Q · max(A, ε))."""
    if a is None:
        return q
    return math.sqrt(q * max(a, A_FLOOR))
