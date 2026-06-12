"""Per-user basic statistics for the authority module.

For each user_id that appears in the graph (asker, answerer, or both) we compute simple counts.

Two flavors of accept rate:

* ``answerer_accept_rate``   = (# of this user's answers that were accepted)
                                / (# of this user's answers)
                              -- measures *answering* quality.

* ``asker_accept_rate``      = (# of this user's questions with an accepted
                                  answer) / (# of this user's questions)
                              -- the SO-native definition; measures how
                              often the user marks an answer accepted, i.e.
                              participation as an asker.

The authority module uses ``answerer_accept_rate`` for the multi-source authority synthesis.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .db import AuthorityDB, year_of


@dataclass
class UserStats:
    """Per-user activity stats.

    All dicts are keyed by user_id.
    """

    question_count: dict[int, int] = field(default_factory=dict)
    answer_count: dict[int, int] = field(default_factory=dict)
    accepted_answer_count: dict[int, int] = field(default_factory=dict)
    question_with_accepted_count: dict[int, int] = field(default_factory=dict)
    total_question_score: dict[int, int] = field(default_factory=dict)
    total_answer_score: dict[int, int] = field(default_factory=dict)
    first_activity_year: dict[int, int] = field(default_factory=dict)
    last_activity_year: dict[int, int] = field(default_factory=dict)

    # convenience derived metrics --------------------------------------------
    def answerer_accept_rate(self, user_id: int) -> float:
        ac = self.answer_count.get(user_id, 0)
        if ac == 0:
            return 0.0
        return self.accepted_answer_count.get(user_id, 0) / ac

    def asker_accept_rate(self, user_id: int) -> float:
        qc = self.question_count.get(user_id, 0)
        if qc == 0:
            return 0.0
        return self.question_with_accepted_count.get(user_id, 0) / qc

    def all_user_ids(self) -> set[int]:
        return (
            set(self.question_count)
            | set(self.answer_count)
            | set(self.total_question_score)
            | set(self.total_answer_score)
        )


def compute_user_stats(db: AuthorityDB) -> UserStats:
    """Pass over questions + answers once, accumulate per-user stats."""
    stats = UserStats()

    # which question_ids have an accepted answer (asker-side metric)
    accepted_questions: set[int] = set()
    # used to assign asker-side question_with_accepted_count later
    question_owner: dict[int, int | None] = {}

    # ----- pass 1: questions ----------------------------------------------
    q_count: Counter[int] = Counter()
    q_score: defaultdict[int, int] = defaultdict(int)
    first_yr: dict[int, int] = {}
    last_yr: dict[int, int] = {}

    for q in db.iter_questions():
        question_owner[q.question_id] = q.owner_user_id
        if q.owner_user_id is None:
            continue
        uid = q.owner_user_id
        q_count[uid] += 1
        q_score[uid] += q.score
        yr = year_of(q.creation_date)
        if yr is not None:
            if uid not in first_yr or yr < first_yr[uid]:
                first_yr[uid] = yr
            if uid not in last_yr or yr > last_yr[uid]:
                last_yr[uid] = yr

    # ----- pass 2: answers ------------------------------------------------
    a_count: Counter[int] = Counter()
    a_accepted: Counter[int] = Counter()
    a_score: defaultdict[int, int] = defaultdict(int)

    for ans in db.iter_answers():
        if ans.owner_user_id is None:
            continue
        uid = ans.owner_user_id
        a_count[uid] += 1
        a_score[uid] += ans.score
        if ans.is_accepted:
            a_accepted[uid] += 1
            accepted_questions.add(ans.question_id)
        yr = year_of(ans.creation_date)
        if yr is not None:
            if uid not in first_yr or yr < first_yr[uid]:
                first_yr[uid] = yr
            if uid not in last_yr or yr > last_yr[uid]:
                last_yr[uid] = yr

    # ----- derive asker-side accepted question count ----------------------
    q_with_accepted: Counter[int] = Counter()
    for qid in accepted_questions:
        owner = question_owner.get(qid)
        if owner is not None:
            q_with_accepted[owner] += 1

    stats.question_count = dict(q_count)
    stats.answer_count = dict(a_count)
    stats.accepted_answer_count = dict(a_accepted)
    stats.question_with_accepted_count = dict(q_with_accepted)
    stats.total_question_score = dict(q_score)
    stats.total_answer_score = dict(a_score)
    stats.first_activity_year = first_yr
    stats.last_activity_year = last_yr
    return stats
