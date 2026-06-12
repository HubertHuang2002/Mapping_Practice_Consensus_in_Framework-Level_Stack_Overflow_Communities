"""Module B seam — the author-level network authority A. Two providers:

  FullPagerankAuthorityProvider (DEFAULT, time-EXCLUDED): A = the FULL-period PageRank RANK
    PERCENTILE. The default view factors time out (time is a separate timeline axis), so there is one
    full-period graph. We first tried log→min-max magnitude here, but on real data it was dominated
    by a few hubs (even "top 1%" ≈ 0.24) so authority barely differentiated and decoupled from the
    detail's "top X%"; the rank percentile spreads authors uniformly and keeps size ⇄ label consistent
    (ADR 0003 addendum 2026-06-05).

  PageRankAuthorityProvider (for the future TIMELINE axis): A = the answer author's per-YEAR
    PageRank PERCENTILE. Percentile is the right call there because comparing across yearly graphs
    of different sizes needs rank commensurability (a 90th-pct answerer in sparse 2026 vs dense 2021).

Both map owner_user_id -> [0,1], and both return None out-of-graph (unknown/deleted author, or
author absent from the graph) — never a fabricated 0. None is "no authority signal"; downstream the
fusion (breakdown.fusion) degrades the node weight to the answer-level Q alone, and the dashboard
renders the author cell as n/a. Drop-in for VoteAuthorityProxy — the contract never changes.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from authority import AuthorityStore
from contract.ports import Answer


class PageRankAuthorityProvider:
    """AuthorityProvider backed by persisted yearly-PageRank percentiles
    (reads the authority DB tables via AuthorityStore)."""

    source_name = "pagerank_yearly_pct"

    def __init__(self, db_path: str):
        self._results = AuthorityStore(db_path)  # raises if authority hasn't been persisted yet
        self._pct_by_year: dict[int, dict[int, float]] = {}  # lazy per-year cache

    def _percentiles(self, year: int) -> dict[int, float]:
        if year not in self._pct_by_year:
            self._pct_by_year[year] = self._results.pagerank_percentile(year)
        return self._pct_by_year[year]

    def score(self, a: Answer) -> float | None:
        if a.owner_user_id is None or a.date is None:
            return None
        year = datetime.fromtimestamp(a.date, tz=timezone.utc).year
        return self._percentiles(year).get(a.owner_user_id)

    def close(self) -> None:
        self._results.close()


class FullPagerankAuthorityProvider:
    """AuthorityProvider, time-EXCLUDED default: A = the author's full-period PageRank RANK PERCENTILE
    (1.0 = top). Author-level (one value per author, date-independent), read once from
    users.pagerank_full.

    Percentile, NOT log-magnitude: magnitude was dominated by a few hubs (even a "top 1%" author
    landed at ~0.24), so it barely differentiated authors and decoupled from the legible "top X%";
    the rank spreads authors uniformly, makes A meaningfully drive the size fusion, AND keeps size
    consistent with the detail's "top X%" (ADR 0003 addendum 2026-06-05). Out-of-graph -> None.
    """

    source_name = "pagerank_full_pct"

    def __init__(self, db_path: str):
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT user_id, pagerank_full FROM users "
                "WHERE pagerank_full IS NOT NULL AND pagerank_full > 0"
            ).fetchall()
        finally:
            conn.close()
        self._pct: dict[int, float] = {}
        if rows:
            ordered = sorted(rows, key=lambda r: r[1])  # ascending PageRank (values are effectively unique)
            n = len(ordered)
            self._pct = {uid: (i / (n - 1) if n > 1 else 1.0) for i, (uid, _) in enumerate(ordered)}

    def score(self, a: Answer) -> float | None:
        if a.owner_user_id is None:
            return None
        return self._pct.get(a.owner_user_id)  # rank percentile; None when out of the full-period graph

    def close(self) -> None:
        pass
