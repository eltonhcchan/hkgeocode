"""Tool registry shared by the LLM agent and the rule-based agent.

Each tool is a plain function ``fn(session, **kwargs) -> ToolResult``. The
registry also carries a JSON schema for every tool so the OpenAI-compatible
agent can expose them as function tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from hkgeocode import CELL_SIZE_M

from . import charts, mapping
from .correlation import CorrelationResult, correlate
from .csdi import DEFAULT_LAYERS, CSDIError, LayerData, fetch_layer, search_catalogue
from .grid import LEVEL_LABEL, QuantifySummary, assign_codes, cell_counts, quantify

LAYER_KEYS = ("A", "B")
DEFAULT_LENGTH = 4
MAX_TABLE_ROWS = 25


# --------------------------------------------------------------------------- #
# Results and session state
# --------------------------------------------------------------------------- #
@dataclass
class Artefact:
    kind: str  # "plotly" | "html" | "dataframe" | "markdown"
    payload: Any
    title: str = ""


@dataclass
class ToolResult:
    text: str
    artefacts: list[Artefact] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def add_fig(self, fig, title: str = "") -> "ToolResult":
        self.artefacts.append(Artefact("plotly", fig, title))
        return self

    def add_df(self, df: pd.DataFrame, title: str = "") -> "ToolResult":
        self.artefacts.append(Artefact("dataframe", df, title))
        return self

    def add_html(self, html: str, title: str = "") -> "ToolResult":
        self.artefacts.append(Artefact("html", html, title))
        return self


@dataclass
class LoadedLayer:
    key: str
    name: str
    url: str
    raw: LayerData
    df: pd.DataFrame  # with code2/code4/code6 columns, inside coverage only
    dropped: int

    @property
    def attribute_columns(self) -> list[str]:
        return [c for c in self.df.columns if c not in ("easting", "northing", "code2", "code4", "code6")]

    @property
    def color(self) -> str:
        return charts.COLOR_A if self.key == "A" else charts.COLOR_B


class Session:
    """Analysis state: loaded layers, summaries, correlation results."""

    def __init__(self, output_dir: Path | str = "output"):
        self.layers: dict[str, LoadedLayer] = {}
        self.length: int = DEFAULT_LENGTH
        self.summaries: dict[tuple[str, int], QuantifySummary] = {}
        self.correlations: dict[int, CorrelationResult] = {}
        self.maps: dict[tuple[int, bool], str] = {}
        self.output_dir = Path(output_dir)
        self.permutations = 499
        self.log: list[str] = []

    # -- layers -----------------------------------------------------------
    def load(self, key: str, url: str, name: str | None = None, progress=None) -> LoadedLayer:
        key = key.upper()
        if key not in LAYER_KEYS:
            raise ValueError("layer key must be A or B")
        raw = fetch_layer(url, name=name, progress=progress)
        df, dropped = assign_codes(raw.df)
        layer = LoadedLayer(key=key, name=name or raw.name, url=raw.url, raw=raw, df=df, dropped=dropped)
        self.layers[key] = layer
        self.summaries = {k: v for k, v in self.summaries.items() if k[0] != key}
        self.correlations.clear()
        self.maps.clear()
        self.log.append(f"loaded {key}: {layer.name} ({len(df):,} points)")
        return layer

    def layer(self, key: str) -> LoadedLayer:
        key = (key or "").strip().upper()
        if key in self.layers:
            return self.layers[key]
        # Allow matching by name.
        for lyr in self.layers.values():
            if key and key in lyr.name.upper():
                return lyr
        raise ValueError(f"layer {key!r} is not loaded; loaded layers: {self.layer_list()}")

    def layer_list(self) -> str:
        return ", ".join(f"{k} = {v.name}" for k, v in self.layers.items()) or "none"

    @property
    def ready(self) -> bool:
        return all(k in self.layers for k in LAYER_KEYS)

    # -- analyses -------------------------------------------------------------
    def summary(self, key: str, length: int | None = None) -> QuantifySummary:
        length = self._length(length)
        lyr = self.layer(key)
        cache_key = (lyr.key, length)
        if cache_key not in self.summaries:
            self.summaries[cache_key] = quantify(lyr.df, lyr.name, length, lyr.dropped)
        return self.summaries[cache_key]

    def correlation(self, length: int | None = None) -> CorrelationResult:
        length = self._length(length)
        if not self.ready:
            raise ValueError("both layers A and B must be loaded before correlating")
        if length not in self.correlations:
            a, b = self.layers["A"], self.layers["B"]
            self.correlations[length] = correlate(
                a.df, b.df, a.name, b.name, length=length, permutations=self.permutations
            )
        return self.correlations[length]

    def map_html(self, length: int | None = None, show_points: bool = True) -> str:
        length = self._length(length)
        key = (length, show_points)
        if key not in self.maps:
            result = self.correlation(length)
            a, b = self.layers["A"], self.layers["B"]
            m = mapping.build_map(result, a.df, b.df, show_points=show_points)
            html = mapping.map_html(m)
            self.maps[key] = html
            try:
                mapping.save_map(m, self.output_dir / f"chatbot_map_{length}.html")
                mapping.dump_cells_geojson(result, self.output_dir / f"chatbot_cells_{length}.geojson")
            except OSError:
                pass
        return self.maps[key]

    def _length(self, length: int | None) -> int:
        length = int(length) if length else self.length
        if length not in CELL_SIZE_M:
            raise ValueError("length must be 2, 4 or 6")
        return length

    # -- context for the LLM ----------------------------------------------------
    def context_text(self) -> str:
        if not self.layers:
            return "No layers loaded yet."
        parts = []
        for k, lyr in self.layers.items():
            cols = ", ".join(lyr.attribute_columns[:40])
            parts.append(
                f"Layer {k}: {lyr.name} ({len(lyr.df):,} points, {lyr.dropped} outside coverage). "
                f"Source: {lyr.url}. Attribute fields: {cols}."
            )
        parts.append(f"Current resolution: {LEVEL_LABEL[self.length]} (length {self.length}).")
        if self.correlations:
            parts.append("Correlation already computed for lengths: " + ", ".join(map(str, self.correlations)))
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _df_text(df: pd.DataFrame, max_rows: int = MAX_TABLE_ROWS) -> str:
    return df.head(max_rows).to_string(index=False)


def _find_column(lyr: LoadedLayer, name: str) -> str:
    """Resolve a user/LLM supplied field name case-insensitively and by alias."""
    if not name:
        raise ValueError(f"field name is required; fields of {lyr.name}: {', '.join(lyr.attribute_columns)}")
    cols = lyr.attribute_columns
    lower = {c.lower(): c for c in cols}
    if name.lower() in lower:
        return lower[name.lower()]
    alias = {f["alias"].lower(): f["name"] for f in lyr.raw.fields if f.get("alias")}
    if name.lower() in alias and alias[name.lower()] in cols:
        return alias[name.lower()]
    # Semantic shortcuts for the Wi-Fi.HK layer and similar CSDI geotagging schemas.
    shortcuts = {
        "venue": "SEARCH01_EN",
        "venue type": "SEARCH01_EN",
        "type": "SEARCH01_EN",
        "category": "SEARCH01_EN",
        "district": "SEARCH02_EN",
        "area": "SEARCH02_EN",
        "provider": "SEARCH03_EN",
        "operator": "SEARCH03_EN",
        "service": "SEARCH03_EN",
        "hotspots": "NSEARCH06_EN",
        "number of hotspots": "NSEARCH06_EN",
        "name": "NAME_EN",
    }
    if name.lower() in shortcuts and shortcuts[name.lower()] in cols:
        return shortcuts[name.lower()]
    partial = [c for c in cols if name.lower() in c.lower()]
    if len(partial) == 1:
        return partial[0]
    raise ValueError(f"unknown field {name!r} for {lyr.name}; available: {', '.join(cols)}")


def _is_categorical(series: pd.Series) -> bool:
    if series.dtype == object:
        return True
    return series.nunique(dropna=True) <= 30


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def tool_search_csdi(session: Session, keyword: str, max_results: int = 10) -> ToolResult:
    items = search_catalogue(keyword, max_results=max_results)
    if not items:
        return ToolResult(f"No CSDI point layers found for {keyword!r}.")
    rows = [
        {"dataset_id": it.dataset_id, "title": it.title, "point_layers": ", ".join(n for _, n in it.point_layers), "layer_url": it.layer_url}
        for it in items
    ]
    df = pd.DataFrame(rows)
    text = f"CSDI point layers matching {keyword!r}:\n" + "\n".join(
        f"- {r['title']} [{r['point_layers']}] -> {r['layer_url']}" for r in rows
    )
    return ToolResult(text, data={"items": rows}).add_df(df, "CSDI search results")


def tool_load_layers(
    session: Session,
    url_a: str | None = None,
    url_b: str | None = None,
    name_a: str | None = None,
    name_b: str | None = None,
) -> ToolResult:
    msgs = []
    for key, url, name in (("A", url_a, name_a), ("B", url_b, name_b)):
        if not url:
            continue
        try:
            lyr = session.load(key, url, name)
        except (CSDIError, ValueError) as exc:
            msgs.append(f"Layer {key}: failed to load {url}: {exc}")
            continue
        msgs.append(f"Layer {key} = {lyr.name}: {len(lyr.df):,} points loaded ({lyr.dropped} outside coverage).")
    if not msgs:
        msgs.append("No URL given. Default layers: " + "; ".join(f"{k}: {v}" for k, v in DEFAULT_LAYERS.items()))
    return ToolResult("\n".join(msgs))


def tool_quantify(session: Session, layer: str = "both", length: int | None = None) -> ToolResult:
    length = session._length(length)
    keys = list(session.layers) if layer.lower() in ("both", "all", "") else [session.layer(layer).key]
    texts = []
    res = ToolResult("")
    for key in keys:
        s = session.summary(key, length)
        lyr = session.layers[key]
        texts.append(s.to_text())
        res.add_fig(charts.count_histogram(lyr.df, lyr.name, length, lyr.color))
        res.add_df(s.distribution.rename(columns={"points_in_cell": "points in cell", "cells": "number of cells"}), f"{lyr.name}: cells by count")
    res.text = "\n\n".join(texts)
    return res


def tool_correlate(session: Session, length: int | None = None) -> ToolResult:
    r = session.correlation(length)
    res = ToolResult(r.to_text() + "\n\nInterpretation: " + r.interpret())
    res.add_fig(charts.ab_scatter(r))
    res.add_fig(charts.nn_histogram(r, "a_to_b"))
    res.add_fig(charts.cluster_bar(r))
    stats = pd.DataFrame(
        [
            ("Pearson r (occupied cells)", f"{r.pearson_r:.3f}", f"p={r.pearson_p:.2g}"),
            ("Spearman rho (occupied cells)", f"{r.spearman_rho:.3f}", f"p={r.spearman_p:.2g}"),
        ]
        + (
            [
                ("Pearson r (study area, zeros incl.)", f"{r.study_area['pearson_r']:.3f}", f"p={r.study_area['pearson_p']:.2g}"),
                ("Spearman rho (study area, zeros incl.)", f"{r.study_area['spearman_rho']:.3f}", f"p={r.study_area['spearman_p']:.2g}"),
            ]
            if r.study_area.get("available")
            else []
        )
        + [
            ("Jaccard overlap of occupied cells", f"{r.jaccard:.3f}", f"{r.cells_both:,} shared / {r.cells_union:,} union"),
            ("Bivariate Moran's I", f"{r.moran['I']:.3f}", f"pseudo p={r.moran['p_value']:.3f}, z={r.moran['z_score']:.1f}"),
            ("Median nearest B from A", f"{r.nn_a_to_b['median_m']:.0f} m", f"random {r.nn_a_to_b['random_median_m']:.0f} m"),
            ("Median nearest A from B", f"{r.nn_b_to_a['median_m']:.0f} m", f"random {r.nn_b_to_a['random_median_m']:.0f} m"),
        ],
        columns=["measure", "value", "note"],
    )
    res.add_df(stats, "Correlation measures")
    return res


def tool_describe_dataset(session: Session, layer: str) -> ToolResult:
    lyr = session.layer(layer)
    rows = []
    for col in lyr.attribute_columns:
        s = lyr.df[col]
        non_null = s.notna() & (s.astype(str).str.strip() != "")
        sample = s[non_null].astype(str).head(3).tolist()
        rows.append(
            {
                "field": col,
                "type": str(s.dtype),
                "non_empty": int(non_null.sum()),
                "unique": int(s.nunique(dropna=True)),
                "sample": " | ".join(x[:40] for x in sample),
            }
        )
    df = pd.DataFrame(rows)
    text = (
        f"Layer {lyr.key} = {lyr.name}: {len(lyr.df):,} points (EPSG:2326), source {lyr.url}.\n"
        f"Fields ({len(rows)}):\n" + _df_text(df, 60)
    )
    return ToolResult(text).add_df(df, f"{lyr.name}: fields")


def tool_field_values(session: Session, layer: str, field: str, top_n: int = 15, contains: str | None = None) -> ToolResult:
    lyr = session.layer(layer)
    col = _find_column(lyr, field)
    df = lyr.df
    if contains:
        mask = df[col].astype(str).str.contains(contains, case=False, na=False)
        df = df[mask]
    vc = df[col].fillna("(blank)").astype(str).str.strip().replace("", "(blank)").value_counts()
    table = vc.head(top_n).rename_axis(col).reset_index(name="features")
    text = (
        f"{lyr.name}: {col} has {len(vc):,} distinct values over {len(df):,} features"
        + (f" containing {contains!r}" if contains else "")
        + ".\n"
        + _df_text(table)
    )
    return ToolResult(text, data={"values": table.to_dict("records")}).add_df(table, f"{lyr.name}: {col}")


def tool_query_layer(session: Session, layer: str, field: str, contains: str, length: int | None = None) -> ToolResult:
    """Filter a layer by substring match on a field and summarise the subset."""
    lyr = session.layer(layer)
    col = _find_column(lyr, field)
    length = session._length(length)
    mask = lyr.df[col].astype(str).str.contains(contains, case=False, na=False)
    sub = lyr.df[mask]
    if sub.empty:
        return ToolResult(f"No {lyr.name} features where {col} contains {contains!r}.")
    counts = cell_counts(sub, length)
    name_col = next((c for c in ("NAME_EN", "NAME", "STOP_ID") if c in sub.columns), None)
    show_cols = [c for c in (name_col, col, f"code{length}") if c]
    text = (
        f"{len(sub):,} of {len(lyr.df):,} {lyr.name} features have {col} containing {contains!r}; "
        f"they occupy {len(counts):,} {LEVEL_LABEL[length]}s (max {int(counts['count'].max())} in {counts.iloc[0].code}).\n"
        + _df_text(sub[show_cols], 15)
    )
    res = ToolResult(text, data={"count": int(len(sub))})
    res.add_df(sub[show_cols].head(200), f"{lyr.name} where {col} contains {contains!r}")
    res.add_df(counts.head(15)[["code", "count"]], "Top cells for the subset")
    return res


def tool_histogram(
    session: Session,
    layer: str = "both",
    field: str | None = None,
    length: int | None = None,
    bins: int = 30,
) -> ToolResult:
    length = session._length(length)
    keys = list(session.layers) if layer.lower() in ("both", "all", "") else [session.layer(layer).key]
    res = ToolResult("")
    texts = []
    for key in keys:
        lyr = session.layers[key]
        if field:
            col = _find_column(lyr, field)
            if _is_categorical(lyr.df[col]):
                res.add_fig(charts.category_bar(lyr.df, col, lyr.name, color=lyr.color))
                texts.append(f"{col} in {lyr.name} is categorical, so a bar chart of value frequencies is shown instead.")
            else:
                res.add_fig(charts.numeric_histogram(lyr.df, col, lyr.name, bins, lyr.color))
                s = pd.to_numeric(lyr.df[col], errors="coerce").dropna()
                texts.append(
                    f"{lyr.name}: {col} over {len(s):,} values: min {s.min():g}, median {s.median():g}, "
                    f"mean {s.mean():.2f}, max {s.max():g}, total {s.sum():g}."
                )
        else:
            s = session.summary(key, length)
            res.add_fig(charts.count_histogram(lyr.df, lyr.name, length, lyr.color))
            texts.append(s.to_text())
    res.text = "\n\n".join(texts)
    return res


def tool_bar_chart(
    session: Session,
    layer: str,
    field: str,
    top_n: int = 15,
    weight: str | None = None,
) -> ToolResult:
    lyr = session.layer(layer)
    col = _find_column(lyr, field)
    wcol = _find_column(lyr, weight) if weight else None
    fig = charts.category_bar(lyr.df, col, lyr.name, top_n=top_n, color=lyr.color, weight=wcol)
    values = tool_field_values(session, lyr.key, col, top_n)
    if wcol:
        agg = (
            pd.to_numeric(lyr.df[wcol], errors="coerce").fillna(0).groupby(lyr.df[col].fillna("(blank)").astype(str)).sum()
            .sort_values(ascending=False).head(top_n).rename(f"sum of {wcol}").rename_axis(col).reset_index()
        )
        text = f"{lyr.name}: sum of {wcol} by {col} (top {top_n}):\n" + _df_text(agg)
        return ToolResult(text).add_fig(fig).add_df(agg, f"{lyr.name}: {wcol} by {col}")
    return ToolResult(values.text).add_fig(fig).add_df(values.artefacts[0].payload, f"{lyr.name}: {col}")


def tool_top_cells(session: Session, layer: str = "both", length: int | None = None, top_n: int = 10) -> ToolResult:
    length = session._length(length)
    keys = list(session.layers) if layer.lower() in ("both", "all", "") else [session.layer(layer).key]
    res = ToolResult("")
    texts = []
    for key in keys:
        lyr = session.layers[key]
        counts = cell_counts(lyr.df, length).head(top_n)
        table = counts[["code", "count"]].copy()
        name_col = next((c for c in ("NAME_EN", "NAME") if c in lyr.df.columns), None)
        if name_col:
            names = lyr.df.groupby(f"code{length}")[name_col].agg(lambda s: "; ".join(s.astype(str).head(3)))
            table["examples"] = table["code"].map(names)
        texts.append(f"{lyr.name}: top {top_n} {LEVEL_LABEL[length]}s:\n" + _df_text(table))
        res.add_fig(charts.top_cells_bar(lyr.df, lyr.name, length, top_n, lyr.color))
        res.add_df(table, f"{lyr.name}: top cells")
    res.text = "\n\n".join(texts)
    return res


def tool_compare_cells(session: Session, kind: str = "A only", length: int | None = None, top_n: int = 15) -> ToolResult:
    """List cells by co-location class: 'A only', 'B only' or 'both'."""
    r = session.correlation(length)
    kind_norm = kind.strip().lower().replace("_", " ")
    mapping_ = {"a only": "A only", "b only": "B only", "both": "both", "a": "A only", "b": "B only", "shared": "both"}
    cls = mapping_.get(kind_norm)
    if cls is None:
        raise ValueError("kind must be 'A only', 'B only' or 'both'")
    cells = r.cells[r.cells.cls == cls]
    label = {"A only": f"{r.name_a} but no {r.name_b}", "B only": f"{r.name_b} but no {r.name_a}", "both": "both layers"}[cls]
    sort_col = "b" if cls == "B only" else "a"
    top = cells.sort_values([sort_col, "a", "b"], ascending=False).head(top_n)
    table = top[["code", "a", "b", "cluster", "parent"]].rename(columns={"a": r.name_a, "b": r.name_b, "parent": "2 km cell"})
    text = f"{len(cells):,} {r.cell_label}s contain {label}. Top {top_n} by count:\n" + _df_text(table)
    res = ToolResult(text, data={"n_cells": int(len(cells))})
    res.add_df(table, f"Cells with {label}")
    if r.length > 2:
        by_parent = cells.groupby("parent").size().sort_values(ascending=False).head(10).rename("cells").reset_index()
        res.text += "\n\n2 km district cells with the most such cells:\n" + _df_text(by_parent)
        res.add_df(by_parent, "By 2 km district cell")
    return res


def tool_show_map(session: Session, length: int | None = None, show_points: bool = True) -> ToolResult:
    length = session._length(length)
    html = session.map_html(length, show_points)
    r = session.correlation(length)
    cc = r.cluster_counts()
    text = (
        f"Map of {r.name_a} (blue) and {r.name_b} (red) on {r.cell_label}s; purple cells hold both. "
        f"Toggle the local Moran cluster layer (HH {cc.get('HH', 0):,}, LH {cc.get('LH', 0):,}, HL {cc.get('HL', 0):,}) "
        f"and the point layers in the layer control. Saved to {session.output_dir / f'chatbot_map_{length}.html'}."
    )
    return ToolResult(text).add_html(html, "Map")


def tool_cell_lookup(session: Session, code: str) -> ToolResult:
    code = code.strip().upper()
    if len(code) not in CELL_SIZE_M:
        raise ValueError("HKGeoCode must be 2, 4 or 6 characters")
    col = f"code{len(code)}"
    texts = [f"HKGeoCode {code} ({LEVEL_LABEL[len(code)]}):"]
    res = ToolResult("")
    for key, lyr in session.layers.items():
        sub = lyr.df[lyr.df[col] == code]
        texts.append(f"- {lyr.name}: {len(sub):,} points")
        if len(sub):
            show = [c for c in ("NAME_EN", "ADDRESS_EN", "SEARCH01_EN", "SEARCH02_EN", "STOP_ID") if c in sub.columns]
            if show:
                texts.append(_df_text(sub[show], 10))
                res.add_df(sub[show].head(50), f"{lyr.name} in {code}")
    res.text = "\n".join(texts)
    return res


def tool_insights(session: Session, length: int | None = None, top_n: int = 8) -> ToolResult:
    r = session.correlation(length)
    a, b = session.layers["A"], session.layers["B"]
    cells = r.cells
    a_only = cells[cells.cls == "A only"].sort_values("a", ascending=False).head(top_n)
    b_only = cells[cells.cls == "B only"].sort_values("b", ascending=False).head(top_n)
    bp = r.by_parent.copy()
    bp["share_shared"] = bp["cells_both"] / bp[["cells_a", "cells_b"]].max(axis=1).clip(lower=1)
    best = bp.sort_values("cells_both", ascending=False).head(5)
    imbalanced = bp[(bp.points_a >= 20)].assign(ratio=lambda d: d.points_b / d.points_a.clip(lower=1)).sort_values("ratio").head(5)

    lines = [r.interpret(), "", "Suggested actions:"]
    lines.append(
        f"1. Gap filling: {len(cells[cells.cls == 'A only']):,} {r.cell_label}s have {a.name} but no {b.name}. "
        "The busiest of them are " + ", ".join(f"{c.code} ({int(c.a)} {a.name})" for c in a_only.itertuples()) + "."
    )
    lines.append(
        f"2. Reverse gaps: {len(cells[cells.cls == 'B only']):,} cells have {b.name} but no {a.name}; "
        "largest: " + ", ".join(f"{c.code} ({int(c.b)})" for c in b_only.itertuples()) + "."
    )
    lines.append(
        "3. Shared hotspots to prioritise for joint services or capacity checks: "
        + ", ".join(f"{p.parent} ({int(p.cells_both)} shared cells, {int(p.hh)} HH)" for p in best.itertuples())
        + "."
    )
    if len(imbalanced):
        lines.append(
            f"4. Under-served districts (many {a.name}, few {b.name}): "
            + ", ".join(f"{p.parent} ({int(p.points_a)} vs {int(p.points_b)})" for p in imbalanced.itertuples())
            + "."
        )
    lines.append(
        "5. Next analytical steps: repeat at another resolution (2 km for district policy, 5 m for site design), "
        "add a population or employment layer from CSDI as a denominator, and test distance bands "
        "(e.g. share of A within 200 m of B) by venue type or operator."
    )
    res = ToolResult("\n".join(lines))
    res.add_fig(charts.parent_comparison_bar(r))
    res.add_df(
        bp.head(15).rename(columns={"parent": "2 km cell", "cells_a": f"cells {a.name}", "cells_b": f"cells {b.name}", "cells_both": "shared", "points_a": a.name, "points_b": b.name, "hh": "HH cells", "share_shared": "share shared"}),
        "2 km district cells ranked by shared cells",
    )
    res.add_df(a_only[["code", "a", "parent"]].rename(columns={"a": a.name, "parent": "2 km cell"}), f"Busiest {a.name} cells without {b.name}")
    return res


def tool_set_resolution(session: Session, length: int) -> ToolResult:
    length = session._length(length)
    session.length = length
    return ToolResult(f"Resolution set to {LEVEL_LABEL[length]} (HKGeoCode length {length}).")


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., ToolResult]

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _params(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


LAYER_PARAM = {"type": "string", "description": "Layer key: 'A', 'B', or 'both'. Layer names are also accepted."}
LENGTH_PARAM = {
    "type": "integer",
    "enum": [2, 4, 6],
    "description": "HKGeoCode length / resolution: 2 = 2 km district cell, 4 = 100 m neighbourhood cell, 6 = 5 m cell. Omit to use the current resolution.",
}

TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "search_csdi",
            "Search the Hong Kong CSDI portal catalogue for datasets that expose a point FeatureServer layer.",
            _params({"keyword": {"type": "string"}, "max_results": {"type": "integer", "default": 10}}, ["keyword"]),
            tool_search_csdi,
        ),
        Tool(
            "load_layers",
            "Download point layers from CSDI ArcGIS FeatureServer URLs into slots A and/or B (this replaces the current layer and resets results).",
            _params(
                {
                    "url_a": {"type": "string", "description": "FeatureServer layer URL for layer A"},
                    "url_b": {"type": "string", "description": "FeatureServer layer URL for layer B"},
                    "name_a": {"type": "string"},
                    "name_b": {"type": "string"},
                }
            ),
            tool_load_layers,
        ),
        Tool(
            "quantify",
            "Bin a layer's points onto HKGeoCode cells and report the count distribution (points, occupied cells, max/mean per cell, top cells) with a histogram.",
            _params({"layer": LAYER_PARAM, "length": LENGTH_PARAM}),
            tool_quantify,
        ),
        Tool(
            "correlate",
            "Compute spatial correlation between layers A and B: per-cell Pearson/Spearman, Jaccard overlap, bivariate Moran's I with local clusters, nearest-neighbour distances vs a random baseline, plus an interpretation.",
            _params({"length": LENGTH_PARAM}),
            tool_correlate,
        ),
        Tool(
            "describe_dataset",
            "List the attribute fields of a layer with types, fill rates, unique counts and sample values.",
            _params({"layer": LAYER_PARAM}, ["layer"]),
            tool_describe_dataset,
        ),
        Tool(
            "field_values",
            "Frequency table of the values of one attribute field (e.g. district, venue type, operator). Use to answer 'how many ... by ...' questions.",
            _params(
                {
                    "layer": LAYER_PARAM,
                    "field": {"type": "string", "description": "field name; shortcuts: district, venue type, provider, hotspots, name"},
                    "top_n": {"type": "integer", "default": 15},
                    "contains": {"type": "string", "description": "optional case-insensitive substring filter on the field"},
                },
                ["layer", "field"],
            ),
            tool_field_values,
        ),
        Tool(
            "query_layer",
            "Filter a layer where a field contains a substring (e.g. district contains 'Yuen Long') and summarise the subset: count, sample rows, cells.",
            _params(
                {"layer": LAYER_PARAM, "field": {"type": "string"}, "contains": {"type": "string"}, "length": LENGTH_PARAM},
                ["layer", "field", "contains"],
            ),
            tool_query_layer,
        ),
        Tool(
            "histogram",
            "Histogram. Without 'field': points per HKGeoCode cell for the layer(s). With a numeric 'field': distribution of that field (categorical fields fall back to a bar chart).",
            _params({"layer": LAYER_PARAM, "field": {"type": "string"}, "length": LENGTH_PARAM, "bins": {"type": "integer", "default": 30}}),
            tool_histogram,
        ),
        Tool(
            "bar_chart",
            "Bar chart of feature counts by a categorical field (top N), optionally weighted by summing a numeric field.",
            _params(
                {
                    "layer": LAYER_PARAM,
                    "field": {"type": "string"},
                    "top_n": {"type": "integer", "default": 15},
                    "weight": {"type": "string", "description": "numeric field to sum instead of counting rows"},
                },
                ["layer", "field"],
            ),
            tool_bar_chart,
        ),
        Tool(
            "top_cells",
            "Cells with the most points for a layer, with a bar chart.",
            _params({"layer": LAYER_PARAM, "length": LENGTH_PARAM, "top_n": {"type": "integer", "default": 10}}),
            tool_top_cells,
        ),
        Tool(
            "compare_cells",
            "List cells by co-location class: cells with A only (A but no B), B only, or both. Answers 'which cells have X but no Y'.",
            _params(
                {"kind": {"type": "string", "enum": ["A only", "B only", "both"]}, "length": LENGTH_PARAM, "top_n": {"type": "integer", "default": 15}},
                ["kind"],
            ),
            tool_compare_cells,
        ),
        Tool(
            "show_map",
            "Render the interactive map of both layers on HKGeoCode cells with co-location classes and local Moran clusters.",
            _params({"length": LENGTH_PARAM, "show_points": {"type": "boolean", "default": True}}),
            tool_show_map,
        ),
        Tool(
            "cell_lookup",
            "What is inside a given HKGeoCode cell (2, 4 or 6 characters) for each loaded layer.",
            _params({"code": {"type": "string"}}, ["code"]),
            tool_cell_lookup,
        ),
        Tool(
            "insights",
            "Interpretation of the spatial relationship plus suggested actions: gap cells, shared hotspots, under-served districts, next analytical steps.",
            _params({"length": LENGTH_PARAM, "top_n": {"type": "integer", "default": 8}}),
            tool_insights,
        ),
        Tool(
            "set_resolution",
            "Change the default HKGeoCode resolution used by later tools.",
            _params({"length": LENGTH_PARAM}, ["length"]),
            tool_set_resolution,
        ),
    ]
}


def run_tool(session: Session, name: str, arguments: dict | str | None) -> ToolResult:
    """Execute a registered tool, converting any error into a ToolResult."""
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult(f"Unknown tool {name!r}. Available: {', '.join(TOOLS)}")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return ToolResult(f"Could not parse arguments for {name}: {exc}")
    arguments = {k: v for k, v in (arguments or {}).items() if v is not None}
    try:
        return tool.fn(session, **arguments)
    except (ValueError, KeyError, CSDIError) as exc:
        return ToolResult(f"{name} failed: {exc}")
    except TypeError as exc:
        return ToolResult(f"{name} got invalid arguments {arguments}: {exc}")


def openai_tool_schemas() -> list[dict]:
    return [t.openai_schema() for t in TOOLS.values()]


def parse_length(text: str) -> int | None:
    """Pick a resolution out of free text ('2 km', '100 m', '5 m', 'district')."""
    t = text.lower()
    if re.search(r"\b2\s*km\b|district", t):
        return 2
    if re.search(r"\b100\s*m\b|neighbou?rhood", t):
        return 4
    if re.search(r"\b5\s*m\b|standard cell", t):
        return 6
    return None
