"""Proxy QueryResolver — the cold-path resolve seam, stand-in until Module C 2b.

Maps a free-text query to a canonical group_id by a small KNOWN demo lookup (the curated queries
whose dup chains were already snapshotted/seeded). Returns None for anything else — that "None" is
exactly the gap the real resolver fills: RAG over question embeddings + an LLM equivalence gate
(src/canonical/{vector_search,llm_filter}) that can resolve ARBITRARY text and persist canonical_group
with real retrieval_rank/score + gate_decision/confidence. Same QueryResolver protocol → drop-in swap.

The real resolver is what lets PLAN step 9 select demo queries by running free text through the cold
path (emergent selection by peeking, not by pre-labelled topic).
"""
from __future__ import annotations

from contract.ports import QueryResolver  # noqa: F401  (documents the protocol this satisfies)

# Curated demo queries → the canonical group their dup chain was seeded under.
KNOWN_DEMO = {
    "the useState set method is not reflecting a change immediately": "q54069253",
    "usestate setter does not update state immediately": "q54069253",
}


class KnownChainResolver:
    """QueryResolver proxy: resolve only the curated demo queries (substring match, case-insensitive)."""

    source_name = "known_dup_chain"

    def __init__(self, known: dict[str, str] | None = None):
        self._known = {k.strip().lower(): v for k, v in (known or KNOWN_DEMO).items()}

    def resolve(self, query_text: str) -> str | None:
        q = (query_text or "").strip().lower()
        if not q:
            return None
        for text, group_id in self._known.items():
            if text in q or q in text:
                return group_id
        return None
