"""
Module A — Stack Overflow Data Collection (React)
=================================================
Fetch & DB: collect Stack Overflow data for a single tag (reactjs) into SQLite.

Time range: 63 months (2021-01 ~ 2026-04), collected in 5 yearly sub-periods.
Balance strategy: NO CAP — collect every question SO has in this window.
Storage: SQLite

Schema design goals:
  - Every post (Q/A/C) carries one-hot framework flags inherited from
    the question (is_reactjs in v2; the design generalizes to N tags)
  - Every post linked to an owner_user_id for graph construction
  - Comments store reply_to_user_id for precise interaction edges
  - body_text pre-cleaned for NLP pipeline (code blocks removed)

Interaction edge types (for graph construction):
  1. answer:        answerer -> question asker
  2. comment_reply: commenter -> reply_to_user (API-provided, most precise)
  3. comment:       commenter -> post owner (fallback when reply_to_user absent)
"""

import requests
import sqlite3
import time
import json
import logging
import re
from html import unescape
from datetime import datetime, timezone
from pathlib import Path

from config import (
    API_BASE, API_KEY, TAGS, DB_PATH,
    REQUEST_DELAY, PAGE_SIZE,
    MAX_QUESTIONS_PER_TAG_PER_YEAR,
    TAG_COLUMNS, ONEHOT_COLS,
)

# DDL fragment + INSERT placeholders for the four one-hot columns.
_ONEHOT_DDL = ", ".join(f"{c} INTEGER DEFAULT 0" for c in ONEHOT_COLS)
_ONEHOT_NAMES = ", ".join(ONEHOT_COLS)
_ONEHOT_PLACEHOLDERS = ", ".join("?" * len(ONEHOT_COLS))


def compute_flags_from_tags(q_tags: list) -> tuple:
    """Return a tuple of 0/1 flags in TAGS order for the given list of SO tags."""
    return tuple(1 if t in q_tags else 0 for t in TAGS)

# Time range: 63 months from 2021-01 to 2026-04 in 5 yearly sub-periods.
# Last sub-period (2026-01 ~ 2026-04) is only 3 months — partial year.
DATE_FROM = datetime(2021, 1, 1, tzinfo=timezone.utc)
DATE_TO = datetime(2026, 4, 1, tzinfo=timezone.utc)

YEARLY_PERIODS = [
    (datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2022, 1, 1, tzinfo=timezone.utc)),
    (datetime(2022, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 1, tzinfo=timezone.utc)),
    (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 1, tzinfo=timezone.utc)),
    (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)),
    (datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2026, 4, 1, tzinfo=timezone.utc)),
]

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML Cleaning
# ---------------------------------------------------------------------------

_RE_CODE_BLOCK = re.compile(r"<pre[^>]*>.*?</pre>", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"<code[^>]*>.*?</code>", re.DOTALL)
_RE_TAGS = re.compile(r"<[^>]+>")
_RE_MULTI_SPACE = re.compile(r"[ \t]+")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")


def clean_html(html: str) -> str:
    """Convert SO HTML body to clean plain text, replacing code blocks with [CODE] token."""
    if not html:
        return ""
    text = _RE_CODE_BLOCK.sub(" [CODE] ", html)
    text = _RE_INLINE_CODE.sub(" [CODE] ", text)
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("</p>", "\n").replace("</li>", "\n")
    text = _RE_TAGS.sub("", text)
    text = unescape(text)
    text = _RE_MULTI_SPACE.sub(" ", text)
    text = _RE_MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Database Setup
# ---------------------------------------------------------------------------

def init_db(db_path: Path) -> sqlite3.Connection:
    """Create SQLite database with full schema for downstream analysis."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    onehot_indexes = "\n    ".join(
        f"CREATE INDEX IF NOT EXISTS idx_{tbl}_{col} ON {tbl}({col});"
        for tbl in ("questions", "answers", "comments", "interactions")
        for col in ONEHOT_COLS
    )
    conn.executescript(f"""
    -- Questions: the root of every thread.
    -- Framework membership is encoded as four 0/1 flags so a question tagged
    -- with multiple of our target frameworks (e.g. django + fastapi) sets
    -- both flags to 1. Replaces the old single-valued `framework_tag` which
    -- was arbitrarily assigned to whichever tag's collection pass ran first.
    CREATE TABLE IF NOT EXISTS questions (
        question_id     INTEGER PRIMARY KEY,
        title           TEXT,
        body            TEXT,           -- raw HTML from API
        body_text       TEXT,           -- cleaned plain text (for NLP)
        tags            TEXT,           -- JSON array of all SO tags
        score           INTEGER,
        view_count      INTEGER,
        answer_count    INTEGER,
        creation_date   INTEGER,        -- Unix timestamp
        owner_user_id   INTEGER,        -- graph node
        owner_display_name TEXT,
        link            TEXT,           -- URL for dashboard citation
        is_closed       INTEGER DEFAULT 0,
        closed_reason   TEXT,
        {_ONEHOT_DDL}  -- one-hot framework membership (set from `tags` array)
    );

    -- Answers: each belongs to a question; one-hot flags are inherited from parent
    CREATE TABLE IF NOT EXISTS answers (
        answer_id       INTEGER PRIMARY KEY,
        question_id     INTEGER,        -- parent question
        body            TEXT,           -- raw HTML
        body_text       TEXT,           -- cleaned plain text (for NLP)
        score           INTEGER,
        is_accepted     INTEGER,
        creation_date   INTEGER,
        owner_user_id   INTEGER,        -- graph node
        owner_display_name TEXT,
        {_ONEHOT_DDL},  -- inherited from parent question (denormalized for query speed)
        FOREIGN KEY (question_id) REFERENCES questions(question_id)
    );

    -- Comments: on questions or answers, with optional reply_to_user
    CREATE TABLE IF NOT EXISTS comments (
        comment_id          INTEGER PRIMARY KEY,
        post_id             INTEGER,    -- question_id or answer_id this comment is on
        post_type           TEXT,       -- 'question' or 'answer'
        question_id         INTEGER,    -- always points to the root question (for traceability)
        body                TEXT,       -- raw HTML
        body_text           TEXT,       -- cleaned plain text (for NLP)
        score               INTEGER,
        creation_date       INTEGER,
        owner_user_id       INTEGER,    -- graph node (commenter)
        owner_display_name  TEXT,
        reply_to_user_id    INTEGER,    -- graph edge target (precise, from API)
        {_ONEHOT_DDL},  -- inherited from root question
        FOREIGN KEY (question_id) REFERENCES questions(question_id)
    );

    -- Users: profile data for authority analysis (PageRank vs reputation)
    CREATE TABLE IF NOT EXISTS users (
        user_id         INTEGER PRIMARY KEY,
        display_name    TEXT,
        reputation      INTEGER,        -- for RQ3: PageRank vs reputation correlation
        badge_gold      INTEGER DEFAULT 0,
        badge_silver    INTEGER DEFAULT 0,
        badge_bronze    INTEGER DEFAULT 0,
        creation_date   INTEGER,
        link            TEXT
    );

    -- Interactions: pre-built edges for graph construction (NetworkX input)
    CREATE TABLE IF NOT EXISTS interactions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_user_id  INTEGER,        -- who initiated (answerer/commenter)
        target_user_id  INTEGER,        -- who was interacted with (asker/replyed-to)
        interaction_type TEXT,           -- 'answer', 'comment', or 'comment_reply'
        post_id         INTEGER,        -- the answer_id or comment_id
        parent_post_id  INTEGER,        -- the post being responded to
        {_ONEHOT_DDL},  -- inherited from root question (per-framework graph filter)
        creation_date   INTEGER         -- for temporal analysis
    );

    -- Progress tracking for resumable collection
    CREATE TABLE IF NOT EXISTS _progress (
        tag         TEXT,
        stage       TEXT,               -- 'questions', 'answers', 'comments', etc.
        updated_at  INTEGER,
        PRIMARY KEY (tag, stage)
    );

    -- Fine-grained comment-fetch progress: one row per post we have already
    -- queried for comments (regardless of whether it had any). Lets the
    -- comments stage resume mid-way instead of re-fetching from batch 0.
    CREATE TABLE IF NOT EXISTS _comments_done (
        post_id     INTEGER,
        post_type   TEXT,               -- 'question' or 'answer'
        PRIMARY KEY (post_id, post_type)
    );

    -- Indexes for efficient downstream queries
    CREATE INDEX IF NOT EXISTS idx_questions_owner ON questions(owner_user_id);
    CREATE INDEX IF NOT EXISTS idx_answers_qid ON answers(question_id);
    CREATE INDEX IF NOT EXISTS idx_answers_owner ON answers(owner_user_id);
    CREATE INDEX IF NOT EXISTS idx_comments_pid ON comments(post_id);
    CREATE INDEX IF NOT EXISTS idx_comments_qid ON comments(question_id);
    CREATE INDEX IF NOT EXISTS idx_comments_owner ON comments(owner_user_id);
    CREATE INDEX IF NOT EXISTS idx_comments_reply ON comments(reply_to_user_id);
    CREATE INDEX IF NOT EXISTS idx_interactions_source ON interactions(source_user_id);
    CREATE INDEX IF NOT EXISTS idx_interactions_target ON interactions(target_user_id);
    -- One per (table, framework flag): supports per-framework filtering
    {onehot_indexes}
    """)
    conn.commit()
    return conn


def mark_progress(conn: sqlite3.Connection, tag: str, stage: str):
    """Record that a collection stage is complete for a given tag."""
    now = int(datetime.now(timezone.utc).timestamp())
    conn.execute(
        "INSERT OR REPLACE INTO _progress (tag, stage, updated_at) VALUES (?, ?, ?)",
        (tag, stage, now),
    )
    conn.commit()


def check_progress(conn: sqlite3.Connection, tag: str, stage: str) -> bool:
    """Check if a collection stage was already completed for a given tag."""
    row = conn.execute(
        "SELECT 1 FROM _progress WHERE tag = ? AND stage = ?", (tag, stage)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# API Helpers
# ---------------------------------------------------------------------------

def api_get(endpoint: str, params: dict, max_items: int = 0) -> list:
    """
    Call SO API with automatic pagination. Returns all items.
    If max_items > 0, stops fetching once the cap is reached.
    """
    params.setdefault("site", "stackoverflow")
    params.setdefault("pagesize", PAGE_SIZE)
    if API_KEY:
        params["key"] = API_KEY

    all_items = []
    page = 1
    # SO API has a known bug where has_more=False appears mid-stream even when
    # more pages exist. Ignore has_more; treat 2 consecutive empty pages as
    # genuine end of stream.
    empty_streak = 0

    # Per-page HTTP retry with exponential backoff. Without a cap, a sustained
    # 429 (incl. Cloudflare's IP-level "error code: 1015") makes the old code
    # hammer the same page forever and get the IP banned. After MAX_RETRIES we
    # give up on this page and return what we have so the caller moves on.
    MAX_RETRIES = 6
    retries = 0

    while True:
        params["page"] = page
        url = f"{API_BASE}/{endpoint}"

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            retries += 1
            if retries > MAX_RETRIES:
                log.error(
                    "HTTP error on %s page %d after %d retries, giving up on this batch: %s",
                    endpoint, page, MAX_RETRIES, e,
                )
                break
            wait = min(5 * (2 ** (retries - 1)), 300)  # 5,10,20,40,80,160 capped at 300
            log.error(
                "HTTP error on %s page %d (retry %d/%d), waiting %ds: %s",
                endpoint, page, retries, MAX_RETRIES, wait, e,
            )
            time.sleep(wait)
            continue

        retries = 0  # reset on a successful request

        if "error_id" in data:
            log.error("API error: %s - %s", data.get("error_name"), data.get("error_message"))
            if data.get("error_id") == 502:  # throttle violation
                backoff = data.get("backoff", 60)
                log.warning("Throttled. Waiting %ds...", backoff)
                time.sleep(backoff)
                continue
            break

        # Respect API backoff field if present
        if "backoff" in data:
            log.warning("Backoff requested: %ds", data["backoff"])
            time.sleep(data["backoff"])

        items = data.get("items", [])
        all_items.extend(items)

        quota = data.get("quota_remaining", "?")
        has_more = data.get("has_more", False)
        log.info(
            "  %s p%d: +%d (total=%d, quota=%s, has_more=%s)",
            endpoint, page, len(items), len(all_items), quota, has_more,
        )

        # Stop if cap reached
        if max_items > 0 and len(all_items) >= max_items:
            all_items = all_items[:max_items]
            log.info("  Reached cap of %d items, stopping pagination.", max_items)
            break

        # End-of-stream: 2 consecutive empty pages (ignore unreliable has_more)
        if not items:
            empty_streak += 1
            if empty_streak >= 2:
                log.info("  End of stream (2 consecutive empty pages)")
                break
        else:
            empty_streak = 0

        page += 1
        time.sleep(REQUEST_DELAY)

    return all_items


# ---------------------------------------------------------------------------
# Data Collection
# ---------------------------------------------------------------------------

def _insert_questions(conn: sqlite3.Connection, items: list, tag: str):
    """Insert question items into the database.

    One-hot framework flags are computed from the question's `tags` array.
    Title is HTML-unescaped (SO API returns raw HTML entities like &#39; in titles).
    `tag` is the collection-loop's current tag, used as a defensive fallback to
    set is_<tag>=1 if the API response omits it from `tags`.
    """
    rows = []
    for q in items:
        owner = q.get("owner", {})
        body_html = q.get("body", "")
        title_clean = unescape(q.get("title", ""))
        body_text = clean_html(body_html)
        q_tags = q.get("tags", [])
        flags = list(compute_flags_from_tags(q_tags))
        if tag in TAGS:
            flags[TAGS.index(tag)] = 1
        rows.append((
            q["question_id"],
            title_clean,
            body_html,
            body_text,
            json.dumps(q_tags),
            q.get("score", 0),
            q.get("view_count", 0),
            q.get("answer_count", 0),
            q.get("creation_date", 0),
            owner.get("user_id"),
            owner.get("display_name", ""),
            q.get("link", ""),
            1 if q.get("closed_date") else 0,
            q.get("closed_reason", ""),
            *flags,
        ))

    conn.executemany(f"""
        INSERT OR IGNORE INTO questions
        (question_id, title, body, body_text, tags, score, view_count,
         answer_count, creation_date, owner_user_id, owner_display_name,
         link, is_closed, closed_reason, {_ONEHOT_NAMES})
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {_ONEHOT_PLACEHOLDERS})
    """, rows)
    conn.commit()
    return len(rows)


def collect_questions(conn: sqlite3.Connection, tag: str) -> list[int]:
    """
    Collect questions for a given tag across 4 yearly periods,
    capped at MAX_QUESTIONS_PER_TAG_PER_YEAR per period for balance.
    """
    if check_progress(conn, tag, "questions"):
        log.info("[%s] questions already collected, loading from DB...", tag)
        col = TAG_COLUMNS[tag]
        rows = conn.execute(
            f"SELECT question_id FROM questions WHERE {col} = 1"
        ).fetchall()
        return [r[0] for r in rows]

    cap = MAX_QUESTIONS_PER_TAG_PER_YEAR  # 0 means no cap
    cap_label = "no cap" if cap == 0 else f"cap={cap}"
    log.info("=== Collecting questions for [%s] (%s × %d periods) ===",
             tag, cap_label, len(YEARLY_PERIODS))

    all_qids = []
    for period_start, period_end in YEARLY_PERIODS:
        ts_from = int(period_start.timestamp())
        ts_to = int(period_end.timestamp())
        label = f"{period_start.strftime('%Y-%m')}~{period_end.strftime('%Y-%m')}"

        items = api_get("questions", {
            "tagged": tag,
            "fromdate": ts_from,
            "todate": ts_to,
            "order": "desc",
            "sort": "votes",  # prioritize high-quality content
            "filter": "withbody",
        }, max_items=cap)  # cap=0 → api_get treats as unlimited

        inserted = _insert_questions(conn, items, tag)
        qids = [q["question_id"] for q in items]
        all_qids.extend(qids)
        log.info("  [%s] %s: %d questions (%s)", tag, label, inserted, cap_label)

    mark_progress(conn, tag, "questions")
    log.info("Total questions for [%s]: %d", tag, len(all_qids))
    return all_qids


def _build_question_flags(conn: sqlite3.Connection, qids: list[int]) -> dict[int, tuple]:
    """Return {question_id: (is_react,)} one-hot flag tuple for the given qids.
    Used by answers/comments to inherit their parent question's flags."""
    if not qids:
        return {}
    result: dict[int, tuple] = {}
    # Batch the IN clause to keep it under SQLite's variable limit
    for i in range(0, len(qids), 500):
        batch = qids[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT question_id, {_ONEHOT_NAMES} FROM questions "
            f"WHERE question_id IN ({placeholders})",
            batch,
        ).fetchall()
        for r in rows:
            result[r[0]] = r[1:]
    return result


def collect_answers(conn: sqlite3.Connection, question_ids: list[int], tag: str) -> list[dict]:
    """Collect answers for all question_ids. Inherits one-hot flags from parent question."""
    if check_progress(conn, tag, "answers"):
        log.info("[%s] answers already collected, loading from DB...", tag)
        # Filter by the matching one-hot column for this tag
        col = TAG_COLUMNS[tag]
        rows = conn.execute(
            f"SELECT answer_id, question_id FROM answers WHERE {col} = 1"
        ).fetchall()
        return [{"answer_id": r[0], "question_id": r[1]} for r in rows]

    log.info("=== Collecting answers for [%s] (%d questions) ===", tag, len(question_ids))

    parent_flags = _build_question_flags(conn, question_ids)
    zero_flags = (0,) * len(ONEHOT_COLS)
    all_answers = []
    batch_size = 100

    for i in range(0, len(question_ids), batch_size):
        batch = question_ids[i:i + batch_size]
        ids_str = ";".join(str(qid) for qid in batch)

        items = api_get(f"questions/{ids_str}/answers", {
            "order": "desc",
            "sort": "creation",
            "filter": "withbody",
        })

        rows = []
        for a in items:
            owner = a.get("owner", {})
            body_html = a.get("body", "")
            flags = parent_flags.get(a.get("question_id"), zero_flags)
            rows.append((
                a["answer_id"],
                a.get("question_id"),
                body_html,
                clean_html(body_html),
                a.get("score", 0),
                1 if a.get("is_accepted", False) else 0,
                a.get("creation_date", 0),
                owner.get("user_id"),
                owner.get("display_name", ""),
                *flags,
            ))
            all_answers.append(a)

        conn.executemany(f"""
            INSERT OR IGNORE INTO answers
            (answer_id, question_id, body, body_text, score, is_accepted,
             creation_date, owner_user_id, owner_display_name, {_ONEHOT_NAMES})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {_ONEHOT_PLACEHOLDERS})
        """, rows)
        conn.commit()
        log.info("  Batch %d-%d: +%d answers", i, i + len(batch), len(rows))

    mark_progress(conn, tag, "answers")
    log.info("Total answers for [%s]: %d", tag, len(all_answers))
    return all_answers


def _build_answer_to_question_map(conn: sqlite3.Connection, tag: str) -> dict[int, int]:
    """Build answer_id -> question_id lookup for linking comments to root question.
    Filtered by the tag's one-hot flag so we only see this tag's collected answers."""
    col = TAG_COLUMNS[tag]
    rows = conn.execute(
        f"SELECT answer_id, question_id FROM answers WHERE {col} = 1"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def collect_comments_for_posts(
    conn: sqlite3.Connection,
    post_ids: list[int],
    post_type: str,
    tag: str,
    answer_to_question: dict[int, int],
    question_flags: dict[int, tuple],
):
    """
    Collect comments for a list of posts.
    One-hot flags are inherited from the comment's root question.
    """
    if not post_ids:
        return []

    # Skip posts already queried for comments in a previous (possibly
    # interrupted) run, so we resume mid-way instead of from batch 0.
    done = {
        row[0]
        for row in conn.execute(
            "SELECT post_id FROM _comments_done WHERE post_type = ?", (post_type,)
        )
    }
    if done:
        before = len(post_ids)
        post_ids = [pid for pid in post_ids if pid not in done]
        log.info("  Resuming: %d/%d %ss already done, %d remaining",
                 before - len(post_ids), before, post_type, len(post_ids))
    if not post_ids:
        log.info("  All %s comments already collected.", post_type)
        return []

    log.info("  Collecting comments for %d %ss...", len(post_ids), post_type)

    zero_flags = (0,) * len(ONEHOT_COLS)
    all_comments = []
    batch_size = 100

    for i in range(0, len(post_ids), batch_size):
        batch = post_ids[i:i + batch_size]
        ids_str = ";".join(str(pid) for pid in batch)

        items = api_get(f"posts/{ids_str}/comments", {
            "order": "desc",
            "sort": "creation",
            "filter": "withbody",
        })

        rows = []
        for c in items:
            owner = c.get("owner", {})
            reply_to = c.get("reply_to_user", {})
            body_html = c.get("body", "")
            pid = c.get("post_id")

            # Resolve question_id: direct if comment on question, via lookup if on answer
            if post_type == "question":
                qid = pid
            else:
                qid = answer_to_question.get(pid)

            flags = question_flags.get(qid, zero_flags)
            rows.append((
                c["comment_id"],
                pid,
                post_type,
                qid,
                body_html,
                clean_html(body_html),
                c.get("score", 0),
                c.get("creation_date", 0),
                owner.get("user_id"),
                owner.get("display_name", ""),
                reply_to.get("user_id"),
                *flags,
            ))
            all_comments.append(c)

        conn.executemany(f"""
            INSERT OR IGNORE INTO comments
            (comment_id, post_id, post_type, question_id, body, body_text, score,
             creation_date, owner_user_id, owner_display_name,
             reply_to_user_id, {_ONEHOT_NAMES})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {_ONEHOT_PLACEHOLDERS})
        """, rows)
        # Record every post in this batch as done (even those with 0 comments)
        # so a resumed run skips them. Same transaction as the inserts above.
        conn.executemany(
            "INSERT OR IGNORE INTO _comments_done (post_id, post_type) VALUES (?, ?)",
            [(pid, post_type) for pid in batch],
        )
        conn.commit()

    log.info("  Got %d comments for %ss", len(all_comments), post_type)
    return all_comments


def collect_comments(conn: sqlite3.Connection, question_ids: list[int], answers: list[dict], tag: str):
    """Collect comments on both questions and answers for a given tag."""
    if check_progress(conn, tag, "comments"):
        log.info("[%s] comments already collected, skipping.", tag)
        return

    log.info("=== Collecting comments for [%s] ===", tag)

    # Build answer->question map for resolving comment's root question
    a2q = _build_answer_to_question_map(conn, tag)
    # One-hot flags for the same questions, inherited onto each comment
    q_flags = _build_question_flags(conn, question_ids)

    # Comments on questions
    collect_comments_for_posts(conn, question_ids, "question", tag, a2q, q_flags)

    # Comments on answers
    answer_ids = [a["answer_id"] for a in answers]
    collect_comments_for_posts(conn, answer_ids, "answer", tag, a2q, q_flags)

    mark_progress(conn, tag, "comments")


# ---------------------------------------------------------------------------
# Build Interactions (Graph Edges)
# ---------------------------------------------------------------------------

def build_interactions(conn: sqlite3.Connection):
    """
    Build user interaction edges from answers and comments.
    Three edge types:
      1. answer:        answerer -> question asker
      2. comment_reply: commenter -> reply_to_user (most precise, from API)
      3. comment:       commenter -> post owner (fallback when reply_to_user is absent)

    Each edge inherits its parent question's four one-hot framework flags so a
    dual-tag question's edges appear in both frameworks' subgraphs.
    Self-loops (user interacting with themselves) are excluded.
    """
    log.info("=== Building interaction edges ===")
    cursor = conn.cursor()

    # Clear and rebuild all edges
    cursor.execute("DELETE FROM interactions")

    # SELECT-side projection of the four flags (always inherited from the
    # parent question `q`, regardless of edge type)
    flag_select = ", ".join(f"q.{c}" for c in ONEHOT_COLS)

    # 1) Answer edges: answerer -> question owner
    cursor.execute(f"""
        INSERT INTO interactions
            (source_user_id, target_user_id, interaction_type, post_id,
             parent_post_id, {_ONEHOT_NAMES}, creation_date)
        SELECT
            a.owner_user_id,
            q.owner_user_id,
            'answer',
            a.answer_id,
            q.question_id,
            {flag_select},
            a.creation_date
        FROM answers a
        JOIN questions q ON a.question_id = q.question_id
        WHERE a.owner_user_id IS NOT NULL
          AND q.owner_user_id IS NOT NULL
          AND a.owner_user_id != q.owner_user_id
    """)
    answer_edges = cursor.rowcount
    log.info("  Answer edges: %d", answer_edges)

    # 2) Comment reply edges: commenter -> reply_to_user (precise, API-provided).
    #    JOIN with questions via comments.question_id to inherit flags.
    cursor.execute(f"""
        INSERT INTO interactions
            (source_user_id, target_user_id, interaction_type, post_id,
             parent_post_id, {_ONEHOT_NAMES}, creation_date)
        SELECT
            c.owner_user_id,
            c.reply_to_user_id,
            'comment_reply',
            c.comment_id,
            c.post_id,
            {flag_select},
            c.creation_date
        FROM comments c
        JOIN questions q ON c.question_id = q.question_id
        WHERE c.reply_to_user_id IS NOT NULL
          AND c.owner_user_id IS NOT NULL
          AND c.owner_user_id != c.reply_to_user_id
    """)
    reply_edges = cursor.rowcount
    log.info("  Comment reply edges (precise): %d", reply_edges)

    # 3a) Fallback: comment on question -> question owner (when no reply_to_user)
    cursor.execute(f"""
        INSERT INTO interactions
            (source_user_id, target_user_id, interaction_type, post_id,
             parent_post_id, {_ONEHOT_NAMES}, creation_date)
        SELECT
            c.owner_user_id,
            q.owner_user_id,
            'comment',
            c.comment_id,
            q.question_id,
            {flag_select},
            c.creation_date
        FROM comments c
        JOIN questions q ON c.post_id = q.question_id
        WHERE c.post_type = 'question'
          AND c.reply_to_user_id IS NULL
          AND c.owner_user_id IS NOT NULL
          AND q.owner_user_id IS NOT NULL
          AND c.owner_user_id != q.owner_user_id
    """)
    cq_edges = cursor.rowcount
    log.info("  Comment-on-question fallback edges: %d", cq_edges)

    # 3b) Fallback: comment on answer -> answer owner (when no reply_to_user)
    cursor.execute(f"""
        INSERT INTO interactions
            (source_user_id, target_user_id, interaction_type, post_id,
             parent_post_id, {_ONEHOT_NAMES}, creation_date)
        SELECT
            c.owner_user_id,
            a.owner_user_id,
            'comment',
            c.comment_id,
            a.answer_id,
            {flag_select},
            c.creation_date
        FROM comments c
        JOIN answers a ON c.post_id = a.answer_id
        JOIN questions q ON a.question_id = q.question_id
        WHERE c.post_type = 'answer'
          AND c.reply_to_user_id IS NULL
          AND c.owner_user_id IS NOT NULL
          AND a.owner_user_id IS NOT NULL
          AND c.owner_user_id != a.owner_user_id
    """)
    ca_edges = cursor.rowcount
    log.info("  Comment-on-answer fallback edges: %d", ca_edges)

    conn.commit()
    total = answer_edges + reply_edges + cq_edges + ca_edges
    log.info("Total interaction edges: %d", total)


# ---------------------------------------------------------------------------
# Collect Users
# ---------------------------------------------------------------------------

def collect_users(conn: sqlite3.Connection):
    """Fetch user profiles for all unique user IDs found in the dataset."""
    log.info("=== Collecting user profiles ===")
    cursor = conn.cursor()

    # Find all user IDs not yet in the users table (including reply_to targets)
    cursor.execute("""
        SELECT DISTINCT user_id FROM (
            SELECT owner_user_id AS user_id FROM questions WHERE owner_user_id IS NOT NULL
            UNION
            SELECT owner_user_id AS user_id FROM answers WHERE owner_user_id IS NOT NULL
            UNION
            SELECT owner_user_id AS user_id FROM comments WHERE owner_user_id IS NOT NULL
            UNION
            SELECT reply_to_user_id AS user_id FROM comments WHERE reply_to_user_id IS NOT NULL
        )
        WHERE user_id NOT IN (SELECT user_id FROM users)
    """)
    user_ids = [row[0] for row in cursor.fetchall()]
    log.info("Found %d new users to fetch", len(user_ids))

    if not user_ids:
        return

    batch_size = 100
    collected = 0

    for i in range(0, len(user_ids), batch_size):
        batch = user_ids[i:i + batch_size]
        ids_str = ";".join(str(uid) for uid in batch)

        items = api_get(f"users/{ids_str}", {
            "order": "desc",
            "sort": "reputation",
            "pagesize": 100,
        })

        rows = []
        for u in items:
            badges = u.get("badge_counts", {})
            rows.append((
                u["user_id"],
                u.get("display_name", ""),
                u.get("reputation", 0),
                badges.get("gold", 0),
                badges.get("silver", 0),
                badges.get("bronze", 0),
                u.get("creation_date", 0),
                u.get("link", ""),
            ))

        conn.executemany("""
            INSERT OR IGNORE INTO users
            (user_id, display_name, reputation, badge_gold, badge_silver,
             badge_bronze, creation_date, link)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        collected += len(rows)

    log.info("Collected %d user profiles", collected)


# ---------------------------------------------------------------------------
# Summary & Validation
# ---------------------------------------------------------------------------

def print_summary(conn: sqlite3.Connection):
    """Print collection statistics and validate readiness for downstream analysis."""
    cursor = conn.cursor()

    print("\n" + "=" * 70)
    print("DATA COLLECTION SUMMARY")
    print("=" * 70)

    # Table counts
    for table in ["questions", "answers", "comments", "users", "interactions"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table:15s}: {cursor.fetchone()[0]:>8,}")

    total_q = cursor.execute("SELECT COUNT(*) FROM questions").fetchone()[0]

    # Per-framework breakdown
    # Each row counts where the framework's one-hot flag is 1, so dual-tagged
    # questions are correctly counted under BOTH frameworks.
    print("\n  --- Per-framework breakdown ---")
    print(f"  {'Tag':15s} | {'Questions':>9s} | {'Answers':>8s} | {'Comments':>8s} | {'Edges':>7s} | {'Users':>6s}")
    print(f"  {'-'*65}")
    for tag in TAGS:
        col = TAG_COLUMNS[tag]
        q = cursor.execute(f"SELECT COUNT(*) FROM questions WHERE {col}=1").fetchone()[0]
        a = cursor.execute(f"SELECT COUNT(*) FROM answers WHERE {col}=1").fetchone()[0]
        c = cursor.execute(f"SELECT COUNT(*) FROM comments WHERE {col}=1").fetchone()[0]
        e = cursor.execute(f"SELECT COUNT(*) FROM interactions WHERE {col}=1").fetchone()[0]
        u = cursor.execute(f"""
            SELECT COUNT(DISTINCT uid) FROM (
                SELECT owner_user_id AS uid FROM questions WHERE {col}=1 AND owner_user_id IS NOT NULL
                UNION
                SELECT owner_user_id AS uid FROM answers   WHERE {col}=1 AND owner_user_id IS NOT NULL
                UNION
                SELECT owner_user_id AS uid FROM comments  WHERE {col}=1 AND owner_user_id IS NOT NULL
            )
        """).fetchone()[0]
        print(f"  {tag:15s} | {q:>9,} | {a:>8,} | {c:>8,} | {e:>7,} | {u:>6,}")

    # Per-year breakdown
    print("\n  --- Per-year breakdown ---")
    print(f"  {'Period':>18s}", end="")
    for tag in TAGS:
        print(f" {tag:>10s}", end="")
    print("    Total")
    for period_start, period_end in YEARLY_PERIODS:
        ts_from = int(period_start.timestamp())
        ts_to = int(period_end.timestamp())
        label = f"{period_start.strftime('%Y-%m')}~{period_end.strftime('%Y-%m')}"
        print(f"  {label:>18s}", end="")
        row_total = 0
        for tag in TAGS:
            col = TAG_COLUMNS[tag]
            n = cursor.execute(
                f"SELECT COUNT(*) FROM questions WHERE {col}=1 AND creation_date>=? AND creation_date<?",
                (ts_from, ts_to)
            ).fetchone()[0]
            row_total += n
            print(f" {n:>10,}", end="")
        print(f"  {row_total:>6,}")

    # Interaction types
    cursor.execute("""
        SELECT interaction_type, COUNT(*)
        FROM interactions GROUP BY interaction_type ORDER BY interaction_type
    """)
    print("\n  --- Interaction edge types ---")
    for itype, cnt in cursor.fetchall():
        print(f"    {itype:20s}: {cnt:,}")

    # Data quality
    print("\n  --- Data quality ---")
    cursor.execute("SELECT COUNT(*) FROM questions WHERE is_closed = 1")
    closed = cursor.fetchone()[0]
    if total_q:
        print(f"  Closed questions:      {closed:,} / {total_q:,} ({closed/total_q*100:.1f}%)")

    cursor.execute("SELECT COUNT(*) FROM comments WHERE reply_to_user_id IS NOT NULL")
    with_reply = cursor.fetchone()[0]
    total_c = cursor.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    if total_c:
        print(f"  Comments w/ reply_to:  {with_reply:,} / {total_c:,} ({with_reply/total_c*100:.1f}%)")

    # Traceability check: every Q/A/C should have at least one one-hot flag set
    flag_sum = " + ".join(ONEHOT_COLS)
    a_orphan = cursor.execute(f"SELECT COUNT(*) FROM answers WHERE ({flag_sum}) = 0").fetchone()[0]
    c_orphan = cursor.execute(f"SELECT COUNT(*) FROM comments WHERE ({flag_sum}) = 0").fetchone()[0]
    cq_null = cursor.execute("SELECT COUNT(*) FROM comments WHERE question_id IS NULL").fetchone()[0]
    print(f"  Answers with no framework flag: {a_orphan}")
    print(f"  Comments with no framework flag: {c_orphan}")
    print(f"  Comments missing question_id: {cq_null}")

    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("Starting Stack Overflow data collection")
    log.info("Tags: %s", TAGS)
    log.info("Cap: %d questions per tag per year × %d years",
             MAX_QUESTIONS_PER_TAG_PER_YEAR, len(YEARLY_PERIODS))
    log.info("Date range: %s ~ %s",
             DATE_FROM.strftime("%Y-%m-%d"), DATE_TO.strftime("%Y-%m-%d"))
    log.info("Database: %s", DB_PATH)

    conn = init_db(DB_PATH)

    try:
        for tag in TAGS:
            question_ids = collect_questions(conn, tag)
            if not question_ids:
                log.warning("No questions found for [%s], skipping.", tag)
                continue

            answers = collect_answers(conn, question_ids, tag)
            collect_comments(conn, question_ids, answers, tag)

        build_interactions(conn)
        collect_users(conn)
        print_summary(conn)

    finally:
        conn.close()

    log.info("Data collection complete! Database saved to: %s", DB_PATH)


if __name__ == "__main__":
    main()
