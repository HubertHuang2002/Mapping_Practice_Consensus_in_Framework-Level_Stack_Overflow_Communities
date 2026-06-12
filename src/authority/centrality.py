"""Centrality measures for the authority user graph.

Computes a configurable set of centrality measures. 

All scores are min-max normalized to [0, 1].
"""

from __future__ import annotations

import logging
import sys
import time
import warnings
from dataclasses import dataclass, field

import networkx as nx

from . import config


# --------------------------------------------------------------------------
# Logging (stderr, tagged)
# --------------------------------------------------------------------------
_log = logging.getLogger("authority.centrality")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[authority.centrality] %(message)s"))
    _log.addHandler(_h)
_log.setLevel(logging.INFO)


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------
@dataclass
class CentralityResult:
    """Per-user centrality scores.

    scores: {method_name: {user_id: normalized_score in [0,1]}}
    notes:  per-method execution notes (exact / sampled / fallback / error)
    """

    scores: dict[str, dict[int, float]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def methods(self) -> list[str]:
        return list(self.scores.keys())


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
def _minmax(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi == lo:
        return {k: 0.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


# --------------------------------------------------------------------------
# Individual measures
# --------------------------------------------------------------------------
def _pagerank(g: nx.DiGraph) -> dict[int, float]:
    if g.number_of_nodes() == 0:
        return {}
    return nx.pagerank(
        g,
        alpha=config.PAGERANK_ALPHA,
        max_iter=config.PAGERANK_MAX_ITER,
        tol=config.PAGERANK_TOL,
        weight="weight",
    )


def _in_degree(g: nx.DiGraph) -> dict[int, float]:
    return dict(g.in_degree(weight="weight"))


def _out_degree(g: nx.DiGraph) -> dict[int, float]:
    return dict(g.out_degree(weight="weight"))


def _hits(g: nx.DiGraph) -> tuple[dict[int, float], dict[int, float]]:
    """Returns (authority_scores, hub_scores). Uses scipy sparse internally."""
    if g.number_of_nodes() == 0:
        return {}, {}
    try:
        hubs, authorities = nx.hits(g, max_iter=500, normalized=True)
    except nx.PowerIterationFailedConvergence:
        hubs, authorities = nx.hits(g, max_iter=2000, tol=1e-6, normalized=True)
    return authorities, hubs


def _eigenvector(g: nx.DiGraph) -> dict[int, float]:
    """Eigenvector centrality.

    ``eigenvector_centrality_numpy`` is safe on large graphs -- it uses
    scipy.sparse.linalg.eigs (ARPACK) under the hood, not a dense matrix.
    """
    if g.number_of_nodes() == 0:
        return {}
    try:
        return nx.eigenvector_centrality_numpy(g, weight="weight")
    except Exception:
        return nx.eigenvector_centrality(
            g, max_iter=config.EIGENVECTOR_MAX_ITER, weight="weight"
        )


def _katz(g: nx.DiGraph) -> dict[int, float]:
    """Katz centrality via sparse power iteration.

    We deliberately do NOT use ``nx.katz_centrality_numpy``: that function
    calls ``.todense()`` on the adjacency matrix, which allocates an
    O(N^2) dense float array (50k nodes -> 20 GB). Power iteration keeps
    everything sparse, at the cost of being slower per call.

    On non-convergence we halve alpha and retry; very small alpha is fine
    for ranking even if it under-shoots the true Katz score.
    """
    if g.number_of_nodes() == 0:
        return {}
    alpha = config.KATZ_ALPHA
    for _ in range(5):
        try:
            return nx.katz_centrality(
                g,
                alpha=alpha,
                beta=config.KATZ_BETA,
                max_iter=config.KATZ_MAX_ITER,
                weight="weight",
                normalized=True,
            )
        except nx.PowerIterationFailedConvergence:
            alpha /= 2
        except Exception:
            return {}
    return {}


def _betweenness(g: nx.DiGraph) -> dict[int, float]:
    k = config.BETWEENNESS_SAMPLE_K
    if k is not None and k < g.number_of_nodes():
        return nx.betweenness_centrality(
            g, k=k, normalized=True, weight=None, seed=42
        )
    return nx.betweenness_centrality(g, normalized=True, weight=None)


def _closeness(g: nx.DiGraph) -> dict[int, float]:
    return nx.closeness_centrality(g, wf_improved=True)


def _harmonic(g: nx.DiGraph) -> dict[int, float]:
    return nx.harmonic_centrality(g)


_EXPENSIVE = {"betweenness", "closeness", "harmonic"}

_RUNNERS: dict[str, callable] = {
    "pagerank": _pagerank,
    "in_degree": _in_degree,
    "out_degree": _out_degree,
    "eigenvector": _eigenvector,
    "katz": _katz,
    "betweenness": _betweenness,
    "closeness": _closeness,
    "harmonic": _harmonic,
}


# --------------------------------------------------------------------------
# Subgraph fallback for expensive measures
# --------------------------------------------------------------------------
def _top_n_subgraph(
    g: nx.DiGraph, scores: dict[int, float], n: int
) -> nx.DiGraph:
    top_nodes = sorted(scores, key=lambda u: scores[u], reverse=True)[:n]
    return g.subgraph(top_nodes).copy()


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def compute_centralities(
    g: nx.DiGraph, methods: tuple[str, ...] | None = None
) -> CentralityResult:
    """Compute every requested centrality on ``g``.

    Progress is logged to stderr at INFO level (visible by default).
    Each method is wrapped in try/except so one failing measure does NOT
    abort the others -- you'll see a `notes` entry like `error: ...`.
    """
    methods = methods or config.CENTRALITY_METHODS
    result = CentralityResult()
    n_nodes = g.number_of_nodes()
    n_edges = g.number_of_edges()
    _log.info(f"graph: {n_nodes:,} nodes, {n_edges:,} edges")
    _log.info(f"requested centralities: {list(methods)}")

    # pagerank first (also used as the score for expensive-fallback subgraph)
    pr: dict[int, float] = {}
    if "pagerank" in methods:
        t0 = time.time()
        _log.info("computing pagerank ...")
        pr = _pagerank(g)
        result.scores["pagerank"] = _minmax(pr)
        result.notes["pagerank"] = "exact"
        _log.info(f"  pagerank done in {time.time() - t0:.1f}s")

    # HITS (one call -> two scores)
    if "hits_authority" in methods or "hits_hub" in methods:
        try:
            t0 = time.time()
            _log.info("computing hits ...")
            auth, hub = _hits(g)
            if "hits_authority" in methods:
                result.scores["hits_authority"] = _minmax(auth)
                result.notes["hits_authority"] = "exact"
            if "hits_hub" in methods:
                result.scores["hits_hub"] = _minmax(hub)
                result.notes["hits_hub"] = "exact"
            _log.info(f"  hits done in {time.time() - t0:.1f}s")
        except Exception as e:
            for m in ("hits_authority", "hits_hub"):
                if m in methods:
                    result.scores[m] = {}
                    result.notes[m] = f"error: {e!r}"
                    _log.warning(f"  {m} FAILED: {e!r}")

    # everything else
    for method in methods:
        if method in ("pagerank", "hits_authority", "hits_hub"):
            continue
        if method not in _RUNNERS:
            result.notes[method] = "unknown method, skipped"
            result.scores[method] = {}
            _log.warning(f"  unknown method {method!r}, skipped")
            continue

        target = g
        note = "exact"
        if method in _EXPENSIVE and n_nodes > config.EXPENSIVE_MAX_NODES:
            if not pr:
                pr = _pagerank(g)
            target = _top_n_subgraph(g, pr, config.EXPENSIVE_FALLBACK_TOP_N)
            note = (
                f"computed on top-{config.EXPENSIVE_FALLBACK_TOP_N} "
                "subgraph by PageRank (graph too big for exact)"
            )
        elif method == "betweenness" and config.BETWEENNESS_SAMPLE_K:
            if config.BETWEENNESS_SAMPLE_K < n_nodes:
                note = f"sampled (k={config.BETWEENNESS_SAMPLE_K})"

        t0 = time.time()
        _log.info(
            f"computing {method} on {target.number_of_nodes():,} nodes "
            f"({note}) ..."
        )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = _RUNNERS[method](target)
            result.scores[method] = _minmax(raw)
            result.notes[method] = note
            _log.info(f"  {method} done in {time.time() - t0:.1f}s")
        except MemoryError as e:
            result.scores[method] = {}
            result.notes[method] = f"out of memory: {e!r}"
            _log.warning(f"  {method} OOM, skipped")
        except Exception as e:
            result.scores[method] = {}
            result.notes[method] = f"error: {e!r}"
            _log.warning(f"  {method} FAILED: {e!r}")

    return result
