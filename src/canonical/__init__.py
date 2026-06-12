"""canonical — Module C: a free-text query → its query-equivalent canonical group of questions.

Real path (2b, OpenAI): `resolver.OpenAIRagResolver` (the QueryResolver seam serve uses) embeds the
query, retrieves over the prebuilt corpus index, and gates each candidate for same-question equivalence:

    embed (text-embedding-3-small)  →  index (in-memory cosine)  →  gate (LLM equivalence, nano)
    └─ build_index builds the corpus .npy once          resolver wires these + writes canonical_group

The heavy deps (numpy / openai) live in those modules and are NOT eager-imported here, so importing
the `canonical` package stays light.

Stand-ins still in use:
  • `proxy.DuplicateChainGroups` — the CanonicalGroupProvider (fetch half: group_id → member answers),
    proxied off the SO duplicate chain + snapshot until a real DB-backed fetch lands (PLAN step 9).
  • `resolve.KnownChainResolver` — a zero-dependency QueryResolver fallback (curated demo queries only),
    kept for offline / no-API use; serve runs the real OpenAIRagResolver.
  • `fetch` — one-shot builder for the out-of-window canonical fixture (data/canonical_q54069253.json).
"""
