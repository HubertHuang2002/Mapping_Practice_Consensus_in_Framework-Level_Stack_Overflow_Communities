"""The two seams to other teams' work. Module D depends on these Protocols, not on concrete
sources, so a proxy today and the real provider later are drop-in interchangeable.

  AuthorityProvider      ← Module B  (yearly PageRank).         Proxy: SO vote/accept/reputation.
  CanonicalGroupProvider ← Module C  (query → canonical_group). Proxy: SO duplicate chain.

This mirrors the existing provider-neutral `llm_call` seam (PLAN ▼Q2): the call sites are stable,
the blast radius of a real swap is confined to one adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class Answer:
    """One answer's identity + the signals a real authority provider would also key on."""

    answer_id: int
    vote: int
    is_accepted: bool
    date: int | None  # epoch seconds
    reputation: int | None = None
    body: str | None = None  # answer content (for the detail panel)
    author: str | None = None
    owner_user_id: int | None = None  # SO user id of the author — the key a real AuthorityProvider maps on


@dataclass
class CanonicalGroup:
    group_id: str
    answers: list[Answer]


@runtime_checkable
class CanonicalGroupProvider(Protocol):
    """Module C seam (fetch half): resolve a group_id to its member answers."""

    source_name: str

    def fetch(self, group_id: str) -> CanonicalGroup: ...


@runtime_checkable
class QueryResolver(Protocol):
    """Module C seam (resolve half): a free-text query → its canonical group_id, persisting the
    resolved membership into canonical_group. Returns None when no equivalent group is found.

    This is the cold-path entry (POST /queries → resolve → bake). Proxy: a known dup-chain lookup
    (demo queries only). Real (the pending "2b"): RAG retrieval over question embeddings + an LLM
    equivalence gate (src/canonical/{vector_search,llm_filter}). Swapping proxy → real touches only
    this adapter — the bake stages downstream consume canonical_group regardless of how it was filled."""

    source_name: str

    def resolve(self, query_text: str) -> str | None: ...


@runtime_checkable
class AuthorityProvider(Protocol):
    """Module B seam: map an answer to a scalar authority, or None when the author is out of the
    authority graph (no signal — rendered honestly as n/a, never faked to zero)."""

    source_name: str

    def score(self, answer: Answer) -> float | None: ...
