# Network & Multi-source Authority (v2)

The authority module of the Stack Overflow `[reactjs]` consensus-mapping project (Framing
B). Builds the user-user interaction graph from Q→A edges, computes
multiple centrality / authority measures, detects Louvain communities,
synthesizes a multi-source authority score, and ships a Streamlit dashboard.

---

## Files

| File | Role |
|---|---|
| `config.py` | all tunables; supports runtime override via `apply_overrides()` |
| `db.py` | schema-tolerant DB access (questions / answers / users / interactions) |
| `graph.py` | builds directed Q→A user graph (full + yearly), with data-cleaning filters |
| `centrality.py` | 10 centrality measures with safety nets for large graphs |
| `authority.py` | yearly PageRank, Louvain, multi-source authority synthesis |
| `user_stats.py` | per-user Q/A counts, accept rates, activity span |
| `storage.py` | writes everything into one fat `user_authority` table |
| `html_utils.py` | converts SO `body` HTML → Markdown (preserves code blocks) |
| `pipeline.py` | `AuthorityPipeline.run()` + `AuthorityStore` reader |
| `dashboard.py` | Streamlit dashboard (5 pages) |
| `__init__.py` | public API |

---

## Installation

```bash
pip install networkx python-louvain scipy pandas streamlit plotly beautifulsoup4
```

`beautifulsoup4` is optional — if missing, a stdlib-only HTML parser is
used (slightly less clean output).

---

## Usage

### 1. Run the authority pipeline

The DB path and every config knob can be set per-run via kwargs:

```python
from authority import AuthorityPipeline

# all defaults
AuthorityPipeline("so_data_react_2021_2026.db").run()

# override individual settings just for this run
AuthorityPipeline(
    "so_data_react_2021_2026.db",
    EDGE_DIRECTION="answerer_to_asker",
    YEARS=(2024, 2025),
    VALID_YEAR_MIN=2024,
    VALID_YEAR_MAX=2025,
    CENTRALITY_METHODS=("pagerank", "in_degree", "hits_authority"),
    BETWEENNESS_SAMPLE_K=None,  # exact, slower
).run()
```

Any `UPPER_CASE` name in `config.py` can be passed as a kwarg. Unknown
keys raise `ValueError` listing the valid set. The override is applied for
the duration of `.run()` and restored afterwards, so other consumers of
the module see the defaults unchanged.

`run()` returns an `AuthorityRun`:

```python
res = AuthorityPipeline("so_data_react_2021_2026.db").run()
print(res.summary())               # full dict for the audit step
print(res.top_authorities(10))     # top users by synthesized authority
```

Pass `persist=False` to compute without writing to the DB, and
`compute_centrality=False` to skip the centrality step for faster iteration.

### 2. Read authority output from other stages

```python
from authority import AuthorityStore

r = AuthorityStore("so_data_react_2021_2026.db")

# pandas DataFrame, one row per user, ~28 columns including every centrality
df = r.user_table()

# what Stage 4 compute_cluster_metrics expects
pr_2024 = r.pagerank_yearly(2024)

# individual centrality lookups
betweenness = r.centrality("betweenness")
auth = r.authority_score()
comm = r.communities()
```

### 3. Launch the dashboard

```bash
streamlit run authority/dashboard.py -- --db so_data_react_2021_2026.db
```

Five pages:

1. **Overview** — run metadata, top-N users by authority.
2. **Distributions** — cumulative distribution charts for any metric
   (PageRank, authority, reputation, every centrality, Q/A counts, accept
   rate). Auto-switches to log-x when the value range spans >1000×.
3. **Network** — interactive Plotly network: top-N users by chosen metric,
   nodes coloured by Louvain community, sized by metric. Hover for user
   details.
4. **Centrality comparison** — Spearman rank-correlation heatmap across
   all 10 centralities, side-by-side top-K tables per measure, and a
   scatter plot of any two centralities against each other.
5. **User detail** — pick a user, see all their stats, every centrality
   score, and their top questions/answers **rendered with real code
   blocks** (parses `body` HTML, not `body_text`).

---

## Graph design

* **Nodes**: users (`user_id`)
* **Edges**: `asker → answerer`, one per answer; self-answers dropped;
  excluded users (e.g. `user_id=0` for SO's deleted-user placeholder)
  dropped; answers outside `[VALID_YEAR_MIN, VALID_YEAR_MAX]` dropped.
* **Weight**: count of answers from the same (asker, answerer) ordered pair.
* **Yearly subgraphs**: per spec §2.6, with `rank_percentile` for
  cross-year comparison.

Edge direction is a config flag (`EDGE_DIRECTION`):

| Setting | What PageRank ranks |
|---|---|
| `asker_to_answerer` *(default)* | answering authority — high score = expert answerer |
| `answerer_to_asker` | matches literal README `interactions` table layout |

---

## Centrality measures

All 10 produced by default, all normalized to `[0, 1]`. They live in the
fat table as `cent_<name>` columns.

| Measure | What it captures | Cost |
|---|---|---|
| `pagerank` | recursive authority via incoming weighted edges | fast |
| `in_degree` | weighted incoming edge count (raw popularity as answer target) | trivial |
| `out_degree` | weighted outgoing edge count (asking activity) | trivial |
| `hits_authority` | HITS authority score (mutually reinforced with hubs) | fast |
| `hits_hub` | HITS hub score | fast |
| `eigenvector` | recursive importance, no damping | fast |
| `katz` | eigenvector + baseline; automatic α back-off on divergence | fast |
| `betweenness` | bridge centrality; sampled (`BETWEENNESS_SAMPLE_K`) on large graphs | expensive |
| `closeness` | inverse mean shortest-path; falls back to top-N subgraph on large graphs | expensive |
| `harmonic` | sum of inverse distances; similar fallback | expensive |

For graphs with more than `EXPENSIVE_MAX_NODES` (default 50k), the three
expensive measures are computed on the top-`EXPENSIVE_FALLBACK_TOP_N`
subgraph (by PageRank) instead of the full graph. The notes column of
`AuthorityRun.centralities.notes` tells you which mode each measure ran in.

To opt out, override `CENTRALITY_METHODS`:

```python
AuthorityPipeline(db, CENTRALITY_METHODS=("pagerank", "hits_authority")).run()
```

---

## Output written to the DB

| Table / column | Meaning |
|---|---|
| `users.pagerank_full`, `users.community_id` | basic stamps on source `users` table |
| `user_pagerank_yearly(user_id, year, ...)` | yearly PageRank + percentile + low_data flag |
| **`user_authority`** | **one fat row per user**: stats, all centralities, authority, community |
| `authority_run_meta` | run metadata (edge direction, modularity, scheme, ...) |
| `_progress` | resumable-collection bookkeeping |

`user_authority` columns:

* identity: `user_id`, `display_name`, `reputation`
* activity: `question_count`, `answer_count`, `accepted_answer_count`,
  `answerer_accept_rate`, `asker_accept_rate`, `total_question_score`,
  `total_answer_score`, `first_activity_year`, `last_activity_year`
* network: `community_id`
* synthesized authority: `authority_score`, `authority_scheme`,
  `comp_pagerank`, `comp_tag_reputation`, `comp_accept_rate`
* centralities: `cent_pagerank`, `cent_in_degree`, `cent_out_degree`,
  `cent_hits_authority`, `cent_hits_hub`, `cent_eigenvector`, `cent_katz`,
  `cent_betweenness`, `cent_closeness`, `cent_harmonic`

Re-running is idempotent (`INSERT OR REPLACE`).

---

## Multi-source authority synthesis (spec §2.5)

Combines three signals on a per-user basis:

| Signal | Source |
|---|---|
| PageRank | this network |
| Tag reputation | SO `users.reputation` |
| Accept rate | computed from this DB (`answerer_accept_rate` from `user_stats`) |

The synthesis scheme is auto-picked from the Spearman overlap between
PageRank and reputation on the top-200 users:

| Overlap ρ | Scheme |
|---|---|
| `> 0.8` | `pagerank_only` (signals redundant) |
| `0.5 – 0.8` | `weighted`: `0.5·PR + 0.3·tag_rep + 0.2·accept_rate` |
| `< 0.5` | `dual_track` (PR primary, native reported separately) |
| `None` (constant or missing data) | `pagerank_only` (safe fallback) |

The previous `ConstantInputWarning` from scipy is now caught: if either
signal is constant on the calibration sample (e.g. all reputations are
NULL or all zero), the overlap is recorded as `None` and the scheme falls
back to `pagerank_only`.

---

## HTML → Markdown for the dashboard

The README schema stores two body variants per Q/A:

| Column | Use |
|---|---|
| `body` | raw HTML with real code | dashboard rendering |
| `body_text` | plain text, code → `[CODE]` placeholder | NLP / embedding (Stage 2) |

The dashboard renders **`body`** through `html_utils.html_to_markdown()`,
which produces:

* fenced code blocks with language hint from `class="language-jsx"` etc.
* inline `<code>` → backticks
* `<a href>` → `[text](url)`
* `<strong>`/`<em>`/`<ul>`/`<ol>`/`<blockquote>`/`<h*>` preserved

So users see real code instead of `[CODE]` placeholders.

---

## Open items inherited from the spec

* **Year window**: spec §1.1 says 2019–2025, README says 2023–2026, you've
  resolved this round to **2021–2026** in `config.py`. Yearly PageRank
  auto-detects which years actually appear in the data.
* **Authority weights**: still the placeholder `0.5/0.3/0.2`; the spec
  asks for calibration once the SO-API pull is complete.
* **`reputation` source**: currently read from `users.reputation`. The
  spec §2.5 wants *tag-specific* reputation from `user.top_tags`; that
  requires a separate SO-API pull and slots into the same column when
  available.
