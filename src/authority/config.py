"""Authority configuration.
"""

from __future__ import annotations

import os
import sys

import config as _central

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
# AUTHORITY_DB stays as a legacy per-module override; otherwise defer to the central path
# (src/config.py, which honors COMMUNITY_DB). authority reads/writes the same integration bus
# as the rest of the spine, so they must resolve to one file.
DB_PATH: str = os.environ.get("AUTHORITY_DB") or str(_central.DB_PATH)

# --------------------------------------------------------------------------
# Graph construction
# --------------------------------------------------------------------------
EDGE_DIRECTION: str = "asker_to_answerer"
DROP_SELF_ANSWERS: bool = True
EDGE_SOURCE: str = "rebuild"

# --------------------------------------------------------------------------
# Yearly windows
# --------------------------------------------------------------------------
YEARS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025, 2026)
VALID_YEAR_MIN: int = 2021
VALID_YEAR_MAX: int = 2026
MIN_EDGES_PER_YEAR: int = 200

# --------------------------------------------------------------------------
# Data cleaning
# --------------------------------------------------------------------------
EXCLUDE_USER_IDS: frozenset[int] = frozenset({0})  # SO placeholder for deleted users

# --------------------------------------------------------------------------
# PageRank
# --------------------------------------------------------------------------
PAGERANK_ALPHA: float = 0.85
PAGERANK_MAX_ITER: int = 200
PAGERANK_TOL: float = 1.0e-09

# --------------------------------------------------------------------------
# Centrality measures (spec section 8 references PageRank/HITS expert finding)
# --------------------------------------------------------------------------
CENTRALITY_METHODS: tuple[str, ...] = (
    "pagerank",
    "in_degree",
    "out_degree",
    "hits_authority",
    "hits_hub",
    "eigenvector",
    "katz",
    "betweenness",
    "closeness",
    "harmonic",
)

# Sampling for expensive centralities. Set to None to run exact computation;
# anything > a few thousand nodes makes exact betweenness impractical.
BETWEENNESS_SAMPLE_K: int | None = 500

# Katz parameters. Alpha must be < 1 / largest_eigenvalue or the iteration
# diverges. 0.005 is conservative; the module also catches the divergence
# error and reduces alpha automatically.
KATZ_ALPHA: float = 0.005
KATZ_BETA: float = 1.0
KATZ_MAX_ITER: int = 1000

EIGENVECTOR_MAX_ITER: int = 1000

# Above this node count, the expensive centralities (betweenness, closeness,
# harmonic) are computed on the top-N subgraph (by PageRank) rather than the
# full graph, to keep runtime reasonable.
# v2.1: lowered from 50_000 -- closeness/harmonic on 50k nodes in pure
# Python NetworkX takes 30+ minutes, so a smaller default is friendlier.
EXPENSIVE_MAX_NODES: int = 20_000
EXPENSIVE_FALLBACK_TOP_N: int = 5_000

# --------------------------------------------------------------------------
# Multi-source authority synthesis (spec section 2.5)
# --------------------------------------------------------------------------
AUTHORITY_WEIGHTS: dict[str, float] = {
    "pagerank": 0.5,
    "tag_reputation": 0.3,
    "accept_rate": 0.2,
}
OVERLAP_HIGH: float = 0.8
OVERLAP_LOW: float = 0.5
CALIBRATION_TOP_N: int = 200

# --------------------------------------------------------------------------
# Louvain
# --------------------------------------------------------------------------
LOUVAIN_RESOLUTION: float = 1.0
LOUVAIN_RANDOM_STATE: int = 42

# --------------------------------------------------------------------------
# Bookkeeping
# --------------------------------------------------------------------------
STAGE_NAME: str = "authority"


# --------------------------------------------------------------------------
# Runtime override mechanism
# --------------------------------------------------------------------------
def apply_overrides(overrides: dict) -> dict:
    """Mutate module-level constants. Returns previous values for restore."""
    if not overrides:
        return {}
    mod = sys.modules[__name__]
    previous: dict = {}
    for name, value in overrides.items():
        if not hasattr(mod, name) or not name.isupper():
            valid = sorted(k for k in dir(mod) if k.isupper())
            raise ValueError(
                f"Unknown config key {name!r}. Valid keys: {valid}"
            )
        previous[name] = getattr(mod, name)
        setattr(mod, name, value)
    return previous


def snapshot() -> dict:
    """Return current config values as a dict (for logging / metadata)."""
    mod = sys.modules[__name__]
    return {k: getattr(mod, k) for k in dir(mod) if k.isupper()}
