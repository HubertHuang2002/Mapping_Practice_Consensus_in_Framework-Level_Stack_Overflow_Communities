"""Authority pipeline orchestrator and public read API.

Use ``AuthorityPipeline`` to run the module. Config can be overridden at runtime via kwargs:

    # use defaults from config.py
    AuthorityPipeline("so_data_react_2021_2026.db").run()

    # override individual settings for this run only
    AuthorityPipeline(
        "so_data_react_2021_2026.db",
        EDGE_DIRECTION="answerer_to_asker",
        YEARS=(2024, 2025),
        BETWEENNESS_SAMPLE_K=None,        # exact, slower
        CENTRALITY_METHODS=("pagerank", "in_degree"),  # only these
    ).run()

After a run, downstream stages use ``AuthorityStore`` to read the persisted output:
    from authority import AuthorityStore
    r = AuthorityStore("so_data_react_2021_2026.db")
    pr_2024 = r.pagerank_yearly(2024)
    df = r.user_table()       # pandas DataFrame, one row per user
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

from . import config
from .authority import (
    AuthorityResult,
    CommunityResult,
    PageRankResult,
    compute_pagerank,
    detect_communities,
    synthesize_authority,
)
from .centrality import CentralityResult, compute_centralities
from .db import AuthorityDB
from .graph import GraphBundle, build_graph
from .storage import write_results
from .user_stats import UserStats, compute_user_stats


# ==========================================================================
# Run result (in-memory)
# ==========================================================================
@dataclass
class AuthorityRun:
    graph: GraphBundle
    pagerank: PageRankResult
    communities: CommunityResult
    authority: AuthorityResult
    user_stats: UserStats
    centralities: CentralityResult

    def summary(self) -> dict:
        return {
            "graph": self.graph.stats(),
            "pagerank": {
                "full_users_scored": len(self.pagerank.full),
                "years_scored": sorted(self.pagerank.yearly),
                "low_data_years": self.pagerank.low_data_years,
            },
            "communities": {
                "n_communities": self.communities.n_communities,
                "modularity": round(self.communities.modularity, 4),
            },
            "authority": {
                "scheme": self.authority.scheme,
                "weights": self.authority.weights,
                "overlap": {
                    k: (None if v is None else round(v, 4))
                    for k, v in self.authority.overlap.items()
                },
                "users_scored": len(self.authority.score),
            },
            "centralities": {
                "methods": self.centralities.methods(),
                "notes": self.centralities.notes,
            },
            "user_stats_total_users": len(self.user_stats.all_user_ids()),
        }

    def top_authorities(self, n: int = 10) -> list[tuple[int, float]]:
        return sorted(
            self.authority.score.items(), key=lambda kv: kv[1], reverse=True
        )[:n]


# ==========================================================================
# Pipeline
# ==========================================================================
class AuthorityPipeline:
    """End-to-end authority computation with runtime config override.

    Parameters
    ----------
    db_path : str, optional
        Path to the SQLite DB. Defaults to config.DB_PATH.
    tag : str
        Tag name written to _progress (default 'reactjs').
    **overrides
        Any UPPER_CASE name from config.py can be passed here to override the
        default just for this run; the previous value is restored when run()
        finishes. Unknown keys raise ValueError.
    """

    def __init__(
        self,
        db_path: str | None = None,
        tag: str = "reactjs",
        **overrides,
    ):
        self.db_path = db_path or config.DB_PATH
        self.tag = tag
        self.overrides = overrides

    def run(
        self,
        persist: bool = True,
        compute_centrality: bool = True,
    ) -> AuthorityRun:
        previous = config.apply_overrides(self.overrides)
        try:
            with AuthorityDB(self.db_path) as db:
                bundle = build_graph(db)
                stats = compute_user_stats(db)
                reputation = db.user_reputation_map()

            pagerank = compute_pagerank(bundle)
            communities = detect_communities(bundle)

            # accept rate used in authority synthesis = answerer-side rate
            accept_rate = {
                uid: stats.answerer_accept_rate(uid)
                for uid in stats.all_user_ids()
                if stats.answer_count.get(uid, 0) > 0
            }
            authority = synthesize_authority(
                pagerank_full=pagerank.full,
                tag_reputation=reputation or None,
                accept_rate=accept_rate or None,
            )

            if compute_centrality:
                centralities = compute_centralities(bundle.full)
            else:
                centralities = CentralityResult()

            result = AuthorityRun(
                graph=bundle,
                pagerank=pagerank,
                communities=communities,
                authority=authority,
                user_stats=stats,
                centralities=centralities,
            )

            if persist:
                write_results(
                    self.db_path,
                    bundle,
                    pagerank,
                    communities,
                    authority,
                    stats,
                    centralities,
                    tag=self.tag,
                )
            return result
        finally:
            config.apply_overrides(previous)


# ==========================================================================
# Read API for downstream stages
# ==========================================================================
class AuthorityStore:
    """Read-only access to the persisted authority output."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.DB_PATH
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._check_ran()

    def _check_ran(self) -> None:
        tables = {
            r[0]
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "user_authority" not in tables:
            raise RuntimeError(
                "Authority has not been run on this DB yet. Run "
                "AuthorityPipeline(db_path).run() first."
            )

    # ---- yearly PageRank -------------------------------------------------
    def pagerank_yearly(self, year: int) -> dict[int, float]:
        return {
            r["user_id"]: r["pagerank"]
            for r in self._conn.execute(
                "SELECT user_id, pagerank FROM user_pagerank_yearly "
                "WHERE year = ?",
                (year,),
            )
        }

    def pagerank_yearly_all(self) -> dict[int, dict[int, float]]:
        out: dict[int, dict[int, float]] = {}
        for r in self._conn.execute(
            "SELECT user_id, year, pagerank FROM user_pagerank_yearly"
        ):
            out.setdefault(r["year"], {})[r["user_id"]] = r["pagerank"]
        return out

    def pagerank_percentile(self, year: int) -> dict[int, float]:
        return {
            r["user_id"]: r["rank_percentile"]
            for r in self._conn.execute(
                "SELECT user_id, rank_percentile FROM user_pagerank_yearly "
                "WHERE year = ?",
                (year,),
            )
        }

    # ---- the fat table as a DataFrame ------------------------------------
    def user_table(self) -> pd.DataFrame:
        """Full user_authority table as a pandas DataFrame."""
        return pd.read_sql_query(
            "SELECT * FROM user_authority", self._conn
        )

    def authority_score(self) -> dict[int, float]:
        return {
            r["user_id"]: r["authority_score"]
            for r in self._conn.execute(
                "SELECT user_id, authority_score FROM user_authority"
            )
        }

    def communities(self) -> dict[int, int]:
        return {
            r["user_id"]: r["community_id"]
            for r in self._conn.execute(
                "SELECT user_id, community_id FROM user_authority "
                "WHERE community_id IS NOT NULL"
            )
        }

    def centrality(self, method: str) -> dict[int, float]:
        """{user_id: centrality value} for one method (e.g. 'pagerank')."""
        col = f"cent_{method}"
        if col not in self._table_columns("user_authority"):
            raise KeyError(f"Centrality {method!r} not in DB")
        return {
            r["user_id"]: r[col]
            for r in self._conn.execute(
                f"SELECT user_id, {col} FROM user_authority"
            )
        }

    def centrality_methods(self) -> list[str]:
        return [
            c.removeprefix("cent_")
            for c in self._table_columns("user_authority")
            if c.startswith("cent_")
        ]

    # ---- metadata --------------------------------------------------------
    def run_meta(self) -> dict[str, str]:
        return {
            r["key"]: r["value"]
            for r in self._conn.execute(
                "SELECT key, value FROM authority_run_meta"
            )
        }

    def low_data_years(self) -> list[int]:
        raw = self.run_meta().get("low_data_years", "")
        return [int(y) for y in raw.split(",") if y]

    # ---- internal --------------------------------------------------------
    def _table_columns(self, table: str) -> set[str]:
        return {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AuthorityStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
