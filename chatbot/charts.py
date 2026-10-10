"""Plotly figures for the chatbot."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .correlation import NN_BANDS_M, CorrelationResult
from .grid import LEVEL_LABEL, cell_counts

COLOR_A = "#1f77b4"
COLOR_B = "#d62728"
CLUSTER_COLORS = {"HH": "#b2182b", "LL": "#2166ac", "HL": "#f4a582", "LH": "#92c5de", "ns": "#dddddd"}


def _layout(fig: go.Figure, title: str, height: int = 380) -> go.Figure:
    fig.update_layout(title=title, height=height, margin=dict(l=40, r=20, t=50, b=40), template="plotly_white")
    return fig


def count_histogram(df: pd.DataFrame, layer_name: str, length: int, color: str = COLOR_A) -> go.Figure:
    """Histogram of points per occupied cell (one bar per integer count)."""
    counts = cell_counts(df, length)["count"]
    vmax = int(counts.max()) if len(counts) else 1
    nbins = min(vmax, 60)
    fig = px.histogram(counts, nbins=nbins, color_discrete_sequence=[color])
    fig.update_traces(hovertemplate="points per cell=%{x}<br>cells=%{y}<extra></extra>")
    fig.update_layout(showlegend=False, bargap=0.05)
    fig.update_xaxes(title=f"points per {LEVEL_LABEL[length]}")
    fig.update_yaxes(title="number of cells", type="log" if vmax > 20 else "linear")
    return _layout(fig, f"{layer_name}: points per {LEVEL_LABEL[length]} ({len(counts):,} cells)")


def numeric_histogram(df: pd.DataFrame, column: str, layer_name: str, bins: int = 30, color: str = COLOR_A) -> go.Figure:
    series = pd.to_numeric(df[column], errors="coerce").dropna()
    fig = px.histogram(series, nbins=bins, color_discrete_sequence=[color])
    fig.update_layout(showlegend=False, bargap=0.05)
    fig.update_xaxes(title=column)
    fig.update_yaxes(title="count")
    return _layout(fig, f"{layer_name}: distribution of {column} ({len(series):,} values)")


def category_bar(
    df: pd.DataFrame,
    column: str,
    layer_name: str,
    top_n: int = 15,
    color: str = COLOR_A,
    weight: str | None = None,
) -> go.Figure:
    """Bar chart of the most frequent values of a categorical column.

    ``weight`` optionally sums a numeric column per category instead of counting rows.
    """
    series = df[column].fillna("(blank)").astype(str).str.strip().replace("", "(blank)")
    if weight and weight in df.columns:
        vals = pd.to_numeric(df[weight], errors="coerce").fillna(0)
        agg = vals.groupby(series).sum().sort_values(ascending=False)
        ylab = f"sum of {weight}"
    else:
        agg = series.value_counts()
        ylab = "features"
    total_cats = len(agg)
    agg = agg.head(top_n)
    fig = px.bar(x=agg.index, y=agg.values, color_discrete_sequence=[color], text=agg.values)
    fig.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig.update_xaxes(title=column, tickangle=-35)
    fig.update_yaxes(title=ylab)
    suffix = f" (top {top_n} of {total_cats})" if total_cats > top_n else ""
    return _layout(fig, f"{layer_name}: {ylab} by {column}{suffix}", height=460)


def top_cells_bar(df: pd.DataFrame, layer_name: str, length: int, top_n: int = 15, color: str = COLOR_A) -> go.Figure:
    counts = cell_counts(df, length).head(top_n)
    fig = px.bar(counts, x="code", y="count", color_discrete_sequence=[color], text="count")
    fig.update_traces(textposition="outside")
    fig.update_xaxes(title=f"HKGeoCode ({LEVEL_LABEL[length]})", type="category")
    fig.update_yaxes(title="points in cell")
    return _layout(fig, f"{layer_name}: top {top_n} cells")


def ab_scatter(result: CorrelationResult, max_points: int = 20000) -> go.Figure:
    cells = result.cells[(result.cells.a > 0) | (result.cells.b > 0)]
    if len(cells) > max_points:
        cells = cells.sample(max_points, random_state=0)
    jitter = np.random.default_rng(0).uniform(-0.25, 0.25, (len(cells), 2))
    fig = go.Figure(
        go.Scattergl(
            x=cells["a"] + jitter[:, 0],
            y=cells["b"] + jitter[:, 1],
            mode="markers",
            marker=dict(size=5, opacity=0.4, color=[CLUSTER_COLORS.get(c, "#999") for c in cells["cluster"]]),
            text=cells["code"],
            hovertemplate="%{text}<br>A=%{x:.0f} B=%{y:.0f}<extra></extra>",
        )
    )
    fig.update_xaxes(title=f"{result.name_a} per cell")
    fig.update_yaxes(title=f"{result.name_b} per cell")
    return _layout(
        fig,
        f"Per-cell counts, occupied {result.cell_label}s (Pearson r = {result.pearson_r:.2f}; colour = local Moran class)",
        height=420,
    )


def nn_histogram(result: CorrelationResult, direction: str = "a_to_b", max_m: float = 1500) -> go.Figure:
    nn = result.nn_a_to_b if direction == "a_to_b" else result.nn_b_to_a
    src, dst = (result.name_a, result.name_b) if direction == "a_to_b" else (result.name_b, result.name_a)
    d = np.clip(nn["distances"], 0, max_m)
    fig = px.histogram(d, nbins=60, color_discrete_sequence=[COLOR_A if direction == "a_to_b" else COLOR_B])
    for band in NN_BANDS_M:
        fig.add_vline(x=band, line_dash="dot", line_color="#555", annotation_text=f"{band} m")
    fig.add_vline(x=min(nn["median_m"], max_m), line_color="#000", annotation_text="median", annotation_position="top right")
    fig.update_layout(showlegend=False, bargap=0.05)
    fig.update_xaxes(title=f"distance from each {src} to nearest {dst} (m, capped at {max_m:.0f})")
    fig.update_yaxes(title="points")
    return _layout(fig, f"Nearest {dst} from each {src}: median {nn['median_m']:.0f} m (random {nn['random_median_m']:.0f} m)")


def parent_comparison_bar(result: CorrelationResult, top_n: int = 20) -> go.Figure:
    bp = result.by_parent.sort_values("points_a", ascending=False).head(top_n)
    fig = go.Figure(
        [
            go.Bar(name=result.name_a, x=bp["parent"], y=bp["points_a"], marker_color=COLOR_A),
            go.Bar(name=result.name_b, x=bp["parent"], y=bp["points_b"], marker_color=COLOR_B),
        ]
    )
    fig.update_layout(barmode="group")
    fig.update_xaxes(title="2 km district cell", type="category")
    fig.update_yaxes(title="points")
    return _layout(fig, f"Points per 2 km district cell (top {top_n} by {result.name_a})", height=420)


def cluster_bar(result: CorrelationResult) -> go.Figure:
    cc = result.cluster_counts()
    order = ["HH", "HL", "LH", "LL", "ns"]
    fig = go.Figure(
        go.Bar(
            x=order,
            y=[cc.get(k, 0) for k in order],
            marker_color=[CLUSTER_COLORS[k] for k in order],
            text=[cc.get(k, 0) for k in order],
            textposition="outside",
        )
    )
    fig.update_xaxes(title="local bivariate Moran class (A vs lag of B)")
    fig.update_yaxes(title="cells", type="log")
    return _layout(fig, "Local cluster classes")
