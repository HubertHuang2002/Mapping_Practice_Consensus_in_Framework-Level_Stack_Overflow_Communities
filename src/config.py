"""Single source of truth for the shared data artifacts — the SQLite integration bus and the
canonical RAG index. Every plane (serve, orchestration, the breakdown pipeline, the canonical
resolver, authority) imports these instead of hardcoding the filename, so pointing at a renamed db
or another snapshot is one env var, not a find-replace across ~10 files.

Paths are anchored at the repo root, so they resolve the same regardless of the process's cwd.
Override at runtime (relative paths resolve against the repo root; absolute paths are used as-is):

    COMMUNITY_DB=data/so_data_react_2021_2026.db    PYTHONPATH=src uvicorn serve.app:app --reload
    COMMUNITY_INDEX_DIR=/mnt/big/canonical_index    PYTHONPATH=src python -m canonical.build_index
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # src/config.py → src → repo root
DATA_DIR = REPO_ROOT / "data"


def _resolve(env_var: str, default: Path) -> Path:
    """Env override if set (relative → repo-root-anchored, absolute → as-is), else the default."""
    raw = os.environ.get(env_var)
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


# The shared SQLite integration bus: raw SO tables (questions/answers/comments/users/interactions)
# PLUS every computed table (authority, practices, clusters, narratives, query_viz_cache). One file.
DB_PATH: Path = _resolve("COMMUNITY_DB", DATA_DIR / "so_data_react_2021_2026_processed.db")

# Canonical RAG index (corpus embeddings + question_ids .npy) behind free-text resolve.
CANONICAL_INDEX_DIR: Path = _resolve("COMMUNITY_INDEX_DIR", DATA_DIR / "canonical_index")
