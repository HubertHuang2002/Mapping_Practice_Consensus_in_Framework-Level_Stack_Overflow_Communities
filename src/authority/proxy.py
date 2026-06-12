"""SO-data PROXY for the authority seam (pre-differentiator stand-in).

authority ≈ log1p(vote) + accepted bonus, with a floor so every answer stays visible. Vote-primary
on purpose (raw vote is power-law and age-confounded); the exact blend is irrelevant because it
lives entirely behind this one method. The real provider is `authority.provider.PageRankAuthorityProvider`
(yearly PageRank percentile); this proxy is the fallback when that authority data hasn't been persisted.
"""
from __future__ import annotations

import math

from contract.ports import Answer


class VoteAuthorityProxy:
    """AuthorityProvider, proxied. Swap for the yearly-PageRank lookup and nothing downstream notices."""

    source_name = "so_proxy"

    def __init__(self, accepted_bonus: float = 0.5, floor: float = 0.5):
        self.accepted_bonus = accepted_bonus
        self.floor = floor

    def score(self, a: Answer) -> float:
        base = math.log1p(max(a.vote, 0))
        if a.is_accepted:
            base += self.accepted_bonus
        return base + self.floor
