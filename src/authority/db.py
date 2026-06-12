"""Database access for the authority module.

Read-only with respect to source tables (questions / answers / users /interactions).

Authority-owned tables are created by storage.py.
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from dataclasses import dataclass
from typing import Iterator


# --------------------------------------------------------------------------
# Records consumed by the rest of the authority module
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class QuestionRow:
    question_id: int
    owner_user_id: int | None
    creation_date: int | None
    score: int = 0


@dataclass(frozen=True)
class AnswerRow:
    answer_id: int
    question_id: int
    owner_user_id: int | None
    creation_date: int | None
    is_accepted: int = 0
    score: int = 0


@dataclass(frozen=True)
class UserRow:
    user_id: int
    display_name: str | None
    reputation: int | None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _to_unix(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = _dt.datetime.strptime(s[: len(fmt) + 2], fmt)
            return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def year_of(unix_ts: int | None) -> int | None:
    if unix_ts is None:
        return None
    return _dt.datetime.fromtimestamp(unix_ts, _dt.timezone.utc).year


# --------------------------------------------------------------------------
# DB wrapper
# --------------------------------------------------------------------------
class AuthorityDB:
    """Read-only access to the SO collection DB."""

    def __init__(self, db_path: str):
        self.db_path = db_path

        # Pre-flight: detect SQLite's silent "create empty file on open" gotcha.
        # If the user points at a path that doesn't exist, SQLite would happily
        # create an empty file there -- giving a "Tables: []" error far from
        # the actual problem.
        abs_path = os.path.abspath(db_path)
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"DB file does not exist: {abs_path}\n"
                f"  (SQLite would silently create an empty file here; "
                f"aborting to avoid confusion.)\n"
                f"  Check the path, or use an absolute path. "
                f"You can locate the file with:\n"
                f"    find ~ -name '{os.path.basename(db_path)}' -size +1M 2>/dev/null"
            )
        if os.path.getsize(db_path) == 0:
            raise RuntimeError(
                f"DB file is 0 bytes (empty): {abs_path}\n"
                f"  This usually means SQLite created an empty file when a "
                f"previous run pointed at a missing path. Delete this file and "
                f"point at the real DB."
            )

        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._tables = self._list_tables()

        if not self._tables:
            raise RuntimeError(
                f"DB has no tables: {abs_path}\n"
                f"  File exists but contains no tables. Either it was created "
                f"empty by a previous mistaken run, or you're pointing at the "
                f"wrong file. Try:\n"
                f"    sqlite3 '{db_path}' '.tables'   # see what's in there\n"
                f"    find ~ -name '{os.path.basename(db_path)}' -size +1M 2>/dev/null"
            )

        self._question_table = self._pick_question_table()

    # ----- introspection --------------------------------------------------
    def _list_tables(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}

    def _columns(self, table: str) -> set[str]:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}

    def _pick_question_table(self) -> str:
        if "questions" in self._tables:
            return "questions"
        if "posts" in self._tables:
            return "posts"
        raise RuntimeError(
            "No question table found (expected 'questions' or 'posts'). "
            f"Tables: {sorted(self._tables)}"
        )

    def _pk_column(self, table: str) -> str:
        cols = self._columns(table)
        for c in (f"{table[:-1]}_id", f"{table}_id", "id"):
            if c in cols:
                return c
        info = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in info:
            if r[5]:
                return r[1]
        raise RuntimeError(f"Cannot determine PK of {table}")

    def has_table(self, name: str) -> bool:
        return name in self._tables

    @property
    def question_table(self) -> str:
        return self._question_table

    # ----- questions ------------------------------------------------------
    def iter_questions(self) -> Iterator[QuestionRow]:
        t = self._question_table
        pk = self._pk_column(t)
        cols = self._columns(t)
        owner = "owner_user_id" if "owner_user_id" in cols else "NULL"
        cdate = "creation_date" if "creation_date" in cols else "NULL"
        score = "score" if "score" in cols else "0"
        q = f"SELECT {pk} AS qid, {owner} AS owner, {cdate} AS cdate, {score} AS score FROM {t}"
        for r in self.conn.execute(q):
            yield QuestionRow(
                question_id=r["qid"],
                owner_user_id=r["owner"],
                creation_date=_to_unix(r["cdate"]),
                score=int(r["score"] or 0),
            )

    def question_owner_map(self) -> dict[int, int | None]:
        return {q.question_id: q.owner_user_id for q in self.iter_questions()}

    # ----- answers --------------------------------------------------------
    def iter_answers(self) -> Iterator[AnswerRow]:
        t = "answers"
        if t not in self._tables:
            raise RuntimeError("No 'answers' table found.")
        cols = self._columns(t)
        pk = "answer_id" if "answer_id" in cols else "id"
        cdate = "creation_date" if "creation_date" in cols else "NULL"
        accepted = "is_accepted" if "is_accepted" in cols else "0"
        score = "score" if "score" in cols else "0"
        q = (
            f"SELECT {pk} AS aid, question_id AS qid, owner_user_id AS owner, "
            f"{cdate} AS cdate, {accepted} AS accepted, {score} AS score "
            f"FROM {t}"
        )
        for r in self.conn.execute(q):
            yield AnswerRow(
                answer_id=r["aid"],
                question_id=r["qid"],
                owner_user_id=r["owner"],
                creation_date=_to_unix(r["cdate"]),
                is_accepted=int(r["accepted"] or 0),
                score=int(r["score"] or 0),
            )

    # ----- users ----------------------------------------------------------
    def iter_users(self) -> Iterator[UserRow]:
        t = "users"
        if t not in self._tables:
            return
        cols = self._columns(t)
        pk = "user_id" if "user_id" in cols else "id"
        name = "display_name" if "display_name" in cols else "NULL"
        rep = "reputation" if "reputation" in cols else "NULL"
        q = f"SELECT {pk} AS uid, {name} AS name, {rep} AS rep FROM {t}"
        for r in self.conn.execute(q):
            yield UserRow(
                user_id=r["uid"],
                display_name=r["name"],
                reputation=r["rep"],
            )

    def user_reputation_map(self) -> dict[int, int]:
        return {
            u.user_id: (u.reputation or 0)
            for u in self.iter_users()
            if u.user_id is not None
        }

    # ----- dashboard content lookups --------------------------------------
    def question_columns(self) -> set[str]:
        return self._columns(self._question_table)

    def answer_columns(self) -> set[str]:
        return self._columns("answers")

    def user_columns(self) -> set[str]:
        return self._columns("users") if "users" in self._tables else set()

    def user_info(self, user_id: int) -> dict | None:
        """Full user row (for dashboard detail view)."""
        if "users" not in self._tables:
            return None
        pk = "user_id" if "user_id" in self._columns("users") else "id"
        r = self.conn.execute(
            f"SELECT * FROM users WHERE {pk} = ?", (user_id,)
        ).fetchone()
        return dict(r) if r else None

    def questions_by_user(self, user_id: int, limit: int = 50) -> list[dict]:
        t = self._question_table
        pk = self._pk_column(t)
        cols = self.question_columns()
        select_cols = [pk]
        for c in ("title", "body", "body_text", "score", "answer_count",
                  "creation_date", "link", "is_closed", "view_count"):
            if c in cols:
                select_cols.append(c)
        sql = (
            f"SELECT {', '.join(select_cols)} FROM {t} "
            f"WHERE owner_user_id = ? ORDER BY score DESC LIMIT ?"
        )
        return [dict(r) for r in self.conn.execute(sql, (user_id, limit))]

    def answers_by_user(self, user_id: int, limit: int = 50) -> list[dict]:
        cols = self.answer_columns()
        select_cols = ["answer_id", "question_id"]
        for c in ("body", "body_text", "score", "is_accepted", "creation_date"):
            if c in cols:
                select_cols.append(c)
        sql = (
            f"SELECT {', '.join(select_cols)} FROM answers "
            f"WHERE owner_user_id = ? ORDER BY score DESC LIMIT ?"
        )
        return [dict(r) for r in self.conn.execute(sql, (user_id, limit))]

    def answers_for_question(
        self, question_id: int, limit: int = 50
    ) -> list[dict]:
        """All answers to a given question; accepted first, then by score."""
        cols = self.answer_columns()
        select_cols = ["answer_id", "question_id", "owner_user_id"]
        for c in (
            "owner_display_name",
            "body",
            "body_text",
            "score",
            "is_accepted",
            "creation_date",
        ):
            if c in cols:
                select_cols.append(c)
        sql = (
            f"SELECT {', '.join(select_cols)} FROM answers "
            f"WHERE question_id = ? "
            f"ORDER BY is_accepted DESC, score DESC "
            f"LIMIT ?"
        )
        return [dict(r) for r in self.conn.execute(sql, (question_id, limit))]

    def question_by_id(self, question_id: int) -> dict | None:
        t = self._question_table
        pk = self._pk_column(t)
        r = self.conn.execute(
            f"SELECT * FROM {t} WHERE {pk} = ?", (question_id,)
        ).fetchone()
        return dict(r) if r else None

    # ----- alternative edge source ----------------------------------------
    def iter_answer_interactions(self) -> Iterator[tuple[int, int, int | None]]:
        if "interactions" not in self._tables:
            raise RuntimeError("No 'interactions' table found.")
        q = (
            "SELECT source_user_id, target_user_id, creation_date "
            "FROM interactions WHERE interaction_type = 'answer'"
        )
        for r in self.conn.execute(q):
            yield (
                r["source_user_id"],
                r["target_user_id"],
                year_of(_to_unix(r["creation_date"])),
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "AuthorityDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
