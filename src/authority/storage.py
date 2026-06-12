"""Authority persistence.

``user_authority`` table holds:
  1. Basic activity stats (Q/A counts, accept rates)
  2. Every computed centrality score
  3. Louvain community id
  4. The synthesized authority score
"""

from __future__ import annotations

import sqlite3
import time

from . import config
from .authority import AuthorityResult, CommunityResult, PageRankResult
from .centrality import CentralityResult
from .graph import GraphBundle
from .user_stats import UserStats


# --------------------------------------------------------------------------
# Schema management
# --------------------------------------------------------------------------
def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_users_table(conn: sqlite3.Connection) -> None:
    """Ensure the source `users` table has the columns we'll write to."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "users" not in tables:
        conn.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, "
            "display_name TEXT, reputation INTEGER)"
        )
    cols = _columns(conn, "users")
    for col, decl in (
        ("pagerank_full", "REAL"),
        ("community_id", "INTEGER"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")


# columns added to the fat authority table for each centrality method
_CENTRALITY_COL_PREFIX = "cent_"


def _ensure_user_authority(
    conn: sqlite3.Connection, centrality_methods: list[str]
) -> None:
    """Create/migrate the wide user_authority table.

    Base columns are fixed; one column per centrality method is added on
    demand so adding a new method later is non-breaking.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_authority (
            user_id INTEGER PRIMARY KEY,
            -- identity / SO native
            display_name        TEXT,
            reputation          INTEGER,
            -- activity stats
            question_count      INTEGER,
            answer_count        INTEGER,
            accepted_answer_count INTEGER,
            answerer_accept_rate REAL,
            asker_accept_rate   REAL,
            total_question_score INTEGER,
            total_answer_score  INTEGER,
            first_activity_year INTEGER,
            last_activity_year  INTEGER,
            -- network
            community_id        INTEGER,
            -- synthesized authority
            authority_score     REAL,
            authority_scheme    TEXT,
            comp_pagerank       REAL,
            comp_tag_reputation REAL,
            comp_accept_rate    REAL
        )
        """
    )
    existing = _columns(conn, "user_authority")
    for method in centrality_methods:
        col = f"{_CENTRALITY_COL_PREFIX}{method}"
        if col not in existing:
            conn.execute(
                f"ALTER TABLE user_authority ADD COLUMN {col} REAL"
            )


def _ensure_yearly_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_pagerank_yearly (
            user_id INTEGER, year INTEGER, pagerank REAL,
            rank_percentile REAL, low_data INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, year)
        )
        """
    )


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS authority_run_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _progress "
        "(tag TEXT, stage TEXT, updated_at INTEGER, PRIMARY KEY (tag, stage))"
    )


# --------------------------------------------------------------------------
# Write everything
# --------------------------------------------------------------------------
def write_results(
    db_path: str,
    bundle: GraphBundle,
    pagerank: PageRankResult,
    communities: CommunityResult,
    authority: AuthorityResult,
    user_stats: UserStats,
    centralities: CentralityResult,
    tag: str = "reactjs",
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_users_table(conn)
        _ensure_user_authority(
            conn, list(centralities.scores.keys())
        )
        _ensure_yearly_table(conn)
        _ensure_meta_table(conn)

        # union of every user we have info on
        all_users: set[int] = set(bundle.full.nodes())
        all_users.update(authority.score)
        all_users.update(communities.partition)
        all_users.update(user_stats.all_user_ids())
        for sc in centralities.scores.values():
            all_users.update(sc)

        # seed users table so the UPDATEs below don't lose anyone
        conn.executemany(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            [(uid,) for uid in all_users],
        )

        # ------- update source users.pagerank_full + community_id ---------
        conn.executemany(
            "UPDATE users SET pagerank_full = ? WHERE user_id = ?",
            [(s, uid) for uid, s in pagerank.full.items()],
        )
        conn.executemany(
            "UPDATE users SET community_id = ? WHERE user_id = ?",
            [(cid, uid) for uid, cid in communities.partition.items()],
        )

        # ------- yearly PageRank -----------------------------------------
        low_set = set(pagerank.low_data_years)
        rows_yearly = []
        for year, scores in pagerank.yearly.items():
            pct = pagerank.yearly_percentile.get(year, {})
            for uid, score in scores.items():
                rows_yearly.append(
                    (uid, year, score, pct.get(uid, 0.0),
                     1 if year in low_set else 0)
                )
        conn.executemany(
            "INSERT OR REPLACE INTO user_pagerank_yearly "
            "(user_id, year, pagerank, rank_percentile, low_data) "
            "VALUES (?, ?, ?, ?, ?)",
            rows_yearly,
        )

        # ------- read display_name + reputation from `users` to denorm ----
        name_rep: dict[int, tuple[str | None, int | None]] = {}
        for r in conn.execute(
            "SELECT user_id, display_name, reputation FROM users"
        ):
            name_rep[r[0]] = (r[1], r[2])

        # ------- assemble fat user_authority rows ------------------
        cent_methods = list(centralities.scores.keys())
        cent_cols = [f"{_CENTRALITY_COL_PREFIX}{m}" for m in cent_methods]

        base_cols = (
            "user_id, display_name, reputation, "
            "question_count, answer_count, accepted_answer_count, "
            "answerer_accept_rate, asker_accept_rate, "
            "total_question_score, total_answer_score, "
            "first_activity_year, last_activity_year, "
            "community_id, "
            "authority_score, authority_scheme, "
            "comp_pagerank, comp_tag_reputation, comp_accept_rate"
        )
        n_base = 18
        all_cols = base_cols + (", " + ", ".join(cent_cols) if cent_cols else "")
        placeholders = ",".join(["?"] * (n_base + len(cent_methods)))
        sql_upsert = (
            f"INSERT OR REPLACE INTO user_authority ({all_cols}) "
            f"VALUES ({placeholders})"
        )

        rows = []
        for uid in all_users:
            name, rep = name_rep.get(uid, (None, None))
            comp = authority.components.get(uid, {})
            row = [
                uid,
                name,
                rep,
                user_stats.question_count.get(uid, 0),
                user_stats.answer_count.get(uid, 0),
                user_stats.accepted_answer_count.get(uid, 0),
                user_stats.answerer_accept_rate(uid),
                user_stats.asker_accept_rate(uid),
                user_stats.total_question_score.get(uid, 0),
                user_stats.total_answer_score.get(uid, 0),
                user_stats.first_activity_year.get(uid),
                user_stats.last_activity_year.get(uid),
                communities.partition.get(uid),
                authority.score.get(uid, 0.0),
                authority.scheme,
                comp.get("pagerank", 0.0),
                comp.get("tag_reputation", 0.0),
                comp.get("accept_rate", 0.0),
            ]
            for m in cent_methods:
                row.append(centralities.scores[m].get(uid, 0.0))
            rows.append(tuple(row))

        conn.executemany(sql_upsert, rows)

        # ------- run metadata --------------------------------------------
        meta = {
            "edge_direction": bundle.edge_direction,
            "modularity": f"{communities.modularity:.6f}",
            "n_communities": str(communities.n_communities),
            "authority_scheme": authority.scheme,
            "low_data_years": ",".join(map(str, pagerank.low_data_years)),
            "years_present": ",".join(map(str, bundle.years_present)),
            "full_nodes": str(bundle.full.number_of_nodes()),
            "full_edges": str(bundle.full.number_of_edges()),
            "centrality_methods": ",".join(cent_methods),
            "centrality_notes": "; ".join(
                f"{m}={n}" for m, n in centralities.notes.items()
            ),
            "config_year_min": str(config.VALID_YEAR_MIN),
            "config_year_max": str(config.VALID_YEAR_MAX),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO authority_run_meta (key, value) VALUES (?, ?)",
            list(meta.items()),
        )

        conn.execute(
            "INSERT OR REPLACE INTO _progress (tag, stage, updated_at) "
            "VALUES (?, ?, ?)",
            (tag, config.STAGE_NAME, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
