"""The Module D data contract: the normalized practice-breakdown the dashboard consumes,
independent of where authority (Module B) and grouping (Module C) come from.

A proxy and the real provider both populate THIS shape, so swapping a provider never touches the
dashboard. Node size is driven by `weight` = √(Q·A) (breakdown.fusion): the fused answer-level
quality × author-level authority. `authority` carries the author-level component A on its own (for
the detail panel's network-vs-native juxtaposition); circle-pack geometry (`r`) and the dashboard
node size both read `weight`, so there is still one size formula.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class Signals:
    """Raw answer-level signals — kept for tooltips and as the authority proxy's inputs."""

    vote: int
    is_accepted: bool
    date: int | None  # epoch seconds
    year: int | None
    reputation: int | None = None


@dataclass
class PracticePoint:
    answer_id: int
    practice_index: int  # position within its answer
    text: str  # the extracted practice statement
    cluster: str  # practice-cluster id (Module D's own clustering)
    authority: float | None  # ← Module B seam: author-level network authority A = full-period PageRank RANK PERCENTILE (1.0=top). Feeds the size fusion AND is shown as "top X%" in the detail. None = no network signal (authority_status says which kind).
    signals: Signals
    conditions: list[str] = field(default_factory=list)  # when-to-apply (from extraction)
    evidence_type: str = ""  # prose | code | both — what backs this practice in the source answer
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0  # filled by layout.pack()
    weight: float = 0.0  # node SIZE driver: fused W = √(Q·A) (breakdown.fusion); A absent → W = Q. Always present.
    # why authority is / isn't present, so the dashboard renders the two null causes DISTINCTLY rather
    # than as one undifferentiated n/a:
    #   "scored"          — author has a yearly-PageRank percentile (authority is a float)
    #   "anonymous"       — no author id (deleted / anonymised) → authority unknowable in principle
    #   "non_interactive" — author present but absent from the answerer network (e.g. self-answer) →
    #                       no network signal, yet native signals (votes/accept) still describe it
    authority_status: str = "scored"
    pagerank: float | None = None  # author RAW full-period PageRank (NOT the saturated percentile) —
    #                                drives the force-field "size by authority" slider end; None = out of graph


@dataclass
class ClusterShell:
    id: str
    name: str
    n: int
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0
    # per-camp signals (camp_signal; None on the long-tail shell or a pre-aggregate group) — so the
    # CommunityCard narrates THIS community's OWN stats (its votes / authority / cohesion), not the
    # query-level numbers. Semantics mirror aggregate.py exactly:
    vote_share: float | None = None           # this camp's share of the HEAD vote mass
    prevalence_n: int | None = None           # distinct answers proposing this camp's practice
    prevalence_share: float | None = None     # prevalence_n / all answers in the group
    voting_agreement: float | None = None     # within-camp vote cohesion (practice_clusters)
    author_pr_share: float | None = None      # this camp's share of the query's network centrality (raw PR)
    top_author: str | None = None             # most-central author whose PRIMARY camp is this one
    top_author_pr_share: float | None = None  # that author's own share of the query's centrality
    authority_coverage: float | None = None   # fraction of this camp's answers whose author is in the graph
    exemplar: str | None = None               # highest-vote practice sentence (representative)
    is_vote_leader: bool = False              # this camp == query_signal.vote_leader_cluster
    is_authority_backed: bool = False         # this camp == the most-central voice's camp (top1_author_cluster)


@dataclass
class AnswerCard:
    """One answer's detail, for the click-to-detail panel (one entry per answer, not per point)."""

    answer_id: int
    author: str | None
    reputation: int | None
    vote: int
    is_accepted: bool
    date: int | None
    year: int | None
    n_practices: int
    # body is deliberately NOT stored here — it's group-independent and bulky, so it's kept out of the
    # cache and lazy-loaded on click via GET /answer/{id} (serve reads it straight from the DB).


@dataclass
class Breakdown:
    group_id: str
    authority_source: str  # provenance → dashboard can show an honest "proxy" badge
    group_source: str
    clusters: list[ClusterShell]
    points: list[PracticePoint]
    answers: list[AnswerCard]
    narrative: dict | None = None  # D-4 per-query narrative → meta.narrative; None until generated
    db_window: str | None = None  # configured data window (e.g. "2021–2026") → honest meta provenance
    n_out_of_window: int | None = None  # FIXTURE/snapshot path only: answers predating the window (year < start) → authority structurally n/a. Real resolver groups are fully in-window so this is 0. NOT "author absent from the in-window graph" (out-of-graph — a distinct null cause that DOES occur on the real path)

    def to_dict(self) -> dict:
        """Flatten to the dashboard's JSON shape (signals hoisted onto each point; answers keyed
        by id for O(1) panel lookup)."""
        return {
            "meta": {
                "group_id": self.group_id,
                "authority_source": self.authority_source,
                "group_source": self.group_source,
                "n_points": len(self.points),
                "n_clusters": len(self.clusters),
                "narrative": self.narrative,  # D-4 (separate cheap LLM step); renderer no-ops if null
                "db_window": self.db_window,  # ADR 0009: out-of-window answers (snapshot/pre-2021) render n/a
                "n_out_of_window": self.n_out_of_window,
            },
            "clusters": [asdict(c) for c in self.clusters],
            "points": [
                {**{k: v for k, v in asdict(p).items() if k != "signals"}, **asdict(p.signals)}
                for p in self.points
            ],
            "answers": {a.answer_id: asdict(a) for a in self.answers},
        }


def year_of(epoch: int | None) -> int | None:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).year if epoch else None
