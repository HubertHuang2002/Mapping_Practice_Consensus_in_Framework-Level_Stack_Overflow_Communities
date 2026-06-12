"""
Module A — shared configuration for the React-only data collection scripts (2021-2026).
"""

import os
from pathlib import Path

# Stack Overflow API v2.3
API_BASE = "https://api.stackexchange.com/2.3"
# Set SE_API_KEY env var (or copy .env.example -> .env at repo root)
API_KEY = os.environ.get("SE_API_KEY", "")

# Target tag: React only (SO tag is "reactjs", DB column is is_react).
TAGS = ["reactjs"]

# Explicit tag -> SQL column mapping. Keep SO tag in TAGS for API calls;
# use a friendlier column name in the DB schema.
TAG_COLUMNS = {"reactjs": "is_react"}

# Ordered list of one-hot column names (matches TAGS order).
ONEHOT_COLS = [TAG_COLUMNS[t] for t in TAGS]

# Time range: 63 months (2021-01 ~ 2026-04), collected in 5 yearly sub-periods.
MONTHS = 63

# Per-tag per-period cap. 0 = no cap (collect everything SO has).
MAX_QUESTIONS_PER_TAG_PER_YEAR = 0

# Cross-framework comparison: disabled (single framework, no comparison possible).
COLLECT_CROSS_FRAMEWORK = False

# Framework name variants (kept for code compatibility; unused when COLLECT_CROSS_FRAMEWORK=False).
FRAMEWORK_NAMES = {
    "reactjs": ["react.js", "reactjs", "react js"],
}

# Database path
DB_PATH = Path(__file__).parent / "so_data_react.db"

# API rate limiting
REQUEST_DELAY = 0.5
PAGE_SIZE = 100
