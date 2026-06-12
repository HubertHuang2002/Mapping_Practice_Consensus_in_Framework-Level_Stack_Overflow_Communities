"""Build the canonical retrieval index — the one-time corpus embed behind free-text resolve.

Embeds every react question's (title + body_text) with OpenAI text-embedding-3-small and persists
the result as two .npy files (a float32 [N, 1536] matrix + an int64 [N] question_id vector) under
data/canonical_index/. Resumable: questions already in the index are skipped, and progress is
checkpointed every few batches so a crash never re-spends on done work. One-time cost ≈ $0.66.

Run: PYTHONPATH=src uv run --no-sync python -m canonical.build_index            # full corpus
     PYTHONPATH=src uv run --no-sync python -m canonical.build_index --limit 200 # smoke subset
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np

import config
from canonical.embed import DIM, MODEL, embed_batch

DB = config.DB_PATH
INDEX_DIR = config.CANONICAL_INDEX_DIR
MAX_CHARS = 30_000  # ~8k tokens — the model's per-input cap is 8191; guards the few giant bodies


def _paths(index_dir: Path) -> tuple[Path, Path]:
    return index_dir / "embeddings.npy", index_dir / "question_ids.npy"


def _load_existing(index_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    emb_p, ids_p = _paths(index_dir)
    if emb_p.exists() and ids_p.exists():
        return np.load(ids_p), np.load(emb_p).astype(np.float32)
    return np.zeros((0,), dtype=np.int64), np.zeros((0, DIM), dtype=np.float32)


def _save(index_dir: Path, ids: list[int], embs: list[np.ndarray]) -> None:
    emb_p, ids_p = _paths(index_dir)
    matrix = np.concatenate(embs, axis=0) if embs else np.zeros((0, DIM), dtype=np.float32)
    np.save(emb_p, matrix.astype(np.float32))
    np.save(ids_p, np.asarray(ids, dtype=np.int64))


def build(
    db: Path = DB,
    index_dir: Path = INDEX_DIR,
    *,
    batch: int = 500,
    checkpoint_every: int = 10,
    limit: int = 0,
) -> dict:
    """Embed all (or --limit) not-yet-indexed react questions, append to the .npy index, checkpoint."""
    index_dir.mkdir(parents=True, exist_ok=True)
    done_ids, done_emb = _load_existing(index_dir)
    done = {int(x) for x in done_ids}

    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT question_id, COALESCE(title, ''), COALESCE(body_text, '') "
            "FROM questions WHERE is_react = 1 ORDER BY question_id"
        ).fetchall()
    finally:
        con.close()

    todo = [(qid, t, b) for qid, t, b in rows if qid not in done]
    if limit:
        todo = todo[:limit]

    ids: list[int] = [int(x) for x in done_ids]
    embs: list[np.ndarray] = [done_emb] if done_emb.size else []
    n_new = 0
    n_batches = (len(todo) + batch - 1) // batch
    for bi, start in enumerate(range(0, len(todo), batch), 1):
        chunk = todo[start:start + batch]
        texts = [f"{t}\n\n{b}"[:MAX_CHARS] for _, t, b in chunk]
        embs.append(np.asarray(embed_batch(texts), dtype=np.float32))
        ids.extend(int(qid) for qid, _, _ in chunk)
        n_new += len(chunk)
        if bi % checkpoint_every == 0 or bi == n_batches:
            _save(index_dir, ids, embs)
            embs = [np.concatenate(embs, axis=0)]  # collapse to keep memory flat
            print(f"  [{bi}/{n_batches}] embedded {n_new}/{len(todo)} new  (total {len(ids)})")

    _save(index_dir, ids, embs)
    return {"model": MODEL, "indexed_total": len(ids), "new": n_new,
            "already_present": len(done), "dim": DIM}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--index-dir", default=str(INDEX_DIR))
    ap.add_argument("--batch", type=int, default=500, help="inputs per embedding request")
    ap.add_argument("--limit", type=int, default=0, help="cap new questions (0 = all) — smoke subset")
    args = ap.parse_args()
    import json
    print(json.dumps(build(Path(args.db), Path(args.index_dir),
                           batch=args.batch, limit=args.limit), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
