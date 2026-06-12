"""Real Module C resolver (2b): free-text query → canonical group, via OpenAI RAG + LLM gate.

Drop-in QueryResolver replacing canonical.resolve.KnownChainResolver: it resolves ARBITRARY text,
not just curated demo queries. Pipeline = embed query → cosine top-K over the prebuilt index → LLM
equivalence gate (concurrent batches) → persist canonical_group → return the group_id.

Persistence: every gated candidate is written to canonical_group with its retrieval rank/score and
gate_decision ('equivalent' | 'borderline' | 'not_equivalent') + confidence — full provenance of
what was considered. The group's MEMBERS are the 'equivalent' rows (load_group_answers filters on
that, treating legacy NULL rows from the proxy seed as members too). group_id = f"q{medoid}", where
medoid = the member most central to the equivalent set (ADR 0010 — set-stable, not the phrasing-
dependent top-cosine anchor). Returns None when nothing gates equivalent — that honest None is
exactly what the proxy could never produce for free text.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import config
from canonical.embed import embed_query
from canonical.gate import gate_candidates
from canonical.index import INDEX_DIR, medoid_of, search

DB = config.DB_PATH


class OpenAIRagResolver:
    """QueryResolver (resolve half): RAG retrieval + LLM equivalence gate over the react corpus."""

    source_name = "openai_rag_gate"

    def __init__(
        self,
        db_path: str | Path = DB,
        *,
        index_dir: str | Path = INDEX_DIR,
        top_k: int = 100,          # candidate depth the gate examines — the real recall dial (cheap to raise)
        threshold: float = 0.30,   # off-domain None-guard ONLY: skip the gate when even the top match is unrelated.
                                   #   NOT a relevance cut (for in-domain queries every top_k hit clears it by a mile);
                                   #   top_k does the selecting, the gate does precision. Raising it risks valid groups.
        accept_conf: float = 0.50,  # gate confidence at/above which 'equivalent' counts as a member
        dedup_threshold: float = 0.50,  # ADR 0010: reuse at Jaccard >= this. CONSERVATIVE — live-validated
                                        #   that genuine paraphrases reach only ~0.17 overlap (retrieval is
                                        #   phrasing-sensitive), so effective dedup needs multi-query expansion
                                        #   first; 0.5 merges only near-identical sets (safe, rarely fires).
    ):
        self.db_path = Path(db_path)
        self.index_dir = Path(index_dir)
        self.top_k = top_k
        self.threshold = threshold
        self.accept_conf = accept_conf
        self.dedup_threshold = dedup_threshold

    def resolve(self, query_text: str) -> str | None:
        q = (query_text or "").strip()
        if not q:
            return None

        hits = search(embed_query(q), index_dir=self.index_dir,
                      top_k=self.top_k, threshold=self.threshold)
        if not hits:
            return None

        texts = self._fetch_texts([h[0] for h in hits])
        candidates = [(qid, score, rank, *texts.get(qid, ("", ""))) for qid, score, rank in hits]
        gated = gate_candidates(q, candidates)

        rows: list[tuple] = []          # canonical_group rows (all candidates, full provenance)
        members: list[tuple[int, float]] = []  # (qid, score) for 'equivalent' rows → group membership
        for g in gated:
            if g.equivalent and g.confidence >= self.accept_conf:
                decision = "equivalent"
                members.append((g.question_id, g.score))
            elif g.equivalent:
                decision = "borderline"  # equivalent but low-confidence — recorded, NOT a member (voting TODO)
            else:
                decision = "not_equivalent"
            rows.append((g.question_id, g.rank, g.score, decision, g.confidence))

        if not members:
            return None

        member_qids = [qid for qid, _ in members]

        # ADR 0010: "same problem" is a SET question, not a representative one (semantic equivalence
        # is non-transitive). Reuse an already-resolved group whose member set overlaps this one by
        # Jaccard >= threshold (blocked by shared members) → cache hit, no new group, no re-bake.
        existing = self._overlapping_group(set(member_qids))
        if existing:
            return existing

        # New group. Representative = MEDOID of the equivalent set (most central member), not the
        # top-cosine anchor — the anchor is a query-phrasing artifact (ADR 0010); medoid is set-stable.
        rep = medoid_of(member_qids) or max(members, key=lambda m: m[1])[0]
        group_id = f"q{rep}"
        self._write_group(group_id, rows)
        return group_id

    # ── internals ─────────────────────────────────────────────────────────────────────────────────

    def _overlapping_group(self, sprime: set[int]) -> str | None:
        """ADR 0010 cache-hit / dedup: return an existing group whose member set overlaps *sprime*
        by Jaccard >= dedup_threshold, else None. Blocking: only groups sharing >=1 member are
        scored. Compares the FULL sets (not a single representative) — equivalence is non-transitive,
        so a representative-link test would mis-merge. Self-overlap (re-resolving a query whose group
        already exists) is Jaccard ~1.0 → returns that same group, making re-resolve idempotent."""
        if not sprime:
            return None
        con = sqlite3.connect(self.db_path)
        try:
            marks = ",".join("?" * len(sprime))
            cand = {r[0] for r in con.execute(
                f"SELECT DISTINCT group_id FROM canonical_group WHERE question_id IN ({marks}) "
                f"AND (gate_decision IS NULL OR gate_decision = 'equivalent')", tuple(sprime))}
            if not cand:
                return None
            gmarks = ",".join("?" * len(cand))
            sets: dict[str, set[int]] = {}
            for gid, qid in con.execute(
                f"SELECT group_id, question_id FROM canonical_group WHERE group_id IN ({gmarks}) "
                f"AND (gate_decision IS NULL OR gate_decision = 'equivalent')", tuple(cand)):
                sets.setdefault(gid, set()).add(qid)
        finally:
            con.close()
        best, best_j = None, 0.0
        for gid, sg in sets.items():
            j = len(sprime & sg) / len(sprime | sg)
            if j > best_j:
                best, best_j = gid, j
        return best if best_j >= self.dedup_threshold else None

    def _fetch_texts(self, qids: list[int]) -> dict[int, tuple[str, str]]:
        if not qids:
            return {}
        marks = ",".join("?" * len(qids))
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute(
                f"SELECT question_id, COALESCE(title, ''), COALESCE(body_text, '') "
                f"FROM questions WHERE question_id IN ({marks})", qids).fetchall()
        finally:
            con.close()
        return {int(qid): (title, body) for qid, title, body in rows}

    def _write_group(self, group_id: str, rows: list[tuple]) -> None:
        """Replace this group's canonical_group rows (idempotent re-resolve)."""
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("DELETE FROM canonical_group WHERE group_id = ?", (group_id,))
            con.executemany(
                "INSERT INTO canonical_group (group_id, question_id, retrieval_rank, retrieval_score, "
                "gate_decision, gate_confidence, gate_voting_agreement) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                [(group_id, qid, rank, score, decision, conf) for qid, rank, score, decision, conf in rows])
            con.commit()
        finally:
            con.close()
