"""
Module A — Data Collection Integration Test (React)
===================================================
Small-sample end-to-end validation of the collection pipeline:

  Part A: Volume estimation (per sub-period)
  Part B: Collect a small sample into a test SQLite DB
  Part C: Build interactions and verify all edge types
  Part D: Verify downstream-analysis readiness
  Part E: Mini graph analysis simulation
  Part F: Sample thread visualization
"""

import requests
import json
import time
from html import unescape
from pathlib import Path

from config import (
    API_BASE, API_KEY, TAGS, MONTHS,
    MAX_QUESTIONS_PER_TAG_PER_YEAR,
    TAG_COLUMNS, ONEHOT_COLS,
)
from collect_data import (
    clean_html, YEARLY_PERIODS, init_db, build_interactions,
    compute_flags_from_tags,
)

OUTPUT_DIR = Path(__file__).parent / "test_output"
TEST_DB = OUTPUT_DIR / "test_sample.db"

SAMPLE_Q_PER_TAG = 50


def api_get(endpoint, params=None):
    if params is None:
        params = {}
    params.setdefault("site", "stackoverflow")
    if API_KEY:
        params["key"] = API_KEY
    resp = requests.get(f"{API_BASE}/{endpoint}", params=params, timeout=30)
    return resp.json()


# ---------------------------------------------------------------------------
# Part A: Volume estimation
# ---------------------------------------------------------------------------

def part_a_volume():
    cap = MAX_QUESTIONS_PER_TAG_PER_YEAR
    cap_label = "no cap" if cap == 0 else f"cap={cap}/tag/period"
    print("=" * 70)
    print(f"[Part A] Volume Estimation ({len(YEARLY_PERIODS)} periods × {len(TAGS)} tag, {cap_label})")
    print("=" * 70)

    print(f"\n  {'Period':>18s}", end="")
    for tag in TAGS:
        print(f"  {tag:>12s}", end="")
    print("    Total")
    print("  " + "-" * 50)

    grand = 0
    for p_start, p_end in YEARLY_PERIODS:
        ts_from = int(p_start.timestamp())
        ts_to = int(p_end.timestamp())
        label = f"{p_start.strftime('%Y-%m')}~{p_end.strftime('%Y-%m')}"
        print(f"  {label:>18s}", end="")
        row_total = 0
        for tag in TAGS:
            data = api_get("questions", {
                "tagged": tag, "fromdate": ts_from, "todate": ts_to,
                "filter": "total", "pagesize": 1,
            })
            raw = data.get("total", 0)
            capped = raw if cap == 0 else min(raw, cap)
            row_total += capped
            print(f"  {capped:>12,}", end="")
            time.sleep(0.25)
        grand += row_total
        print(f"  {row_total:>6,}")

    print("  " + "-" * 50)
    print(f"  GRAND TOTAL: {grand:,}")
    return grand


# ---------------------------------------------------------------------------
# Part B: Collect a small sample
# ---------------------------------------------------------------------------

def init_test_db():
    TEST_DB.parent.mkdir(exist_ok=True)
    if TEST_DB.exists():
        TEST_DB.unlink()
    return init_db(TEST_DB)


def _insert_question(conn, q, primary_tag):
    o = q.get("owner", {})
    body = q.get("body", "")
    title = unescape(q.get("title", ""))
    body_text = clean_html(body)
    q_tags = q.get("tags", [])
    flags = list(compute_flags_from_tags(q_tags))
    if primary_tag in TAGS:
        flags[TAGS.index(primary_tag)] = 1
    conn.execute(
        f"""INSERT OR IGNORE INTO questions
            (question_id, title, body, body_text, tags, score, view_count,
             answer_count, creation_date, owner_user_id, owner_display_name,
             link, is_closed, closed_reason, {', '.join(ONEHOT_COLS)})
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,{','.join('?' * len(ONEHOT_COLS))})""",
        (
            q["question_id"], title, body, body_text,
            json.dumps(q_tags), q.get("score", 0), q.get("view_count", 0),
            q.get("answer_count", 0), q.get("creation_date", 0),
            o.get("user_id"), o.get("display_name", ""),
            q.get("link", ""), 1 if q.get("closed_date") else 0,
            q.get("closed_reason", ""),
            *flags,
        ),
    )


def _q_flags(conn, qid):
    row = conn.execute(
        f"SELECT {', '.join(ONEHOT_COLS)} FROM questions WHERE question_id = ?",
        (qid,),
    ).fetchone()
    return row if row else (0,) * len(ONEHOT_COLS)


def collect_sample(conn, tag):
    q_data = api_get("search/advanced", {
        "tagged": tag,
        "fromdate": int(YEARLY_PERIODS[0][0].timestamp()),
        "todate": int(YEARLY_PERIODS[-1][1].timestamp()),
        "answers": 1, "sort": "activity", "order": "desc",
        "filter": "withbody", "pagesize": SAMPLE_Q_PER_TAG,
    })
    questions = q_data.get("items", [])

    for q in questions:
        _insert_question(conn, q, tag)

    qids = [q["question_id"] for q in questions]
    time.sleep(0.5)

    if not qids:
        return 0

    ids_str = ";".join(str(qid) for qid in qids)

    # Answers
    a_data = api_get(f"questions/{ids_str}/answers", {
        "filter": "withbody", "pagesize": 100,
    })
    answers = a_data.get("items", [])
    for a in answers:
        o = a.get("owner", {})
        body = a.get("body", "")
        flags = _q_flags(conn, a.get("question_id"))
        conn.execute(
            f"""INSERT OR IGNORE INTO answers
                (answer_id, question_id, body, body_text, score, is_accepted,
                 creation_date, owner_user_id, owner_display_name,
                 {', '.join(ONEHOT_COLS)})
                VALUES (?,?,?,?,?,?,?,?,?,{','.join('?' * len(ONEHOT_COLS))})""",
            (
                a["answer_id"], a.get("question_id"),
                body, clean_html(body), a.get("score", 0),
                1 if a.get("is_accepted") else 0, a.get("creation_date", 0),
                o.get("user_id"), o.get("display_name", ""),
                *flags,
            ),
        )
    time.sleep(0.5)

    # Comments on questions
    cq_data = api_get(f"posts/{ids_str}/comments", {
        "filter": "withbody", "pagesize": 100,
    })
    for c in cq_data.get("items", []):
        o = c.get("owner", {})
        rt = c.get("reply_to_user", {})
        pid = c.get("post_id")
        flags = _q_flags(conn, pid)
        conn.execute(
            f"""INSERT OR IGNORE INTO comments
                (comment_id, post_id, post_type, question_id, body, body_text,
                 score, creation_date, owner_user_id, owner_display_name,
                 reply_to_user_id, {', '.join(ONEHOT_COLS)})
                VALUES (?,?,?,?,?,?,?,?,?,?,?,{','.join('?' * len(ONEHOT_COLS))})""",
            (
                c["comment_id"], pid, "question", pid,
                c.get("body", ""), clean_html(c.get("body", "")),
                c.get("score", 0), c.get("creation_date", 0),
                o.get("user_id"), o.get("display_name", ""),
                rt.get("user_id"), *flags,
            ),
        )
    time.sleep(0.5)

    # Comments on answers
    aids = [a["answer_id"] for a in answers]
    if aids:
        aids_str = ";".join(str(aid) for aid in aids[:100])
        ca_data = api_get(f"posts/{aids_str}/comments", {
            "filter": "withbody", "pagesize": 100,
        })
        a2q = {a["answer_id"]: a.get("question_id") for a in answers}
        for c in ca_data.get("items", []):
            o = c.get("owner", {})
            rt = c.get("reply_to_user", {})
            pid = c.get("post_id")
            root_qid = a2q.get(pid)
            flags = _q_flags(conn, root_qid) if root_qid else (0,) * len(ONEHOT_COLS)
            conn.execute(
                f"""INSERT OR IGNORE INTO comments
                    (comment_id, post_id, post_type, question_id, body, body_text,
                     score, creation_date, owner_user_id, owner_display_name,
                     reply_to_user_id, {', '.join(ONEHOT_COLS)})
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,{','.join('?' * len(ONEHOT_COLS))})""",
                (
                    c["comment_id"], pid, "answer", root_qid,
                    c.get("body", ""), clean_html(c.get("body", "")),
                    c.get("score", 0), c.get("creation_date", 0),
                    o.get("user_id"), o.get("display_name", ""),
                    rt.get("user_id"), *flags,
                ),
            )
        time.sleep(0.5)

    conn.commit()
    return len(questions)


def collect_users(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT user_id FROM (
            SELECT owner_user_id AS user_id FROM questions WHERE owner_user_id IS NOT NULL
            UNION
            SELECT owner_user_id AS user_id FROM answers   WHERE owner_user_id IS NOT NULL
            UNION
            SELECT owner_user_id AS user_id FROM comments  WHERE owner_user_id IS NOT NULL
            UNION
            SELECT reply_to_user_id AS user_id FROM comments WHERE reply_to_user_id IS NOT NULL
        )
        WHERE user_id NOT IN (SELECT user_id FROM users)
    """)
    user_ids = [row[0] for row in cursor.fetchall()]
    if not user_ids:
        return 0

    batch_size = 100
    collected = 0
    for i in range(0, len(user_ids), batch_size):
        batch = user_ids[i:i + batch_size]
        ids_str = ";".join(str(uid) for uid in batch)
        data = api_get(f"users/{ids_str}", {
            "order": "desc", "sort": "reputation", "pagesize": 100,
        })
        rows = []
        for u in data.get("items", []):
            badges = u.get("badge_counts", {})
            rows.append((
                u["user_id"], u.get("display_name", ""),
                u.get("reputation", 0),
                badges.get("gold", 0), badges.get("silver", 0), badges.get("bronze", 0),
                u.get("creation_date", 0), u.get("link", ""),
            ))
        conn.executemany("""
            INSERT OR IGNORE INTO users
            (user_id, display_name, reputation, badge_gold, badge_silver,
             badge_bronze, creation_date, link)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        collected += len(rows)
        time.sleep(0.5)
    return collected


def part_b_collect(conn):
    print(f"\n{'='*70}")
    print("[Part B] Collect Small Sample into Test DB")
    print(f"{'='*70}")

    for tag in TAGS:
        collect_sample(conn, tag)
        col = TAG_COLUMNS[tag]
        q = conn.execute(f"SELECT COUNT(*) FROM questions WHERE {col}=1").fetchone()[0]
        a = conn.execute(f"SELECT COUNT(*) FROM answers   WHERE {col}=1").fetchone()[0]
        c = conn.execute(f"SELECT COUNT(*) FROM comments  WHERE {col}=1").fetchone()[0]
        print(f"  [{tag:13s}] questions={q}, answers={a}, comments={c}")

    n_users = collect_users(conn)
    print(f"  Users collected: {n_users}")


# ---------------------------------------------------------------------------
# Part C: Build interactions
# ---------------------------------------------------------------------------

def part_c_interactions(conn):
    print(f"\n{'='*70}")
    print("[Part C] Build Interactions & Verify Edge Types")
    print(f"{'='*70}")

    build_interactions(conn)

    cursor = conn.cursor()
    counts = dict(
        cursor.execute(
            "SELECT interaction_type, COUNT(*) FROM interactions GROUP BY interaction_type"
        ).fetchall()
    )
    e1 = counts.get("answer", 0)
    e2 = counts.get("comment_reply", 0)
    e3 = counts.get("comment", 0)
    total = e1 + e2 + e3
    print(f"  answer edges:              {e1}")
    print(f"  comment_reply edges:       {e2}")
    print(f"  comment fallback edges:    {e3}")
    print(f"  TOTAL edges:               {total}")

    print("\n  Sample edges:")
    rows = cursor.execute(
        f"SELECT source_user_id, target_user_id, interaction_type, "
        f"{', '.join(ONEHOT_COLS)} FROM interactions LIMIT 5"
    ).fetchall()
    for r in rows:
        src, tgt, itype = r[0], r[1], r[2]
        flags = r[3:]
        labels = ",".join(t for t, f in zip(TAGS, flags) if f)
        print(f"    user {src} --[{itype}]--> user {tgt}  ({labels or 'none'})")

    return total


# ---------------------------------------------------------------------------
# Part D: Downstream-readiness checks
# ---------------------------------------------------------------------------

def part_d_validation(conn):
    print(f"\n{'='*70}")
    print("[Part D] Downstream Analysis Readiness")
    print(f"{'='*70}")

    cursor = conn.cursor()
    checks = []
    flag_sum = " + ".join(ONEHOT_COLS)

    n = cursor.execute(f"SELECT COUNT(*) FROM questions WHERE ({flag_sum}) = 0").fetchone()[0]
    checks.append(("Questions: every row has framework flag", n == 0, f"{n} unflagged"))
    n = cursor.execute(f"SELECT COUNT(*) FROM answers   WHERE ({flag_sum}) = 0").fetchone()[0]
    checks.append(("Answers:   every row has framework flag", n == 0, f"{n} unflagged"))
    n = cursor.execute(f"SELECT COUNT(*) FROM comments  WHERE ({flag_sum}) = 0").fetchone()[0]
    checks.append(("Comments:  every row has framework flag", n == 0, f"{n} unflagged"))

    n = cursor.execute("SELECT COUNT(*) FROM comments WHERE question_id IS NULL").fetchone()[0]
    checks.append(("Comments: all have question_id", n == 0, f"{n} missing"))

    nq = cursor.execute("SELECT COUNT(*) FROM questions WHERE body_text IS NULL OR body_text = ''").fetchone()[0]
    na = cursor.execute("SELECT COUNT(*) FROM answers   WHERE body_text IS NULL OR body_text = ''").fetchone()[0]
    checks.append(("Questions: body_text populated", nq == 0, f"{nq} empty"))
    checks.append(("Answers:   body_text populated", na == 0, f"{na} empty"))

    n = cursor.execute(
        "SELECT COUNT(*) FROM interactions WHERE source_user_id IS NULL OR target_user_id IS NULL"
    ).fetchone()[0]
    checks.append(("Interactions: no NULL user IDs", n == 0, f"{n} invalid"))

    n = cursor.execute(
        "SELECT COUNT(*) FROM interactions WHERE source_user_id = target_user_id"
    ).fetchone()[0]
    checks.append(("Interactions: no self-loops", n == 0, f"{n} self-loops"))

    n_users = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    checks.append(("Users: table populated", n_users > 0, "0 users"))

    n_missing = cursor.execute("""
        SELECT COUNT(DISTINCT uid) FROM (
            SELECT source_user_id AS uid FROM interactions
            UNION
            SELECT target_user_id AS uid FROM interactions
        ) WHERE uid NOT IN (SELECT user_id FROM users)
    """).fetchone()[0]
    checks.append(("Users: all interaction users have profiles", n_missing == 0, f"{n_missing} missing"))

    n = cursor.execute(f"SELECT COUNT(*) FROM interactions WHERE ({flag_sum}) = 0").fetchone()[0]
    checks.append(("Interactions: every edge has framework flag", n == 0, f"{n} unflagged"))

    n = cursor.execute(r"""
        SELECT COUNT(*) FROM questions
        WHERE title LIKE '%&#%' OR title LIKE '%&amp;%' OR title LIKE '%&quot;%'
    """).fetchone()[0]
    checks.append(("Titles: HTML entities decoded", n == 0, f"{n} still encoded"))

    n_with_code = cursor.execute(
        "SELECT COUNT(*) FROM questions WHERE body_text LIKE '%[CODE]%'"
    ).fetchone()[0]
    checks.append((
        f"body_text: code-block stripping ran ({n_with_code} questions w/ [CODE])",
        n_with_code > 0,
        "no [CODE] tokens",
    ))

    import re as _re
    _entity = _re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#\d+);")
    rows = cursor.execute("SELECT question_id, body_text FROM questions").fetchall()
    dirty = sum(1 for _, txt in rows if txt and _entity.search(txt))
    checks.append(("body_text: no unresolved HTML entities", dirty == 0, f"{dirty} dirty"))

    all_pass = True
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        extra = "" if passed else f" ({detail})"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name}{extra}")

    return all_pass


# ---------------------------------------------------------------------------
# Part E: Mini graph analysis
# ---------------------------------------------------------------------------

def part_e_graph_sim(conn):
    print(f"\n{'='*70}")
    print("[Part E] Mini Graph Analysis Simulation")
    print(f"{'='*70}")

    cursor = conn.cursor()

    for tag in TAGS:
        col = TAG_COLUMNS[tag]
        edges = cursor.execute(
            f"SELECT source_user_id, target_user_id, interaction_type "
            f"FROM interactions WHERE {col} = 1"
        ).fetchall()

        if not edges:
            print(f"\n  [{tag}] No edges — skipped")
            continue

        nodes = set()
        edge_set = set()
        type_counts = {}
        for src, tgt, itype in edges:
            nodes.add(src)
            nodes.add(tgt)
            edge_set.add((src, tgt))
            type_counts[itype] = type_counts.get(itype, 0) + 1

        n_nodes = len(nodes)
        n_edges = len(edge_set)
        density = n_edges / (n_nodes * (n_nodes - 1)) if n_nodes > 1 else 0

        print(f"\n  [{tag}]")
        print(f"    Nodes (users):    {n_nodes}")
        print(f"    Edges (unique):   {n_edges}")
        print(f"    Density:          {density:.4f}")
        print(f"    Edge types:       {type_counts}")

        in_degree = {}
        for src, tgt in edge_set:
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

        top_in = sorted(in_degree.items(), key=lambda x: -x[1])[:3]
        print(f"    Top in-degree:    {[(uid, d) for uid, d in top_in]}")


# ---------------------------------------------------------------------------
# Part F: Sample thread
# ---------------------------------------------------------------------------

def part_f_sample_thread(conn):
    print(f"\n{'='*70}")
    print("[Part F] Sample Thread Visualization")
    print(f"{'='*70}")

    cursor = conn.cursor()
    base_select = (
        f"SELECT q.question_id, q.title, q.owner_user_id, q.owner_display_name, "
        f"{', '.join('q.' + c for c in ONEHOT_COLS)}"
    )
    row = cursor.execute(f"""
        {base_select}
        FROM questions q
        WHERE q.question_id IN (SELECT question_id FROM answers)
          AND q.question_id IN (SELECT question_id FROM comments WHERE post_type = 'question')
        LIMIT 1
    """).fetchone()

    if not row:
        row = cursor.execute(f"""
            {base_select}
            FROM questions q
            WHERE q.question_id IN (SELECT question_id FROM answers)
            LIMIT 1
        """).fetchone()

    if not row:
        print("  No suitable thread found in sample.")
        return

    qid, title, q_uid, q_name = row[:4]
    flags = row[4:]
    labels = ",".join(t for t, f in zip(TAGS, flags) if f) or "none"
    print(f"\n  Thread: Q{qid} [{labels}]")
    print(f"  Title: {title}")
    print(f"  Asker: {q_name} (uid={q_uid})")

    answers = cursor.execute("""
        SELECT answer_id, owner_user_id, owner_display_name, score, is_accepted,
               substr(body_text, 1, 100) as preview
        FROM answers WHERE question_id = ?
    """, (qid,)).fetchall()

    print(f"\n  Answers ({len(answers)}):")
    for aid, uid, name, score, acc, preview in answers:
        mark = " *" if acc else ""
        print(f"    A{aid} by {name} (uid={uid}) score={score}{mark}")
        print(f"      \"{preview}...\"")

    comments = cursor.execute("""
        SELECT comment_id, post_id, post_type, owner_user_id, owner_display_name,
               reply_to_user_id, substr(body_text, 1, 100) as preview
        FROM comments WHERE question_id = ?
    """, (qid,)).fetchall()

    print(f"\n  Comments ({len(comments)}):")
    for cid, pid, ptype, uid, name, rtu, preview in comments:
        target = f" -> reply_to uid={rtu}" if rtu else f" (on {ptype} {pid})"
        print(f"    C{cid} by {name} (uid={uid}){target}")
        print(f"      \"{preview}\"")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Config: {MONTHS} months × {TAGS}")
    cap = MAX_QUESTIONS_PER_TAG_PER_YEAR
    print(f"        {'no cap' if cap == 0 else f'cap {cap}/tag/period'} × {len(YEARLY_PERIODS)} periods")
    print(f"Date:   {YEARLY_PERIODS[0][0].strftime('%Y-%m-%d')}"
          f" ~ {YEARLY_PERIODS[-1][1].strftime('%Y-%m-%d')}")
    print(f"Sample: {SAMPLE_Q_PER_TAG} questions/tag\n")

    grand = part_a_volume()

    conn = init_test_db()
    try:
        part_b_collect(conn)
        total_edges = part_c_interactions(conn)
        all_pass = part_d_validation(conn)
        part_e_graph_sim(conn)
        part_f_sample_thread(conn)
    finally:
        conn.close()

    print(f"\n{'='*70}")
    print("FINAL VERDICT")
    print(f"{'='*70}")
    results = [
        ("Volume sanity (grand total > 0)", grand > 0),
        ("All edges built", total_edges > 0),
        ("All data checks passed", all_pass),
    ]
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    verdict = "ALL PASS — Ready to run collect_data.py" if all(ok for _, ok in results) else "NEEDS REVIEW"
    print(f"\n  {verdict}")
    print(f"  Test DB: {TEST_DB}")
    print(f"  Output:  {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
