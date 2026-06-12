"""Stage ⑥ — per-query narrative prompt (signal-table v2): DESCRIBE-ONLY.

The verdicts (RQ-1 shape, RQ-2 authority overlay) are ALREADY computed in Python and printed in the
SIGNAL TABLE. The model does NOT choose labels — it writes faithful prose (headline + body) over the
given numbers, never inventing a pattern or adding outside React knowledge.

RQ-1 (shape) leads, from VOTES. RQ-2 (authority) is a raw-PageRank OVERLAY: the named most-central
answerer(s) and which camp they back — reported with concentration (one super-contributor often
dominates) and two honesty rules: centrality ≠ correctness, and agreement with the crowd is partly
ENDOGENOUS (a hub is central because its answers were upvoted) so divergence is the informative case.
Time is intentionally omitted in this version. Few-shots are cross-topic so the prose generalizes.
"""
from __future__ import annotations

import json

_NARR_SYSTEM = """\
You are given a precomputed SIGNAL TABLE for ONE React implementation question. The verdicts are
ALREADY decided in the table (SHAPE line + AUTHORITY OVERLAY block); your job is to DESCRIBE them in
faithful prose — never invent a pattern not in the table, never add outside React knowledge, never
override a label. Output reasoning (CoT, not shown to users), then a headline and a body.

How to read and what to write:

1. SHAPE (RQ-1, from votes) — already labeled consensus / polarization / fragmentation. Lead the body
   with it: name the vote-leading approach and STATE its vote share; say whether one approach leads
   (consensus), two split the field (polarization), or it scatters (fragmentation). If the SHAPE line
   says FRAGILE, say the shape is borderline / not robust.

2. AUTHORITY OVERLAY (RQ-2, raw network centrality) — the OVERLAY block names the most-central
   answerer, the % of the question's network centrality they hold, and which camp they back. Rules:
   - It is a few named voices, NOT "the experts" or "the community". Say "the most-central answerer
     (NAME, X%)", never "experts agree".
   - If [ONE-VOICE-DOMINATED], frame it as a SINGLE influential individual and keep its weight modest.
   - If it DIVERGES from the vote-leader → this is the informative reveal; state it ("the crowd
     upvoted X, but the most-central answerer backs Y").
   - If it AGREES with the vote-leader → DISCOUNT it: note the agreement is partly endogenous (this
     answerer is central BECAUSE their answers get upvoted), so it is weak corroboration, not
     independent.
   - Always carry the caveat that network centrality is not topic correctness.
   - If authority coverage is low, say authority can't be read here.

3. Optionally note the long tail if it holds a notable share of votes.

Then:
- headline: ONE line, <= 14 words, capturing the vote-shape (optionally the divergence twist).
- body: 2-4 sentences. (1) the vote-shape + leader's share. (2) the authority overlay per the rules.
  (3) optional caveat (fragile shape / one-voice / low coverage). Be concrete; name approaches and the
  central author. Describe only what the table states.
- reasoning: think through the table first (not stored)."""

# FS1: consensus by votes, but the ONE dominant author backs a minority camp -> diverge + one-voice.
_NARR_FS1_INPUT = """\
QUERY: Why isn't my React state updated immediately after setState / useState?
SHAPE: consensus  —  vote-leader 〈useEffect after state change〉 at 49% (runner-up 14%); effective camps 3.6
TOTALS: 66 answers · 12 camps + 12 long-tail (long tail holds 18% of votes)

CAMPS (by vote share):
   vote%  prev%  authorPR%  agree  approach  [top author]
    49%    38%      12%     0.97  useEffect after state change  [Nick Vu]
    14%     9%      63%     0.93  functional updater from previous  [Drew Reese]  <- #1 central
     9%     6%       1%     1.00  cleanup intervals or sockets  [dave]
     6%    15%       4%     0.95  read state on next render  [Andy]

AUTHORITY OVERLAY (raw PageRank — concentrated; centrality != correctness):
  most-central answerer: Drew Reese — 63% of this question's network centrality [ONE-VOICE-DOMINATED]
  backs 〈functional updater from previous〉 -> DIVERGES from the vote-leader
  top-3 central authors in vote-leader camp: 0 of 3 · authority coverage: 98%
NOTE: if the central voice AGREES with the vote-leader, discount it (endogenous — central because upvoted)."""
_NARR_FS1_OUTPUT = {
    "reasoning": "Votes converge on useEffect-after-state-change (49%, ~3.5x the runner-up) -> consensus. "
                 "But the overlay shows one answerer, Drew Reese, holds 63% of centrality (one-voice-dominated) "
                 "and backs a minority camp (functional updater, only 14% of votes) -> a diverge worth stating, "
                 "hedged: one influential individual, centrality != correctness.",
    "headline": "Crowd settles on useEffect; the most-central voice quietly differs",
    "body": "By votes the question is largely settled: the useEffect-after-state-change approach leads with "
            "49% of the vote, roughly 3.5x its nearest rival. The authority picture diverges, though — a single "
            "dominant answerer, Drew Reese, who alone holds 63% of this question's network centrality, instead "
            "backs the minority \\u201cfunctional updater\\u201d approach (just 14% of votes). That is one "
            "influential individual rather than an expert consensus, and network centrality is not a claim about "
            "which approach is correct.",
}

# FS2: polarization; the top voices AGREE with the vote-leader -> apply the endogeneity discount.
_NARR_FS2_INPUT = """\
QUERY: How do I debounce a value/input in React?
SHAPE: polarization (FRAGILE)  —  vote-leader 〈Debounce the callback timing〉 at 47% (runner-up 32%); effective camps 3.0
TOTALS: 56 answers · 11 camps + 9 long-tail (long tail holds 9% of votes)

CAMPS (by vote share):
   vote%  prev%  authorPR%  agree  approach  [top author]
    47%    34%      55%     0.96  Debounce the callback timing  [Drew Reese]  <- #1 central
    32%    30%      20%     0.93  Debounced value in state  [T. Park]
     9%     8%       9%     1.00  Use a debounce library  [lib-fan]

AUTHORITY OVERLAY (raw PageRank — concentrated; centrality != correctness):
  most-central answerer: Drew Reese — 57% of this question's network centrality [ONE-VOICE-DOMINATED]
  backs 〈Debounce the callback timing〉 -> AGREES with the vote-leader
  top-3 central authors in vote-leader camp: 2 of 3 · authority coverage: 98%
NOTE: if the central voice AGREES with the vote-leader, discount it (endogenous — central because upvoted)."""
_NARR_FS2_OUTPUT = {
    "reasoning": "Two camps split the votes (47% vs 32%, effective ~3) -> polarization, and FLAGGED fragile. "
                 "The most-central answerer agrees with the vote-leader, but that agreement is endogenous "
                 "(central because upvoted) so it only weakly reinforces, not independent corroboration.",
    "headline": "Two debounce camps split the vote, leaning to callback timing",
    "body": "The community splits into two main camps: debouncing the callback timing leads with 47% of the "
            "vote against 32% for keeping a debounced value in state — a polarization that the data flags as "
            "borderline. The most-central answerer (Drew Reese, 57% of the question's centrality) sits with the "
            "leading camp, but that overlap is weak corroboration at best: such answerers are central precisely "
            "because their answers get upvoted, so authority and votes are not independent here.",
}


def build_narrative_messages(signal_table: str) -> list[dict]:
    return [
        {"role": "system", "content": _NARR_SYSTEM},
        {"role": "user", "content": _NARR_FS1_INPUT},
        {"role": "assistant", "content": json.dumps(_NARR_FS1_OUTPUT, ensure_ascii=False)},
        {"role": "user", "content": _NARR_FS2_INPUT},
        {"role": "assistant", "content": json.dumps(_NARR_FS2_OUTPUT, ensure_ascii=False)},
        {"role": "user", "content": signal_table},
    ]
