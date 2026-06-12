"""User-User interaction graph for the authority module.

Edges encode the relation: ``asker --(answer)--> answerer`` (configurable via ``config.EDGE_DIRECTION``). 
Edge weight = number of answers the same
ordered (asker, answerer) pair contributes. 
Yearly subgraphs split edges by the answer's creation year.

Filters applied at graph-build time (all driven by ``config``):
* drop self-answers
* drop years outside [VALID_YEAR_MIN, VALID_YEAR_MAX]
* drop users in EXCLUDE_USER_IDS (e.g. user_id=0, SO's deleted-user marker)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import networkx as nx

from . import config
from .db import AuthorityDB, year_of


@dataclass
class GraphBundle:
    full: nx.DiGraph
    yearly: dict[int, nx.DiGraph]
    edge_direction: str
    dropped_self_answers: int = 0
    skipped_missing_user: int = 0
    skipped_bad_year: int = 0
    skipped_excluded_user: int = 0
    years_present: list[int] = field(default_factory=list)

    def stats(self) -> dict:
        return {
            "edge_direction": self.edge_direction,
            "full_nodes": self.full.number_of_nodes(),
            "full_edges": self.full.number_of_edges(),
            "full_total_weight": int(
                sum(d["weight"] for _, _, d in self.full.edges(data=True))
            ),
            "dropped_self_answers": self.dropped_self_answers,
            "skipped_missing_user": self.skipped_missing_user,
            "skipped_bad_year": self.skipped_bad_year,
            "skipped_excluded_user": self.skipped_excluded_user,
            "years_present": self.years_present,
            "yearly_edge_counts": {
                y: g.number_of_edges() for y, g in sorted(self.yearly.items())
            },
        }


def _orient(asker: int, answerer: int) -> tuple[int, int]:
    if config.EDGE_DIRECTION == "asker_to_answerer":
        return asker, answerer
    if config.EDGE_DIRECTION == "answerer_to_asker":
        return answerer, asker
    raise ValueError(
        f"Unknown EDGE_DIRECTION {config.EDGE_DIRECTION!r}"
    )


def _add_weighted(g: nx.DiGraph, src: int, dst: int, year: int | None) -> None:
    if g.has_edge(src, dst):
        g[src][dst]["weight"] += 1
    else:
        g.add_edge(src, dst, weight=1, year=year)


def _year_ok(yr: int | None) -> bool:
    if yr is None:
        return False
    return config.VALID_YEAR_MIN <= yr <= config.VALID_YEAR_MAX


def build_graph(db: AuthorityDB) -> GraphBundle:
    if config.EDGE_SOURCE == "interactions":
        return _build_from_interactions(db)
    return _build_from_tables(db)


def _build_from_tables(db: AuthorityDB) -> GraphBundle:
    owner_of_question = db.question_owner_map()
    full = nx.DiGraph()
    yearly: dict[int, nx.DiGraph] = {}
    dropped_self = 0
    skipped_missing = 0
    skipped_year = 0
    skipped_excluded = 0
    year_counter: Counter[int] = Counter()
    excluded = config.EXCLUDE_USER_IDS

    for ans in db.iter_answers():
        answerer = ans.owner_user_id
        asker = owner_of_question.get(ans.question_id)

        if asker is None or answerer is None:
            skipped_missing += 1
            continue
        if asker in excluded or answerer in excluded:
            skipped_excluded += 1
            continue
        if config.DROP_SELF_ANSWERS and asker == answerer:
            dropped_self += 1
            continue

        yr = year_of(ans.creation_date)
        if not _year_ok(yr):
            skipped_year += 1
            continue

        src, dst = _orient(asker, answerer)
        _add_weighted(full, src, dst, yr)
        year_counter[yr] += 1
        if yr not in yearly:
            yearly[yr] = nx.DiGraph()
        _add_weighted(yearly[yr], src, dst, yr)

    return GraphBundle(
        full=full,
        yearly=yearly,
        edge_direction=config.EDGE_DIRECTION,
        dropped_self_answers=dropped_self,
        skipped_missing_user=skipped_missing,
        skipped_bad_year=skipped_year,
        skipped_excluded_user=skipped_excluded,
        years_present=sorted(year_counter),
    )


def _build_from_interactions(db: AuthorityDB) -> GraphBundle:
    full = nx.DiGraph()
    yearly: dict[int, nx.DiGraph] = {}
    dropped_self = 0
    skipped_missing = 0
    skipped_year = 0
    skipped_excluded = 0
    year_counter: Counter[int] = Counter()
    excluded = config.EXCLUDE_USER_IDS

    for src_user, tgt_user, yr in db.iter_answer_interactions():
        answerer, asker = src_user, tgt_user
        if asker is None or answerer is None:
            skipped_missing += 1
            continue
        if asker in excluded or answerer in excluded:
            skipped_excluded += 1
            continue
        if config.DROP_SELF_ANSWERS and asker == answerer:
            dropped_self += 1
            continue
        if not _year_ok(yr):
            skipped_year += 1
            continue

        src, dst = _orient(asker, answerer)
        _add_weighted(full, src, dst, yr)
        year_counter[yr] += 1
        if yr not in yearly:
            yearly[yr] = nx.DiGraph()
        _add_weighted(yearly[yr], src, dst, yr)

    return GraphBundle(
        full=full,
        yearly=yearly,
        edge_direction=config.EDGE_DIRECTION,
        dropped_self_answers=dropped_self,
        skipped_missing_user=skipped_missing,
        skipped_bad_year=skipped_year,
        skipped_excluded_user=skipped_excluded,
        years_present=sorted(year_counter),
    )
