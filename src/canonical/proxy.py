"""SO-data PROXY for the canonical-group seam — the q54069253 fixture's rebuild path only.

One canonical group == one SO duplicate chain. Canonical (out-of-window) answers come from the probe
snapshot JSON; in-window dup answers and author reputation come from the main DB. SUPERSEDED on the
live path by canonical.groups.DbCanonicalGroups (the real resolver's groups need no snapshot and are
fully in-window). Retained so the q54069253 fixture stays reproducible — not used for real queries.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from contract.ports import Answer, CanonicalGroup


class DuplicateChainGroups:
    """CanonicalGroupProvider, proxied: resolve a group_id to its member answers."""

    source_name = "so_duplicate_links"

    def __init__(self, snapshot: Path, db_path: Path):
        self.snapshot = Path(snapshot)
        self.db_path = Path(db_path)

    def fetch(self, group_id: str) -> CanonicalGroup:
        snap = json.loads(self.snapshot.read_text())
        con = sqlite3.connect(self.db_path)
        try:
            reputation = dict(con.execute("SELECT user_id, reputation FROM users"))
            answers = [
                Answer(a["answer_id"], a["score"], bool(a["is_accepted"]),
                       a["creation_date"], reputation.get(a["owner_user_id"]),
                       a.get("body_text"), a.get("owner_display_name"),
                       owner_user_id=a["owner_user_id"])
                for a in snap["canonical_answers"]
            ]
            dup_qids = snap["dup_question_ids"]
            placeholders = ",".join("?" * len(dup_qids))
            rows = con.execute(
                f"SELECT answer_id, score, is_accepted, creation_date, owner_user_id, "
                f"body_text, owner_display_name "
                f"FROM answers WHERE question_id IN ({placeholders})",
                dup_qids,
            )
            answers += [
                Answer(aid, score, bool(acc), cdate, reputation.get(owner), body, author,
                       owner_user_id=owner)
                for aid, score, acc, cdate, owner, body, author in rows
            ]
        finally:
            con.close()
        return CanonicalGroup(group_id, answers)
