"""OpenAI retrieval embeddings for the canonical resolve seam (Module C 2b).

Retrieval embedding ≠ the clustering companion embedder (ADR 0002, breakdown side, local SBERT for
the reproducibility floor). This one maps the question corpus + free-text queries into one shared
space for cosine retrieval; model = OpenAI text-embedding-3-small (1536-dim), the upgrade over the
prior local nomic 768-dim probe. Cost ≈ $0.02 / 1M input tokens (one-time corpus build over the
~220k react questions ≈ $0.66). The client/key are loaded lazily so importing canonical stays light.
"""
from __future__ import annotations

import os
from pathlib import Path

from openai import OpenAI

MODEL = "text-embedding-3-small"
DIM = 1536
_ROOT = Path(__file__).resolve().parents[2]  # src/canonical/embed.py → repo root


def _load_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY"):
                return line.split("=", 1)[1].strip()
    raise SystemExit("OPENAI_API_KEY not found (env or .env)")


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=_load_key())
    return _client


def embed_query(text: str) -> list[float]:
    """Embed one free-text query into the retrieval space."""
    resp = _get_client().embeddings.create(model=MODEL, input=[text or " "])
    return resp.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of corpus texts (one request). Empty inputs are sent as a space (the API
    rejects ''); order is preserved 1:1 with the input list."""
    safe = [(t if t and t.strip() else " ") for t in texts]
    resp = _get_client().embeddings.create(model=MODEL, input=safe)
    return [d.embedding for d in resp.data]
