"""authority — Network & Multi-source Authority.

Public API:
    AuthorityPipeline(db_path, **overrides).run()
    AuthorityStore(db_path)             # for downstream stages
    config                              # for inspecting defaults

Dashboard:
    streamlit run authority/dashboard.py -- --db so_data_react_2021_2026.db
"""

from __future__ import annotations

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
from .pipeline import AuthorityPipeline, AuthorityRun, AuthorityStore
from .user_stats import UserStats, compute_user_stats

__all__ = [
    "config",
    "AuthorityPipeline",
    "AuthorityRun",
    "AuthorityStore",
    "GraphBundle",
    "build_graph",
    "AuthorityDB",
    "PageRankResult",
    "CommunityResult",
    "AuthorityResult",
    "CentralityResult",
    "UserStats",
    "compute_pagerank",
    "detect_communities",
    "synthesize_authority",
    "compute_centralities",
    "compute_user_stats",
]

__version__ = "0.4.0"
