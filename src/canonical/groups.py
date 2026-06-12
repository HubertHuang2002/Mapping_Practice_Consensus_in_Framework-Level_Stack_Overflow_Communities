"""Real CanonicalGroupProvider over the normalized store (no per-group snapshot).

The no-snapshot counterpart to proxy.DuplicateChainGroups: a group's members are the resolved
canonical_group rows the real resolver wrote ('equivalent', plus legacy NULL-decision seed rows);
their answers, authors and reputation come from the main DB. Every member is an in-window question,
so every answer is in-window — n_out_of_window stays 0 on this path (the snapshot / out-of-window
machinery is proxy-only). Every member is just a pooled answer: there is no canonical/dup
distinction on the real path (that was SO-duplicate-proxy vocabulary, now retired).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from contract.ports import Answer, CanonicalGroup


class DbCanonicalGroups:
    """CanonicalGroupProvider: group_id → member answers, read from canonical_group + the DB."""

    source_name = "openai_rag_gate"  # provenance badge: real resolver group (RAG + LLM gate), not the SO-dup proxy

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def fetch(self, group_id: str) -> CanonicalGroup:
        con = sqlite3.connect(self.db_path)
        try:
            qids = [r[0] for r in con.execute(
                "SELECT question_id FROM canonical_group WHERE group_id = ? "
                "AND (gate_decision IS NULL OR gate_decision = 'equivalent')", (group_id,))]
            if not qids:
                return CanonicalGroup(group_id, [])
            reputation = dict(con.execute("SELECT user_id, reputation FROM users"))
            marks = ",".join("?" * len(qids))
            rows = con.execute(
                f"SELECT answer_id, score, is_accepted, creation_date, owner_user_id, "
                f"body_text, owner_display_name "
                f"FROM answers WHERE question_id IN ({marks})", qids)
            answers = [
                Answer(aid, score, bool(acc),
                       cdate, reputation.get(owner), body, author, owner_user_id=owner)
                for aid, score, acc, cdate, owner, body, author in rows
            ]
            return CanonicalGroup(group_id, answers)
        finally:
            con.close()
