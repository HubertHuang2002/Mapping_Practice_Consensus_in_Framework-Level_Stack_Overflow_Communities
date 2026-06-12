"""Authority Streamlit dashboard.

Run:
    streamlit run -m authority.dashboard -- --db so_data_react_2021_2026.db

Or:
    streamlit run authority/dashboard.py -- --db so_data_react_2021_2026.db

Pages:
  1. Overview              -- run metadata, top authorities
  2. Distributions         -- cumulative distributions of every metric
  3. Network               -- interactive Plotly network viz of top-N users
  4. Centrality comparison -- rank-correlation heatmap + side-by-side tables
  5. User detail           -- search a user, see their Qs/As with code rendered
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# allow `streamlit run authority/dashboard.py` from anywhere
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from authority import AuthorityStore, config  # noqa: E402
from authority.db import AuthorityDB  # noqa: E402
from authority.graph import build_graph  # noqa: E402
from authority.html_utils import html_to_markdown  # noqa: E402


# --------------------------------------------------------------------------
# Page setup
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Network & Authority",
    layout="wide",
)


# --------------------------------------------------------------------------
# Sidebar: DB path
# --------------------------------------------------------------------------
def _initial_db_path() -> str:
    # `--` after `streamlit run script.py` separates streamlit args from app args
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        after = sys.argv[idx + 1:]
        parser = argparse.ArgumentParser()
        parser.add_argument("--db", default=config.DB_PATH)
        ns, _ = parser.parse_known_args(after)
        return ns.db
    return config.DB_PATH


st.sidebar.title("Authority Dashboard")
db_path = st.sidebar.text_input("DB path", value=_initial_db_path())


# --------------------------------------------------------------------------
# Cached loaders
# --------------------------------------------------------------------------
@st.cache_resource(show_spinner="Connecting to DB...")
def _open_results(path: str) -> AuthorityStore:
    return AuthorityStore(path)


@st.cache_data(show_spinner="Loading user table...")
def _user_df(path: str) -> pd.DataFrame:
    r = _open_results(path)
    df = r.user_table()
    df = df.set_index("user_id", drop=False)
    return df


@st.cache_data(show_spinner="Loading run metadata...")
def _meta(path: str) -> dict:
    return _open_results(path).run_meta()


@st.cache_resource(show_spinner="Building graph (one-time)...")
def _graph(path: str) -> nx.DiGraph:
    """Rebuild the full graph for the Network page. Cached as a resource."""
    with AuthorityDB(path) as db:
        return build_graph(db).full


# --------------------------------------------------------------------------
# Page router
# --------------------------------------------------------------------------
page = st.sidebar.radio(
    "Page",
    [
        "Overview",
        "Distributions",
        "Network",
        "Centrality Comparison",
        "User Detail",
    ],
)

try:
    meta = _meta(db_path)
    df = _user_df(db_path)
except RuntimeError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"Could not open {db_path}: {e}")
    st.stop()


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
if page == "Overview":
    st.title("Overview")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Users (nodes)", f"{int(meta.get('full_nodes', 0)):,}")
    c2.metric("Edges", f"{int(meta.get('full_edges', 0)):,}")
    c3.metric("Communities", meta.get("n_communities", "?"))
    c4.metric("Modularity Q", meta.get("modularity", "?"))

    st.subheader("Run Metadata")
    meta_df = pd.DataFrame(
        sorted(meta.items()), columns=["key", "value"]
    )
    st.dataframe(meta_df, hide_index=True, use_container_width=True)

    st.subheader("Top Users by Synthesized Authority")
    top_n = st.slider("Show top", 10, 200, 30, key="ov_top")
    cols = [
        "user_id", "display_name", "authority_score",
        "reputation", "question_count", "answer_count",
        "answerer_accept_rate", "community_id",
    ]
    cols = [c for c in cols if c in df.columns]
    st.dataframe(
        df.sort_values("authority_score", ascending=False)
          .head(top_n)[cols]
          .reset_index(drop=True),
        use_container_width=True,
    )


# --------------------------------------------------------------------------
# Distributions
# --------------------------------------------------------------------------
elif page == "Distributions":
    st.title("Cumulative Distributions")
    st.caption(
        "x-axis = metric value (log scale when range is extreme); "
        "y-axis = cumulative count of users at or below x."
    )

    metric_candidates = [
        c for c in (
            "authority_score",
            "reputation",
            "cent_pagerank",
            "cent_in_degree",
            "cent_out_degree",
            "cent_hits_authority",
            "cent_hits_hub",
            "cent_eigenvector",
            "cent_katz",
            "cent_betweenness",
            "cent_closeness",
            "cent_harmonic",
            "question_count",
            "answer_count",
            "answerer_accept_rate",
            "total_answer_score",
        )
        if c in df.columns
    ]

    selected = st.multiselect(
        "Metrics to plot",
        metric_candidates,
        default=[
            m for m in
            ("authority_score", "reputation", "cent_pagerank")
            if m in metric_candidates
        ],
    )

    layout_cols = st.slider("Charts per row", 1, 4, 2)

    def _is_extreme(s: pd.Series) -> bool:
        s = s[s > 0]
        if len(s) < 2:
            return False
        return s.max() / max(s.min(), 1e-12) > 1000

    def _cdf_fig(s: pd.Series, name: str) -> go.Figure:
        s = s.dropna()
        if s.empty:
            fig = go.Figure()
            fig.update_layout(title=f"{name} (no data)")
            return fig
        s_sorted = np.sort(s.values)
        y = np.arange(1, len(s_sorted) + 1)
        use_log = _is_extreme(s)
        x = s_sorted
        if use_log:
            x = np.where(x <= 0, 1e-12, x)
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(x=x, y=y, mode="lines", name=name,
                       line=dict(width=2))
        )
        fig.update_layout(
            title=f"{name}" + (" (log x)" if use_log else ""),
            xaxis_title=name + (" (log)" if use_log else ""),
            yaxis_title="cumulative users",
            xaxis_type="log" if use_log else "linear",
            height=350,
            margin=dict(l=40, r=20, t=40, b=40),
        )
        return fig

    chunks = [selected[i:i + layout_cols] for i in range(0, len(selected), layout_cols)]
    for row in chunks:
        cols = st.columns(len(row))
        for col, m in zip(cols, row):
            with col:
                st.plotly_chart(_cdf_fig(df[m], m), use_container_width=True)


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------
elif page == "Network":
    st.title("Network Visualization")
    st.caption(
        "Top-N users by selected metric and the edges among them. "
        "Node colour = Louvain community; node size = selected metric."
    )

    metric_options = [
        c.removeprefix("cent_") if c.startswith("cent_") else c
        for c in df.columns
        if c.startswith("cent_") or c == "authority_score"
    ]
    metric = st.selectbox(
        "Rank/size by", metric_options,
        index=metric_options.index("authority_score")
              if "authority_score" in metric_options else 0,
    )
    metric_col = "authority_score" if metric == "authority_score" else f"cent_{metric}"

    top_n = st.slider("Top-N nodes to display", 50, 1000, 250)
    show_labels = st.checkbox("Show user_id labels on top 20", value=True)

    full_g = _graph(db_path)
    top_users = df.sort_values(metric_col, ascending=False).head(top_n)
    top_ids = set(top_users["user_id"].tolist())
    sub: nx.DiGraph = full_g.subgraph(top_ids).copy()

    if sub.number_of_nodes() == 0:
        st.warning("No nodes after filtering.")
    else:
        with st.spinner(f"Laying out {sub.number_of_nodes()} nodes..."):
            pos = nx.spring_layout(
                sub, k=1 / math.sqrt(max(sub.number_of_nodes(), 1)),
                iterations=50, seed=42,
            )

        # edges
        ex, ey = [], []
        for u, v in sub.edges():
            ex.extend([pos[u][0], pos[v][0], None])
            ey.extend([pos[u][1], pos[v][1], None])
        edge_trace = go.Scatter(
            x=ex, y=ey, mode="lines",
            line=dict(width=0.5, color="rgba(120,120,120,0.4)"),
            hoverinfo="none",
        )

        # nodes
        nx_data = []
        comm_lookup = dict(zip(df["user_id"], df["community_id"]))
        for uid in sub.nodes():
            row = top_users.loc[uid] if uid in top_users.index else None
            score = row[metric_col] if row is not None else 0.0
            nx_data.append({
                "uid": int(uid),
                "x": pos[uid][0], "y": pos[uid][1],
                "size": 6 + 30 * float(score),
                "community": comm_lookup.get(uid, -1),
                "name": (row["display_name"] if row is not None
                         and "display_name" in row.index else f"u{uid}"),
                "metric": float(score),
            })
        nxdf = pd.DataFrame(nx_data)

        node_trace = go.Scatter(
            x=nxdf["x"], y=nxdf["y"],
            mode="markers+text" if show_labels else "markers",
            marker=dict(
                size=nxdf["size"],
                color=nxdf["community"],
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(title="community"),
                line=dict(width=0.5, color="white"),
            ),
            text=[str(nxdf.iloc[i]["uid"]) if show_labels and i < 20
                  else "" for i in range(len(nxdf))],
            textposition="top center",
            textfont=dict(size=10),
            customdata=nxdf[["uid", "name", "community", "metric"]].values,
            hovertemplate=(
                "<b>user %{customdata[0]} (%{customdata[1]})</b><br>"
                "community: %{customdata[2]}<br>"
                + metric + ": %{customdata[3]:.4f}<extra></extra>"
            ),
        )

        fig = go.Figure(data=[edge_trace, node_trace])
        fig.update_layout(
            height=700,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(showgrid=False, zeroline=False, visible=False),
            yaxis=dict(showgrid=False, zeroline=False, visible=False),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        n_comm = nxdf["community"].nunique()
        st.caption(
            f"{sub.number_of_nodes()} nodes, {sub.number_of_edges()} edges, "
            f"{n_comm} distinct communities in this subgraph."
        )


# --------------------------------------------------------------------------
# Centrality comparison
# --------------------------------------------------------------------------
elif page == "Centrality Comparison":
    st.title("Centrality Comparison")
    st.caption(
        "Different centrality measures rank users differently. Spearman rank "
        "correlation shows how much they agree; the side-by-side tables show "
        "the top-K user lists."
    )

    cent_cols = [c for c in df.columns if c.startswith("cent_")]
    if not cent_cols:
        st.warning("No centrality columns found in the DB.")
        st.stop()

    # ---- correlation heatmap -------------------------------------------
    st.subheader("Rank Correlation (Spearman)")
    rho = df[cent_cols].rank().corr(method="spearman")
    rho.index = [c.removeprefix("cent_") for c in rho.index]
    rho.columns = [c.removeprefix("cent_") for c in rho.columns]
    fig = px.imshow(
        rho, text_auto=".2f", aspect="auto",
        color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
    )
    fig.update_layout(height=500, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

    # ---- side-by-side top-K --------------------------------------------
    st.subheader("Top-K by Each Centrality")
    k = st.slider("K", 5, 50, 15)
    methods = st.multiselect(
        "Methods to show", cent_cols,
        default=cent_cols[: min(4, len(cent_cols))],
    )
    cols = st.columns(max(len(methods), 1))
    for col, m in zip(cols, methods):
        with col:
            mname = m.removeprefix("cent_")
            top = df.sort_values(m, ascending=False).head(k)
            display = top[["user_id", "display_name", m]].rename(
                columns={m: mname}
            ).reset_index(drop=True)
            st.markdown(f"**{mname}**")
            st.dataframe(display, hide_index=True, use_container_width=True)

    # ---- scatter --------------------------------------------------------
    st.subheader("Scatter: Two Centralities")
    c1, c2 = st.columns(2)
    with c1:
        x_metric = st.selectbox("X axis", cent_cols, key="scx", index=0)
    with c2:
        y_metric = st.selectbox(
            "Y axis", cent_cols, key="scy",
            index=min(1, len(cent_cols) - 1),
        )
    sub = df[[x_metric, y_metric, "user_id", "community_id"]].dropna()
    fig = px.scatter(
        sub, x=x_metric, y=y_metric,
        color="community_id",
        hover_data=["user_id"],
        opacity=0.6,
    )
    fig.update_layout(height=500)
    st.plotly_chart(fig, use_container_width=True)


# --------------------------------------------------------------------------
# User detail
# --------------------------------------------------------------------------
elif page == "User Detail":
    st.title("User Detail")

    c1, c2 = st.columns([1, 1])
    with c1:
        uid_input = st.text_input("user_id (exact)", value="")
    with c2:
        top_pick = st.selectbox(
            "Pick from top-100 by authority",
            options=[""] + [
                f"{int(u)} ({n})"
                for u, n in df.sort_values("authority_score", ascending=False)
                    .head(100)[["user_id", "display_name"]].values
            ],
        )

    selected_uid: int | None = None
    if uid_input.strip().isdigit():
        selected_uid = int(uid_input.strip())
    elif top_pick:
        selected_uid = int(top_pick.split()[0])

    if selected_uid is None:
        st.info("Enter a user_id or pick from the dropdown.")
        st.stop()

    if selected_uid not in df.index:
        st.warning(f"user_id {selected_uid} is not in user_authority.")
        st.stop()

    row = df.loc[selected_uid]
    st.subheader(
        f"User {selected_uid}"
        + (f" — {row['display_name']}" if row.get("display_name") else "")
    )

    cols = st.columns(4)
    cols[0].metric("authority", f"{row['authority_score']:.4f}")
    cols[1].metric("reputation", int(row.get("reputation") or 0))
    cols[2].metric("questions", int(row.get("question_count") or 0))
    cols[3].metric("answers", int(row.get("answer_count") or 0))

    cols = st.columns(4)
    cols[0].metric("accepted ans.", int(row.get("accepted_answer_count") or 0))
    cols[1].metric(
        "answerer accept",
        f"{(row.get('answerer_accept_rate') or 0):.1%}",
    )
    cols[2].metric(
        "asker accept",
        f"{(row.get('asker_accept_rate') or 0):.1%}",
    )
    cols[3].metric("community", int(row.get("community_id") or -1))

    st.markdown("**All centrality scores (normalized to [0,1])**")
    cent_cols = [c for c in df.columns if c.startswith("cent_")]
    cent_df = pd.DataFrame({
        "centrality": [c.removeprefix("cent_") for c in cent_cols],
        "value": [row[c] for c in cent_cols],
    }).sort_values("value", ascending=False)
    st.dataframe(cent_df, hide_index=True, use_container_width=True)

    # ---- show their questions / answers --------------------------------
    st.markdown("---")
    sc1, sc2 = st.columns(2)
    with sc1:
        show_n = st.slider(
            "How many of this user's Q/A to load", 5, 50, 10, key="qa_n"
        )
    with sc2:
        max_a_per_q = st.slider(
            "Max answers to show per question", 1, 30, 10, key="apq_n"
        )

    # Fetch everything while the DB connection is open so we can render
    # outside the `with` block (nested expanders aren't allowed in Streamlit).
    with AuthorityDB(db_path) as raw_db:
        user_qs = raw_db.questions_by_user(selected_uid, limit=show_n)
        user_as = raw_db.answers_by_user(selected_uid, limit=show_n)
        qs_with_answers = [
            (q, raw_db.answers_for_question(q["question_id"], limit=max_a_per_q))
            for q in user_qs
            if q.get("question_id") is not None
        ]
        as_with_question = [
            (a, raw_db.question_by_id(a["question_id"]))
            for a in user_as
            if a.get("question_id") is not None
        ]

    # ---- Questions section (each Q + all its answers) -----------------
    st.subheader(f"Questions by This User ({len(qs_with_answers)})")
    st.caption(
        "Each panel shows the user's question followed by **every** answer "
        "it received. Answers are sorted: accepted first, then by score."
    )
    if not qs_with_answers:
        st.info("This user has no questions in the DB.")
    for i, (q, answers) in enumerate(qs_with_answers):
        title = q.get("title") or f"question {q.get('question_id', '?')}"
        n_answers = len(answers)
        with st.expander(
            f"[Q score={q.get('score', '?')}, {n_answers} answers] {title}",
            expanded=(i < 2),
        ):
            st.caption(
                f"question_id={q.get('question_id', '?')}, "
                f"answer_count={q.get('answer_count', '?')}, "
                f"link={q.get('link', '')}"
            )
            st.markdown("**Question**")
            st.markdown(html_to_markdown(q.get("body", "")))

            st.markdown("---")
            st.markdown(f"**Answers to this question ({n_answers})**")
            if not answers:
                st.info("No answers in the DB for this question.")
            for j, a in enumerate(answers):
                accepted = bool(a.get("is_accepted"))
                owner = a.get("owner_user_id")
                owner_name = a.get("owner_display_name") or ""
                is_self = owner == selected_uid
                marker = " 👈 **this user**" if is_self else ""
                badge = "✓ accepted — " if accepted else ""
                st.markdown(
                    f"#### {badge}Answer #{j + 1} "
                    f"(score={a.get('score', '?')}, by user "
                    f"{owner}{f' / {owner_name}' if owner_name else ''}{marker})"
                )
                st.markdown(html_to_markdown(a.get("body", "")))
                if j < len(answers) - 1:
                    st.markdown("---")

    # ---- Answers section (each A + the original question) -------------
    st.subheader(f"Answers by This User ({len(as_with_question)})")
    st.caption(
        "Each panel shows the **original question** first, then this user's "
        "answer to it — so you have the context for what they were answering."
    )
    if not as_with_question:
        st.info("This user has no answers in the DB.")
    for i, (a, orig_q) in enumerate(as_with_question):
        accepted = bool(a.get("is_accepted"))
        qid = a.get("question_id", "?")
        a_label = (
            f"[A score={a.get('score', '?')}"
            f"{', ✓ accepted' if accepted else ''}] on question {qid}"
        )
        with st.expander(a_label, expanded=(i < 2)):
            # original question
            if orig_q is None:
                st.warning(f"Original question {qid} not found in DB.")
            else:
                q_title = orig_q.get("title") or f"question {qid}"
                q_owner = orig_q.get("owner_user_id")
                q_owner_name = orig_q.get("owner_display_name") or ""
                st.markdown(
                    f"**Original question:** _{q_title}_  \n"
                    f"score={orig_q.get('score', '?')}, "
                    f"answer_count={orig_q.get('answer_count', '?')}, "
                    f"asked by user {q_owner}"
                    f"{f' / {q_owner_name}' if q_owner_name else ''}"
                )
                if orig_q.get("link"):
                    st.caption(f"link: {orig_q['link']}")
                st.markdown(html_to_markdown(orig_q.get("body", "")))

            st.markdown("---")
            st.markdown("**This user's answer:**")
            st.markdown(html_to_markdown(a.get("body", "")))
