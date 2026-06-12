"""store — persistence over the shared SQLite integration bus. `schema.py` holds the §3.2
normalized D-side tables (source of truth); `cache.py` holds query_viz_cache (the materialized
Breakdown derived from them). bake writes both, serve reads only the cache.

`init_store(conn)` creates the whole D-side schema in one shot (normalized + cache)."""
from __future__ import annotations

import sqlite3

from store.cache import init_cache
from store.schema import init_schema

__all__ = ["init_store", "init_schema", "init_cache"]


def init_store(conn: sqlite3.Connection) -> None:
    """Create every D-side table — normalized source-of-truth + derived viz cache."""
    init_schema(conn)
    init_cache(conn)
