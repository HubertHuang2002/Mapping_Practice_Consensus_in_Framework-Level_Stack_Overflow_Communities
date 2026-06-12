"""Shared contract kernel: the Breakdown types + provider seams. Depends on nothing; everything
depends on it (the dependency sink). ADR 0008 (data contract + provider seams)."""
from .ports import (
    Answer,
    AuthorityProvider,
    CanonicalGroup,
    CanonicalGroupProvider,
)
from .types import (
    AnswerCard,
    Breakdown,
    ClusterShell,
    PracticePoint,
    Signals,
    year_of,
)

__all__ = [
    "Breakdown",
    "PracticePoint",
    "Signals",
    "AnswerCard",
    "ClusterShell",
    "year_of",
    "Answer",
    "CanonicalGroup",
    "AuthorityProvider",
    "CanonicalGroupProvider",
]
