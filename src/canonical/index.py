"""In-memory cosine retrieval over the OpenAI canonical index.

Loads the .npy matrix ONCE per process (module cache) and L2-normalises it, so each query is a single
`matrix @ q` — no per-query SQLite read + JSON parse (the latency villain in the old vector_search.py
that re-parsed the whole table every call). Returns (question_id, cosine_score, rank) triples.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

import config

INDEX_DIR = config.CANONICAL_INDEX_DIR
_CACHE: dict[str, tuple[np.ndarray, np.ndarray]] = {}  # index_dir → (ids, normalised matrix)


def load_index(index_dir: Path | str = INDEX_DIR) -> tuple[np.ndarray, np.ndarray]:
    """Return (question_ids[int64 N], normalised_matrix[float32 N×D]); cached after first load."""
    key = str(index_dir)
    if key not in _CACHE:
        d = Path(index_dir)
        emb_p, ids_p = d / "embeddings.npy", d / "question_ids.npy"
        if not (emb_p.exists() and ids_p.exists()):
            raise FileNotFoundError(
                f"canonical index not built at {d} — run `python -m canonical.build_index` first")
        ids = np.load(ids_p)
        mat = np.load(emb_p).astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        _CACHE[key] = (ids, mat / norms)
    return _CACHE[key]


def search(
    query_vec: list[float] | np.ndarray,
    *,
    index_dir: Path | str = INDEX_DIR,
    top_k: int = 50,
    threshold: float = 0.0,
) -> list[tuple[int, float, int]]:
    """Cosine top-K. When threshold > 0, only scores ≥ threshold are returned (still capped at top_k)."""
    ids, mat = load_index(index_dir)
    q = np.asarray(query_vec, dtype=np.float32)
    norm = float(np.linalg.norm(q))
    if norm > 0:
        q = q / norm
    scores = mat @ q

    if threshold > 0.0:
        cand = np.where(scores >= threshold)[0]
        order = cand[np.argsort(scores[cand])[::-1]][:top_k]
    else:
        k = min(top_k, scores.shape[0])
        part = np.argpartition(scores, -k)[-k:]
        order = part[np.argsort(scores[part])[::-1]]

    return [(int(ids[i]), float(scores[i]), rank) for rank, i in enumerate(order)]


# ── group representative: MEDOID (most central member) — ADR 0010 group identity ──────────────
_IDPOS: dict[str, dict[int, int]] = {}  # index_dir → {question_id: matrix row}


def _id_positions(index_dir: Path | str = INDEX_DIR) -> dict[int, int]:
    """Cached question_id → matrix-row map (built once; the index has ~221k rows)."""
    key = str(index_dir)
    if key not in _IDPOS:
        ids, _ = load_index(index_dir)
        _IDPOS[key] = {int(q): i for i, q in enumerate(ids.tolist())}
    return _IDPOS[key]


def medoid_of(qids: list[int], *, index_dir: Path | str = INDEX_DIR) -> int | None:
    """The member most central to the SET = max summed cosine to the other members (ADR 0010).

    Unlike 'closest to the query' (a phrasing artifact — empirically the top-cosine anchor of
    q75293463 ranked #26/70 in centrality), the medoid depends only on the set, so it is a stable
    representative for the group id and the relevance gate's canonical_problem text. Members absent
    from the index are skipped; <2 present falls back to the first member.
    """
    if not qids:
        return None
    _, mat = load_index(index_dir)
    pos = _id_positions(index_dir)
    present = [q for q in qids if q in pos]
    if len(present) < 2:
        return present[0] if present else qids[0]
    V = mat[[pos[q] for q in present]]   # rows already L2-normalised
    S = V @ V.T                          # pairwise cosine
    return present[int(S.sum(axis=1).argmax())]
