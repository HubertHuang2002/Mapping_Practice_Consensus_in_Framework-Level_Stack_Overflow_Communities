"""
Module A — Handoff Audit (React)
================================
Read-only audit confirming the collected dataset is ready for downstream analysis.

Defaults to test_output/test_sample.db; pass --db /path/to/so_data.db for prod.
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from config import TAGS, ONEHOT_COLS

DEFAULT_DB = Path(__file__).parent / "test_output" / "test_sample.db"

# Tolerance for "dirty" body_text/title rows (literal user-typed entities).
DIRTY_TOLERANCE_PCT = 0.1

_HTML_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#\d+);")
_DIRTY_PATTERNS = [_HTML_ENTITY]


def section(title: str):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def check(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    extra = f"  ({detail})" if detail else ""
    print(f"  [{status}] {name}{extra}")
    return ok


def find_dirty(text: str) -> list[str]:
    if not text:
        return []
    found = []
    for pat in _DIRTY_PATTERNS:
        m = pat.search(text)
        if m:
            found.append(m.group(0))
    return found


def short(s, n=140):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 3] + "..."


# ---------------------------------------------------------------------------

def audit_schema(conn) -> bool:
    section("1. Schema Completeness")
    cur = conn.cursor()
    onehot = set(ONEHOT_COLS)
    expected = {
        "questions": {
            "question_id", "title", "body", "body_text", "tags", "score",
            "view_count", "answer_count", "creation_date", "owner_user_id",
            "owner_display_name", "link", "is_closed", "closed_reason",
        } | onehot,
        "answers": {
            "answer_id", "question_id", "body", "body_text", "score",
            "is_accepted", "creation_date", "owner_user_id",
            "owner_display_name",
        } | onehot,
        "comments": {
            "comment_id", "post_id", "post_type", "question_id", "body",
            "body_text", "score", "creation_date", "owner_user_id",
            "owner_display_name", "reply_to_user_id",
        } | onehot,
        "users": {
            "user_id", "display_name", "reputation", "badge_gold",
            "badge_silver", "badge_bronze", "creation_date", "link",
        },
        "interactions": {
            "id", "source_user_id", "target_user_id", "interaction_type",
            "post_id", "parent_post_id", "creation_date",
        } | onehot,
    }
    all_ok = True
    for table, expected_cols in expected.items():
        try:
            cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            check(f"Table `{table}` exists", False, "table missing")
            all_ok = False
            continue
        missing = expected_cols - cols
        ok = not missing
        check(f"Table `{table}` has expected columns", ok,
              "missing: " + ", ".join(sorted(missing)) if missing else "")
        all_ok = all_ok and ok
    return all_ok


def audit_volumes(conn) -> dict:
    section("2. Volumes")
    cur = conn.cursor()
    counts = {}
    for table in ["questions", "answers", "comments", "users", "interactions"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        counts[table] = n
        print(f"  {table:14s} {n:>10,}")

    print()
    print(f"  {'framework':14s} | {'Q':>8s} | {'A':>8s} | {'C':>8s} | {'edges':>8s}")
    print("  " + "-" * 55)
    for tag, col in zip(TAGS, ONEHOT_COLS):
        q = cur.execute(f"SELECT COUNT(*) FROM questions    WHERE {col}=1").fetchone()[0]
        a = cur.execute(f"SELECT COUNT(*) FROM answers      WHERE {col}=1").fetchone()[0]
        c = cur.execute(f"SELECT COUNT(*) FROM comments     WHERE {col}=1").fetchone()[0]
        e = cur.execute(f"SELECT COUNT(*) FROM interactions WHERE {col}=1").fetchone()[0]
        print(f"  {tag:14s} | {q:>8,} | {a:>8,} | {c:>8,} | {e:>8,}")

    return counts


def audit_body_text(conn) -> bool:
    section("3. body_text Quality")
    cur = conn.cursor()
    all_ok = True

    for table, id_col in [("questions", "question_id"),
                          ("answers", "answer_id"),
                          ("comments", "comment_id")]:
        n_total = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        n_empty = cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE body_text IS NULL OR body_text = ''"
        ).fetchone()[0]
        ok = n_empty == 0
        all_ok = all_ok and ok
        check(f"{table}.body_text non-empty", ok, f"{n_empty}/{n_total} empty")

        dirty_count = 0
        dirty_examples = []
        rows = cur.execute(f"SELECT {id_col}, body_text FROM {table}").fetchall()
        for rid, txt in rows:
            d = find_dirty(txt)
            if d:
                dirty_count += 1
                if len(dirty_examples) < 3:
                    dirty_examples.append((rid, d, txt))
        dirty_pct = (dirty_count / n_total * 100) if n_total else 0.0
        ok = dirty_pct <= DIRTY_TOLERANCE_PCT
        all_ok = all_ok and ok
        check(
            f"{table}.body_text — entity-clean rate (≤{DIRTY_TOLERANCE_PCT}% dirty)",
            ok,
            f"{dirty_count}/{n_total} dirty ({dirty_pct:.4f}%)",
        )
        for rid, d, txt in dirty_examples:
            print(f"      example {table}.{id_col}={rid} dirty fragments: {d}")
            print(f"        snippet: {short(txt, 120)}")

    print()
    print("  Sample question body_text (first 200 chars of 5 rows):")
    rows = cur.execute(
        f"SELECT question_id, {', '.join(ONEHOT_COLS)}, body_text "
        f"FROM questions LIMIT 5"
    ).fetchall()
    for r in rows:
        qid, txt = r[0], r[-1]
        flags = r[1:-1]
        labels = ",".join(t for t, f in zip(TAGS, flags) if f) or "none"
        print(f"  Q{qid} [{labels}]")
        print(f"    {short(txt, 200)}")

    return all_ok


def audit_titles(conn) -> bool:
    section("4. Title Cleanliness")
    cur = conn.cursor()
    all_ok = True

    n_total = cur.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    rows = cur.execute("SELECT question_id, title FROM questions").fetchall()
    dirty = []
    for qid, t in rows:
        d = find_dirty(t)
        if d:
            dirty.append((qid, d, t))
    dirty_pct = (len(dirty) / n_total * 100) if n_total else 0.0
    ok = dirty_pct <= DIRTY_TOLERANCE_PCT
    all_ok = all_ok and ok
    check(
        f"questions.title — entity-clean rate (≤{DIRTY_TOLERANCE_PCT}% dirty)",
        ok,
        f"{len(dirty)}/{n_total} dirty ({dirty_pct:.4f}%)",
    )
    for qid, d, t in dirty[:3]:
        print(f"      Q{qid} dirty fragments: {d}")
        print(f"        title: {t}")

    print()
    print("  Sample titles:")
    rows = cur.execute(
        f"SELECT question_id, {', '.join(ONEHOT_COLS)}, title "
        f"FROM questions ORDER BY RANDOM() LIMIT 5"
    ).fetchall()
    for r in rows:
        qid, t = r[0], r[-1]
        flags = r[1:-1]
        labels = ",".join(tg for tg, f in zip(TAGS, flags) if f) or "none"
        print(f"    [{labels:12s}] Q{qid}: {t}")

    return all_ok


def audit_traceability(conn) -> bool:
    section("5. ID & Tag Traceability")
    cur = conn.cursor()
    all_ok = True

    flag_sum = " + ".join(ONEHOT_COLS)
    for tbl in ["questions", "answers", "comments"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE ({flag_sum}) = 0").fetchone()[0]
        ok = n == 0
        all_ok = all_ok and ok
        check(f"{tbl}: every row has at least one framework flag", ok, f"{n} unflagged")

    n = cur.execute("SELECT COUNT(*) FROM comments WHERE question_id IS NULL").fetchone()[0]
    ok = n == 0
    all_ok = all_ok and ok
    check("comments: question_id never NULL", ok, f"{n} orphans")

    n = cur.execute("""
        SELECT COUNT(*) FROM answers a
        WHERE NOT EXISTS (SELECT 1 FROM questions q WHERE q.question_id = a.question_id)
    """).fetchone()[0]
    ok = n == 0
    all_ok = all_ok and ok
    check("answers: question_id refers to existing question", ok, f"{n} dangling")

    mismatch_clauses = " OR ".join(f"a.{c} != q.{c}" for c in ONEHOT_COLS)
    n = cur.execute(f"""
        SELECT COUNT(*) FROM answers a
        JOIN questions q ON a.question_id = q.question_id
        WHERE {mismatch_clauses}
    """).fetchone()[0]
    ok = n == 0
    all_ok = all_ok and ok
    check("answers' one-hot flags inherit from parent question", ok, f"{n} mismatched")

    for tbl, col in [("questions", "owner_user_id"),
                     ("answers", "owner_user_id"),
                     ("comments", "owner_user_id")]:
        n = cur.execute(f"""
            SELECT COUNT(DISTINCT {col}) FROM {tbl}
            WHERE {col} IS NOT NULL
              AND {col} NOT IN (SELECT user_id FROM users)
        """).fetchone()[0]
        ok = n == 0
        all_ok = all_ok and ok
        check(f"{tbl}.{col}: all in users table", ok, f"{n} missing profiles")

    return all_ok


def audit_downstream_sample(conn) -> bool:
    section("6. Downstream Input Sample (top-voted questions)")
    cur = conn.cursor()
    rows = cur.execute(f"""
        SELECT q.question_id, q.title, q.body_text, q.score,
               q.owner_user_id, q.tags, {', '.join('q.' + c for c in ONEHOT_COLS)}
        FROM questions q
        ORDER BY q.score DESC LIMIT 5
    """).fetchall()

    if not rows:
        check("Downstream sample available", False, "no rows in DB")
        return False
    check("Downstream sample available", True, f"{len(rows)} rows")

    for r in rows:
        qid, title, body, score, uid, tags_json = r[:6]
        flags = r[6:]
        labels = ",".join(t for t, f in zip(TAGS, flags) if f) or "none"
        try:
            tags = json.loads(tags_json) if tags_json else []
        except json.JSONDecodeError:
            tags = []
        print()
        print(f"  Q{qid}  frameworks={labels}  score={score}  user={uid}")
        print(f"    title: {title}")
        print(f"    tags : {tags}")
        sents = re.split(r"(?<=[.!?])\s+", body or "")
        sents = [s for s in sents if s.strip()][:2]
        for i, s in enumerate(sents, 1):
            print(f"    sent{i}: {short(s, 200)}")

    return True


def audit_data_sufficiency(conn, counts: dict) -> bool:
    section("7. Data Sufficiency")

    is_test = "test_sample" in str(conn.execute("PRAGMA database_list").fetchone()[2])
    n_q = counts["questions"]

    if is_test:
        # Approximate SO React supply 2023-01 ~ 2026-04 ≈ 61k questions
        scale = 61000 / max(n_q, 1)
        print(f"  (test sample — extrapolating × {scale:.0f} to estimate full dataset)")
        for k, n in counts.items():
            print(f"    {k:14s} {int(n * scale):>10,}  (extrapolated)")
    else:
        for k, n in counts.items():
            print(f"  {k:14s} {n:>10,}")

    return True


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB to audit")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Auditing: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        ok_schema = audit_schema(conn)
        counts = audit_volumes(conn)
        ok_body = audit_body_text(conn)
        ok_title = audit_titles(conn)
        ok_trace = audit_traceability(conn)
        ok_sample = audit_downstream_sample(conn)
        audit_data_sufficiency(conn, counts)
    finally:
        conn.close()

    section("FINAL VERDICT")
    sections = {
        "schema": ok_schema,
        "body_text quality": ok_body,
        "title cleanliness": ok_title,
        "traceability": ok_trace,
        "downstream sample": ok_sample,
    }
    for name, ok in sections.items():
        check(name, ok)
    if all(sections.values()):
        print("\n  Audit: READY")
    else:
        print("\n  Audit: NEEDS FIXES")
        sys.exit(1)


if __name__ == "__main__":
    main()
