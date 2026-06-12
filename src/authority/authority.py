"""PageRank yearly, Louvain communities, and multi-source authority synthesis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from . import config
from .graph import GraphBundle

try:
    import community.community_louvain as community_louvain
    _HAVE_LOUVAIN = True
except ImportError:  # pragma: no cover
    _HAVE_LOUVAIN = False

try:
    from scipy.stats import spearmanr
    _HAVE_SCIPY = True
except ImportError:  # pragma: no cover
    _HAVE_SCIPY = False


# ==========================================================================
# Yearly PageRank
# ==========================================================================
@dataclass
class PageRankResult:
    full: dict[int, float]
    yearly: dict[int, dict[int, float]]
    yearly_percentile: dict[int, dict[int, float]]
    low_data_years: list[int] = field(default_factory=list)


def _pagerank(graph: nx.DiGraph) -> dict[int, float]:
    if graph.number_of_nodes() == 0:
        return {}
    return nx.pagerank(
        graph,
        alpha=config.PAGERANK_ALPHA,
        max_iter=config.PAGERANK_MAX_ITER,
        tol=config.PAGERANK_TOL,
        weight="weight",
    )


def _percentile_map(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    if len(scores) == 1:
        return {next(iter(scores)): 1.0}
    ordered = sorted(scores.items(), key=lambda kv: kv[1])
    n = len(ordered)
    return {uid: i / (n - 1) for i, (uid, _) in enumerate(ordered)}


def compute_pagerank(bundle: GraphBundle) -> PageRankResult:
    full = _pagerank(bundle.full)
    yearly: dict[int, dict[int, float]] = {}
    yearly_pct: dict[int, dict[int, float]] = {}
    low_data: list[int] = []

    for year, sg in sorted(bundle.yearly.items()):
        scores = _pagerank(sg)
        yearly[year] = scores
        yearly_pct[year] = _percentile_map(scores)
        if sg.number_of_edges() < config.MIN_EDGES_PER_YEAR:
            low_data.append(year)

    return PageRankResult(
        full=full,
        yearly=yearly,
        yearly_percentile=yearly_pct,
        low_data_years=low_data,
    )


# ==========================================================================
# Louvain
# ==========================================================================
@dataclass
class CommunityResult:
    partition: dict[int, int]
    modularity: float
    n_communities: int


def detect_communities(bundle: GraphBundle) -> CommunityResult:
    if not _HAVE_LOUVAIN:
        raise RuntimeError("python-louvain not installed.")

    undirected = nx.Graph()
    for u, v, data in bundle.full.edges(data=True):
        w = data.get("weight", 1)
        if undirected.has_edge(u, v):
            undirected[u][v]["weight"] += w
        else:
            undirected.add_edge(u, v, weight=w)

    if undirected.number_of_edges() == 0:
        return CommunityResult(partition={}, modularity=0.0, n_communities=0)

    partition = community_louvain.best_partition(
        undirected,
        weight="weight",
        resolution=config.LOUVAIN_RESOLUTION,
        random_state=config.LOUVAIN_RANDOM_STATE,
    )
    modularity = community_louvain.modularity(
        partition, undirected, weight="weight"
    )
    return CommunityResult(
        partition=partition,
        modularity=modularity,
        n_communities=len(set(partition.values())),
    )


# ==========================================================================
# Multi-source authority synthesis (spec section 2.5)
# ==========================================================================
@dataclass
class AuthorityResult:
    score: dict[int, float]
    scheme: str
    weights: dict[str, float]
    overlap: dict[str, float | None]
    components: dict[int, dict[str, float]]


def _minmax(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _safe_spearman(a: list[float], b: list[float]) -> float | None:
    """Spearman rho; returns None when either input is constant."""
    if len(a) < 3:
        return None
    if len(set(a)) < 2 or len(set(b)) < 2:
        return None  # avoid scipy ConstantInputWarning
    if _HAVE_SCIPY:
        rho, _ = spearmanr(a, b)
        if rho != rho:  # nan
            return None
        return float(rho)
    # plain rank-correlation fallback
    def ranks(x: list[float]) -> list[float]:
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for rank, idx in enumerate(order):
            r[idx] = rank
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    va = sum((ra[i] - ma) ** 2 for i in range(n)) ** 0.5
    vb = sum((rb[i] - mb) ** 2 for i in range(n)) ** 0.5
    if va == 0 or vb == 0:
        return None
    return cov / (va * vb)


def synthesize_authority(
    pagerank_full: dict[int, float],
    tag_reputation: dict[int, float] | None = None,
    accept_rate: dict[int, float] | None = None,
) -> AuthorityResult:
    tag_reputation = tag_reputation or {}
    accept_rate = accept_rate or {}

    pr_norm = _minmax(pagerank_full)
    rep_norm = _minmax(tag_reputation)
    acc_norm = _minmax(accept_rate)

    components: dict[int, dict[str, float]] = {
        uid: {
            "pagerank": pr_norm.get(uid, 0.0),
            "tag_reputation": rep_norm.get(uid, 0.0),
            "accept_rate": acc_norm.get(uid, 0.0),
        }
        for uid in pr_norm
    }

    overlap: dict[str, float | None] = {}
    if rep_norm:
        top_users = sorted(
            pr_norm, key=lambda u: pr_norm[u], reverse=True
        )[: config.CALIBRATION_TOP_N]
        common = [u for u in top_users if u in rep_norm]
        if len(common) >= 3:
            overlap["pagerank_vs_tag_reputation"] = _safe_spearman(
                [pr_norm[u] for u in common],
                [rep_norm[u] for u in common],
            )
        if acc_norm:
            common_acc = [u for u in top_users if u in acc_norm]
            if len(common_acc) >= 3:
                overlap["pagerank_vs_accept_rate"] = _safe_spearman(
                    [pr_norm[u] for u in common_acc],
                    [acc_norm[u] for u in common_acc],
                )

    pr_rep = overlap.get("pagerank_vs_tag_reputation")

    if not rep_norm or pr_rep is None:
        scheme = "pagerank_only"
    elif pr_rep > config.OVERLAP_HIGH:
        scheme = "pagerank_only"
    elif pr_rep < config.OVERLAP_LOW:
        scheme = "dual_track"
    else:
        scheme = "weighted"

    if scheme in ("pagerank_only", "dual_track"):
        score = dict(pr_norm)
        weights: dict[str, float] = {}
    else:
        w = config.AUTHORITY_WEIGHTS
        weights = dict(w)
        score = {
            uid: (
                w["pagerank"] * c["pagerank"]
                + w["tag_reputation"] * c["tag_reputation"]
                + w["accept_rate"] * c["accept_rate"]
            )
            for uid, c in components.items()
        }

    return AuthorityResult(
        score=score,
        scheme=scheme,
        weights=weights,
        overlap=overlap,
        components=components,
    )
