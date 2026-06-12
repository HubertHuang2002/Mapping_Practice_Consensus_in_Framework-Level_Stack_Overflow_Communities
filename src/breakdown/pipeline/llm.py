"""The Q2 provider-neutral seam — currently with an OpenAI-strict adapter only.

Call sites use `llm_call(messages, schema, tier, ...)` and never touch OpenAI directly.
Swapping provider = rewrite this file's adapter, not the call sites (PLAN ▼Q2).
We embrace OpenAI strict Structured Outputs: the parsed object is schema-guaranteed,
so there is no parse/retry loop here.
"""
import os
import threading
from pathlib import Path
from typing import Type, TypeVar

import httpx  # transitive via openai; used here to set an explicit request timeout on the client
from openai import BadRequestError, OpenAI
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3]

# Semantic tier -> concrete model id. tier is portable; this mapping is provider-specific.
# G12 TODO: pin dated snapshots (e.g. gpt-5.4-mini-2026-xx) + record resolved comp.model.
TIER_MODEL = {
    "gate": "gpt-5.4-nano",
    "extract": "gpt-5.4-mini",
    "aggregate": "gpt-5.4",
    "narrative": "gpt-5.4",
}

_NO_TEMP: set[str] = set()  # tiers empirically found to reject a custom temperature
T = TypeVar("T", bound=BaseModel)

# Running token tally (G12/cost visibility). Prices are USD per 1M tokens — approximate
# gpt-5.4-family placeholders; the authoritative figure is the provider invoice.
PRICE = {  # tier -> (input, output)
    "gate": (0.20, 1.20),       # gpt-5.4-nano
    "extract": (0.75, 4.50),    # gpt-5.4-mini
    "aggregate": (2.50, 12.00),  # gpt-5.4
    "narrative": (2.50, 12.00),  # gpt-5.4
}
# Per-tier so a mixed-tier bake costs out correctly (a nano gate ≠ a gpt-5.4 narrator).
USAGE: dict[str, dict[str, int]] = {t: {"calls": 0, "input": 0, "output": 0} for t in PRICE}
_USAGE_LOCK = threading.Lock()  # extract/gate fire llm_call from thread pools — keep the tally exact


def usage_cost() -> dict:
    """Snapshot the running tally: per-tier tokens + USD cost, plus the rolled-up totals.
    Embeddings (the resolve-time query embed) are NOT counted — negligible for one short query.
    In-process only: resets on serve restart. Caller diffs two snapshots to cost a single bake."""
    by_tier, calls, inp, out, cost = {}, 0, 0, 0, 0.0
    for t, u in USAGE.items():
        pin, pout = PRICE[t]
        c = u["input"] / 1e6 * pin + u["output"] / 1e6 * pout
        by_tier[t] = {**u, "cost_usd": round(c, 6)}
        calls += u["calls"]; inp += u["input"]; out += u["output"]; cost += c
    return {"calls": calls, "input": inp, "output": out,
            "cost_usd": round(cost, 6), "by_tier": by_tier}


def usage_report() -> str:
    s = usage_cost()
    return (f"LLM usage: {s['calls']} calls | {s['input']:,} in + "
            f"{s['output']:,} out tok | ~${s['cost_usd']:.4f}")


def _load_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENAI_API_KEY"):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENAI_API_KEY not found (env or .env)")


# Explicit request timeout. Without it the SDK default is 600s/request: a single stalled gate call
# (the gate fires ~100 concurrent calls and ex.map() waits on them IN ORDER — gate.py) then freezes
# the whole resolve for up to 10 min, with the request handler parked on a socket recv. Bound it:
# connect=10s catches connection black-holes fast; read=60s is generous for the narrator's long
# generations. A timeout now RAISES (APITimeoutError) and is caught per-candidate in gate.run(),
# which drops that candidate instead of hanging. max_retries kept at the SDK default (2) for transient
# 429/5xx resilience under the gate's concurrency.
_client = OpenAI(
    api_key=_load_key(),
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)


def _parse(**kwargs):
    # parse() graduated out of .beta in recent SDKs; fall back just in case.
    try:
        return _client.chat.completions.parse(**kwargs)
    except AttributeError:
        return _client.beta.chat.completions.parse(**kwargs)


def llm_call(
    messages: list[dict],
    schema: Type[T],
    tier: str,
    k_voting: int = 1,
    temperature: float = 0.0,
) -> T | list[T]:
    """Return a schema-validated instance (k_voting=1) or list of them (k_voting>1)."""
    model = TIER_MODEL[tier]

    def one() -> T:
        kwargs = dict(model=model, messages=messages, response_format=schema)
        if tier not in _NO_TEMP:
            kwargs["temperature"] = temperature
        try:
            comp = _parse(**kwargs)
        except BadRequestError as e:
            if "temperature" in str(e).lower():
                _NO_TEMP.add(tier)  # this model only allows default temp; drop and retry
                kwargs.pop("temperature", None)
                comp = _parse(**kwargs)
            else:
                raise
        if comp.usage:
            with _USAGE_LOCK:
                u = USAGE[tier]
                u["calls"] += 1
                u["input"] += comp.usage.prompt_tokens
                u["output"] += comp.usage.completion_tokens
        msg = comp.choices[0].message
        if getattr(msg, "refusal", None):
            raise RuntimeError(f"model refused: {msg.refusal}")
        return msg.parsed

    return one() if k_voting == 1 else [one() for _ in range(k_voting)]


def embed(texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]:
    """Probe stand-in for the spec's local SBERT companion embedder.

    NOTE: the real companion (ADR 0002) must use local SBERT so it is independent of the
    LLM provider (reproducibility floor / drift check). This OpenAI embedder is a probe
    shortcut to avoid a heavy torch install; swap is one function.
    """
    resp = _client.embeddings.create(model=model, input=texts)
    return [d.embedding for d in resp.data]
