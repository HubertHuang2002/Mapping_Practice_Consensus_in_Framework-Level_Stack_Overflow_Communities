"""orchestration — the application layer that composes the domains into the bake use case.

`driver.py` will hold the idempotent `bake(group_id)` (ADR 0009 §3 + Amendment 2026-06-03 —
keyed by canonical group, not query): resolve group (canonical) →
extract → gate → cluster (breakdown.pipeline) → authority overlay → narrative → assemble + layout →
materialize into store.cache. It depends DOWN on contract + the domains (via ports); only serve
depends on it (cold-path enqueue). Built in phase 2a.
"""
