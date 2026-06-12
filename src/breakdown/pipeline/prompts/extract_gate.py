"""Stage ③+④ — Extractor + Relevance gate, merged into one LLM call (the validated D-1g method).

(③) EXTRACTION pulls every practice an answer proposes; (④) the RELEVANCE GATE marks each
relevant/substantive against THE GROUP'S canonical problem. This relevance gate is a DIFFERENT
gate from the resolver's EQUIVALENCE gate (canonical/gate.py, group membership).

Generalization (2026-06-04, overfit fix — CONTEXT Flagged / PLAN step 11): the few-shots had been
ALL useState and the system prompt hardcoded a useState-framed drop-list (controlled inputs /
event / routing / CSS / validation / …). A targeted probe ($0.0246) proved the model then
SUBSTITUTED useState for whatever canonical_problem it was handed — the canonical solution to a
routing / controlled-input / CSS / validation query came back relevant=False (gate_reason literally
naming "the state-update problem"). Fix: cross-topic few-shots, each carrying its OWN canonical
problem (useState, routing, data-fetching), and a RELATIVE "different concern than THIS problem"
rule instead of a fixed topic list. relevance is now judged against the canonical_problem actually
passed in (anchor text today; medoid text after ADR 0010), not a baked-in topic.
"""
from __future__ import annotations

import json

_EXTRACT_GATE_SYSTEM = """\
You read ONE Stack Overflow answer to a React implementation problem and do TWO things in a
SINGLE pass: (1) extract the implementation practices the answer proposes, then (2) for each
extracted practice, judge whether it belongs in the breakdown for THE CANONICAL PROBLEM
stated below.

Do (1) FIRST and generously, exactly as a faithful extractor would; only AFTER writing a
practice do you judge it. NEVER silently skip a practice because you suspect it is off-topic —
always extract it, then mark it. This keeps niche / minority approaches visible.

(1) EXTRACTION
A *practice* is a concrete action/approach the answer recommends the developer take to solve
the problem — NOT a sentiment or an explanation of root cause. For each DISTINCT practice:
  - practice: ONE declarative sentence stating the recommended action/approach. PRESERVE the
    answer's specificity — keep the scope the answer actually names (e.g. "when updating a Map",
    "the referenced data") instead of flattening it into a bland generic sentence. Phrase similar
    recommendations similarly and genuinely different ones differently.
  - conditions: 0-3 SHORT caveats the answer EXPLICITLY states about when this practice applies
    (e.g. "only for local state", "only in React 18+"). Use [] unless the answer literally
    states such a caveat. NEVER infer or invent a condition; do not restate "when this is useful".
  (Do NOT classify evidence_type — whether the answer shows code is detected deterministically
  in Python, not by you.)
Return practices=[] if the answer proposes no implementation practice — e.g. it only explains
WHY the problem happens, says "me too", says thanks, or asks a clarifying question. If it
recommends two genuinely different approaches, return BOTH (primary first). Be faithful to the
answer; do not add your own React knowledge.

(2) PER-PRACTICE GATE — judge each practice against the CANONICAL PROBLEM below (NOT the
answer's own question), on two INDEPENDENT axes. Judge the PRACTICE AS STATED — never let the
source answer's terseness or missing code lower your verdict:
  - gate_reason: 1 short sentence justifying the two booleans. Write it BEFORE them.
  - relevant: TRUE if the practice is an approach a React developer would take to address THE
    CANONICAL PROBLEM — whatever that problem's topic happens to be (state, effects, rendering,
    routing, forms, data fetching, performance, styling, types, tooling, …). Judge relevance
    RELATIVE TO THIS problem; do NOT assume any fixed topic. Mark FALSE ONLY when the practice
    solves a DIFFERENT React concern than the one THIS canonical problem asks about — something
    the source answer merely also touched in passing (e.g. a routing tip raised inside a
    state-timing problem, or a CSS-styling tip raised inside a routing problem). The SAME practice
    can be relevant under one canonical problem and off-topic under another — decide by THIS
    problem, not by the practice's topic in the abstract. When unsure whether a practice addresses
    the canonical problem, mark TRUE.
  - substantive: judge the PRACTICE itself, NOT how much the answer explained it. A recognizable,
    reusable React technique that addresses the problem is substantive EVEN IF the source answer
    is terse or shows no code. Mark FALSE ONLY for: (a) a content-free placeholder that just
    points back at the answer ("use the approach shown", "use the provided version", "try this",
    "change the function to the provided version"), or (b) a fix that names specific local
    identifiers with no general lesson.
STRONG bias to KEEP (both TRUE). Niche / advanced / unpopular but on-topic approaches MUST be
kept; a well-known majority pattern is ALWAYS substantive."""


def _eg_user(canon: str, title: str, answer: str) -> str:
    return f"Canonical problem:\n{canon}\n\nQuestion: {title}\nAnswer: {answer}"


# ── Few-shots: cross-topic, each with its OWN canonical problem (this is the overfit fix) ──────
_CANON_STATE = (
    "The useState setter does not reflect a change immediately\n\n"
    "I call the setter and read the state right after, but it still holds the old value. "
    "How do I work with the updated value?"
)
_CANON_ROUTING = (
    "How do I navigate programmatically after a form submit in React Router v6?\n\n"
    "After a successful submit I want to redirect from inside my event handler, not with a <Link>."
)
_CANON_FETCH = (
    "Warning: can't perform a React state update on an unmounted component after a fetch\n\n"
    "My component fetches data in useEffect and sometimes setState runs after it unmounts. "
    "How should I handle the fetch lifecycle?"
)

# FS_A — useState (anchor case, both kept: keeps the validated useState behavior from regressing).
_FSA_TITLE = "The useState set method is not reflecting a change immediately"
_FSA_ANSWER = (
    "setState is asynchronous, so the variable still holds the old value right after you call the "
    "setter. Use the functional update form so you build off the latest state:\n```\n"
    "setCount(prev => prev + 1)\n```\nAnd if you need to run something once the value has actually "
    "changed, do it in a useEffect that lists that state as a dependency."
)
_FSA_OUTPUT = {
    "reasoning": "Two distinct on-topic state-handling actions: functional updater and a useEffect; no stated caveats.",
    "practices": [
        {"practice": "Use the functional updater form of the state setter (setState(prev => ...)) to build off the latest state instead of the stale value.",
         "conditions": [],
         "gate_reason": "The functional updater is a central approach to the stale-setter problem.",
         "relevant": True, "substantive": True},
        {"practice": "Run logic that depends on the updated value inside a useEffect that lists that state as a dependency.",
         "conditions": [],
         "gate_reason": "Reacting to the updated value in an effect directly addresses the problem.",
         "relevant": True, "substantive": True},
    ],
}

# FS_B — routing (teaches RELATIVE relevance: navigation is on-topic HERE, CSS is the off-topic one;
# directly inverts the old hardcoded "routing always drop"). Also keeps the placeholder lesson.
_FSB_TITLE = "How do I redirect after a form submit in React Router v6?"
_FSB_ANSWER = (
    "Use the useNavigate hook and call it in your submit handler:\n```\nconst navigate = "
    "useNavigate();\nnavigate('/dashboard');\n```\nYou can also highlight the active link with "
    "clsx. If neither helps, just use the snippet above."
)
_FSB_OUTPUT = {
    "reasoning": "Three actions under a routing problem: programmatic navigation (on-topic), active-link styling (a different concern), and a placeholder pointer.",
    "practices": [
        {"practice": "Use the useNavigate hook to navigate programmatically from inside the submit handler.",
         "conditions": [],
         "gate_reason": "Programmatic navigation via useNavigate is the core approach to THIS routing problem.",
         "relevant": True, "substantive": True},
        {"practice": "Highlight the active link with the clsx helper.",
         "conditions": [],
         "gate_reason": "Active-link styling is a CSS concern the answer also touched, not the navigation problem.",
         "relevant": False, "substantive": True},
        {"practice": "Use the snippet shown above.",
         "conditions": [],
         "gate_reason": "Content-free placeholder that just points back at the answer's code.",
         "relevant": True, "substantive": False},
    ],
}

# FS_C — a "me too" non-answer -> empty list (nothing to extract or gate).
_FSC_TITLE = "The useState set method is not reflecting a change immediately"
_FSC_ANSWER = (
    "I'm hitting exactly the same thing on React 18. Did anyone find a clean fix? Following this thread."
)
_FSC_OUTPUT = {"reasoning": "No recommendation — a 'me too' follow request.", "practices": []}

# FS_D — data-fetching, TERSE: both real techniques KEPT. Teaches substantive judges the PRACTICE,
# not the answer's brevity, on a NON-useState topic.
_FSD_TITLE = "Warning: can't perform a React state update on an unmounted component after fetch"
_FSD_ANSWER = (
    "Use an AbortController in the useEffect and abort it in cleanup so you don't set state after "
    "unmount. You can also just use React Query and let it handle cancellation."
)
_FSD_OUTPUT = {
    "reasoning": "Two real techniques named tersely under a fetch-lifecycle problem: AbortController cleanup and React Query.",
    "practices": [
        {"practice": "Abort the fetch with an AbortController in the useEffect cleanup so state is not set after the component unmounts.",
         "conditions": [],
         "gate_reason": "Aborting in cleanup directly addresses the set-after-unmount problem; a recognizable technique even though the answer is terse.",
         "relevant": True, "substantive": True},
        {"practice": "Use React Query to manage the fetch and its cancellation.",
         "conditions": [],
         "gate_reason": "A data-fetching library that handles cancellation is an on-topic approach here; substantive.",
         "relevant": True, "substantive": True},
    ],
}

# Smoke-test canonical problem for the extract.py CLI — an EXAMPLE (useState), not a privileged
# "the" canonical problem (the old _GATE_CANON / CANONICAL_PROBLEM doubled as both, which leaked).
EXAMPLE_PROBLEM = _CANON_STATE


def build_extraction_gated_messages(canonical_problem: str, question_title: str,
                                    answer_text: str) -> list[dict]:
    return [
        {"role": "system", "content": _EXTRACT_GATE_SYSTEM},
        {"role": "user", "content": _eg_user(_CANON_STATE, _FSA_TITLE, _FSA_ANSWER)},
        {"role": "assistant", "content": json.dumps(_FSA_OUTPUT, ensure_ascii=False)},
        {"role": "user", "content": _eg_user(_CANON_ROUTING, _FSB_TITLE, _FSB_ANSWER)},
        {"role": "assistant", "content": json.dumps(_FSB_OUTPUT, ensure_ascii=False)},
        {"role": "user", "content": _eg_user(_CANON_STATE, _FSC_TITLE, _FSC_ANSWER)},
        {"role": "assistant", "content": json.dumps(_FSC_OUTPUT, ensure_ascii=False)},
        {"role": "user", "content": _eg_user(_CANON_FETCH, _FSD_TITLE, _FSD_ANSWER)},
        {"role": "assistant", "content": json.dumps(_FSD_OUTPUT, ensure_ascii=False)},
        {"role": "user", "content": _eg_user(canonical_problem, question_title, answer_text)},
    ]
