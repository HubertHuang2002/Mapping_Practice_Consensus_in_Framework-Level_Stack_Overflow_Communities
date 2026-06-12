"""Normalized D-side tables — the source of truth behind query_viz_cache (ADR 0009 §3.2).

These are the GROUP-keyed (post-pivot, ADR 0009 Amendment 2026-06-03) facts the bake spine
reads and writes: stage ① resolve → canonical_group, ② extract → practice_extractions,
③ gate / ④ cluster → query_practice + practice_clusters, ⑤ aggregate_signals → camp_signal +
query_signal, ⑥ narrative → query_narrative. cache.py's query_viz_cache (stage ⑦) is the DERIVED view over
these; layout/pack runs once at bake time so serve stays warm.

Measurement unit = canonical GROUP, not the query wording. group-dependent tables key by
group_id; practice_extractions is the one GROUP-INDEPENDENT cache (a practice is a pure function
of an answer body → extract once, reuse across every group that pools that answer). Table names
query_practice / query_narrative are kept for historical continuity; their actual key is group_id.

DDL only — insert/read helpers land with the driver (step 4) and backfill (step 3), once the exact
write shapes are pinned. Foreign-key REFERENCES clauses mirror the spec as intent; we do NOT enable
PRAGMA foreign_keys (canonical_group.group_id is part of a composite PK, not unique on its own, so
it can't be FK-enforced as written — matches the rest of the codebase, which keeps FKs off).
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
-- 薄 submission log：一次 query 提交記其措辭 + resolve 到的 group（NULL = 還在 bake）。
-- 挑報告時從此表 + warm 集合挑（stratum / in_final_set 延後 → 屆時加 in_report flag）。
CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY,
    query_text   TEXT,
    group_id     TEXT REFERENCES canonical_group(group_id),
    submitted_at TEXT
);

-- 一個 canonical group = 一組「實際同題」的 SO questions（stage ① resolve 寫入）。
-- group_id = 錨點 canonical question id（proxy）或成員集合 hash（真 Module C）。
CREATE TABLE IF NOT EXISTS canonical_group (
    group_id              TEXT,
    question_id           INTEGER REFERENCES posts(id),
    retrieval_rank        INTEGER,
    retrieval_score       REAL,
    gate_decision         TEXT,    -- 'equivalent' / 'not_equivalent' / 'borderline'
    gate_confidence       REAL,
    gate_voting_agreement REAL,    -- k=3 majority margin
    PRIMARY KEY (group_id, question_id)
);

-- GROUP-INDEPENDENT 快取：一 practice 一列、按 answer keying。practice 是 answer body 的純
-- 函數 → 抽一次、所有 pool 到該 answer 的 group 重用（lazy：第一次被 pool 才抽；ADR 0007 multi-practice）。
CREATE TABLE IF NOT EXISTS practice_extractions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id             INTEGER REFERENCES answers(id),
    practice_rank         INTEGER,  -- 答案內第幾個 practice（一答 0..N 解）
    practice_sentence     TEXT,
    conditions            TEXT,     -- JSON list（0–3；不進 cluster boundary）
    evidence_type         TEXT,     -- 'prose' | 'code' | 'both'
    extract_model_version TEXT,
    extract_prompt_version TEXT,
    UNIQUE(answer_id, practice_rank)
);

CREATE TABLE IF NOT EXISTS practice_clusters (      -- 群「實體」，per GROUP
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id                 TEXT REFERENCES canonical_group(group_id),
    cluster_name             TEXT,
    cluster_description      TEXT,
    aggregator_model_version TEXT,
    voting_agreement         REAL   -- Step-2 k=3 co-association consensus（ADR 0007）
);

-- GROUP-DEPENDENT 事實，一 (group, practice) 一列：D-1b relevance gate（存 FLAG 不刪 →
-- 「hard-drop vs 顯示被濾掉」變呈現層 toggle）＋ 群歸屬（gate 掉者 practice_cluster_id = NULL）。
CREATE TABLE IF NOT EXISTS query_practice (         -- 名稱沿用；key = (group_id, extraction_id)
    group_id            TEXT REFERENCES canonical_group(group_id),
    extraction_id       INTEGER REFERENCES practice_extractions(id),
    relevant            INTEGER,  -- 0/1 D-1b：跑題到別的 React 子系統？
    substantive         INTEGER,  -- 0/1 D-1b：content-free placeholder / 個案 one-off？
    gate_model_version  TEXT,
    gate_prompt_version TEXT,
    practice_cluster_id INTEGER REFERENCES practice_clusters(id),  -- NULL = 被 gate 掉
    companion_label     INTEGER,  -- matched-k agglomerative companion（ADR 0007）
    PRIMARY KEY (group_id, extraction_id)
);

-- ⑤ aggregate_signals (signal-table v2): the SINGLE SOURCE read by narrator AND dashboard.
-- Supersedes the legacy `cluster_aggregations` (dead DDL, never written; existing DBs may keep an
-- empty copy — harmless). New names so a fresh CREATE applies cleanly (IF NOT EXISTS would NOT have
-- migrated the old table's columns). Per signal-table-v2.md §2. Votes = consensus axis; authority =
-- separate (un-fused) structure axis; prevalence = unweighted mention count.
CREATE TABLE IF NOT EXISTS camp_signal (            -- per (group, head cluster)
    group_id              TEXT REFERENCES canonical_group(group_id),
    cluster_id            INTEGER REFERENCES practice_clusters(id),
    cluster_name          TEXT,
    exemplar              TEXT,     -- highest-vote answer's practice sentence (grounding)
    prevalence_n          INTEGER,  -- distinct answers proposing this camp (unweighted)
    prevalence_share      REAL,
    vote_sum              REAL,     -- Σ distinct-answer votes  → SIGNAL axis (RQ-1)
    vote_share            REAL,
    -- RQ-2 authority = RAW-PageRank OVERLAY (any percentile-then-sum collapses to prevalence; only raw
    -- carries the hub signal — signal-table-v2.md §9). React-group authority is concentrated in 1–2
    -- hubs, so this reports "which central voices back this camp", concentration made explicit. Each
    -- author is attributed once to a PRIMARY camp (their highest-vote answer's camp).
    author_pr_share       REAL,     -- this camp's authors' share of the GROUP's total raw PageRank
    top_author            TEXT,     -- display name of the camp's most-central author
    top_author_pr_share   REAL,     -- that author's share of group raw PR (camp carried by one person?)
    n_top3_authors        INTEGER,  -- how many of the group's top-3 central authors sit in this camp
    authority_coverage    REAL,     -- fraction of camp answers with a known (in-graph) author
    voting_agreement      REAL,     -- copied from practice_clusters (partition confidence)
    PRIMARY KEY (group_id, cluster_id)
);

CREATE TABLE IF NOT EXISTS query_signal (           -- per group; RQ-1 shape + RQ-2 authority overlay
    group_id                   TEXT PRIMARY KEY REFERENCES canonical_group(group_id),
    n_answers                  INTEGER,
    n_practices                INTEGER,
    n_head_camps               INTEGER,
    n_longtail                 INTEGER,
    longtail_vote_share        REAL,
    -- RQ-1 shape (over vote-mass)
    effective_camps            REAL,    -- 1/Σ(vote_share²) Laakso-Taagepera; continuous gradient
    top1_share                 REAL,
    top2_share                 REAL,
    gap                        REAL,    -- top1 − top2
    shape_label                TEXT,    -- consensus | polarization | fragmentation
    shape_fragile              INTEGER, -- 0/1: merge-top2 flips label, or mean(agreement)<0.6
    vote_leader_cluster        INTEGER,
    -- RQ-2 authority OVERLAY (raw-PageRank; concentration-respecting — see camp_signal note)
    top1_author                TEXT,    -- group's #1 most-central answerer (display name)
    top1_pr_share              REAL,    -- their share of group raw PR = CONCENTRATION (gates framing)
    top3_pr_share              REAL,    -- top-3 authors' combined share (oligarchy indicator)
    single_voice_dominated     INTEGER, -- 0/1: top1_pr_share > 0.50 → use singular "one voice" framing
    top1_author_cluster        INTEGER, -- camp the #1 author backs (their primary camp)
    authority_diverges         INTEGER, -- 0/1: top1_author_cluster != vote_leader_cluster (the twist)
    top3_in_vote_leader        INTEGER, -- count of top-3 central authors in the vote-leader camp (0..3)
    authority_coverage_overall REAL,    -- fraction of answers with a known author (RQ-2 gate)
    -- prevalence (conditional)
    prevalence_leader_cluster  INTEGER,
    prevalence_diverges        INTEGER, -- 0/1: prevalence-leader != vote-leader
    computed_at                TEXT
);

CREATE TABLE IF NOT EXISTS query_narrative (        -- 名稱沿用；key = group_id
    group_id                TEXT PRIMARY KEY REFERENCES canonical_group(group_id),
    narrative_json          TEXT,  -- D-4 QueryNarrative dict（shape/dominant/authority_alignment/...）
    narrative_model_version TEXT,
    generation_timestamp    TEXT
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the normalized D-side tables (idempotent). Does NOT touch query_viz_cache —
    that derived table is owned by cache.py (init_cache)."""
    conn.executescript(SCHEMA)
    conn.commit()
