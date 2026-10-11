"""Folium map of HKGeoCode cells: co-location classes and local Moran clusters."""

from __future__ import annotations

import json
from pathlib import Path

import folium
import numpy as np
import pandas as pd
from branca.element import MacroElement, Template
from folium.plugins import FastMarkerCluster
from pyproj import Transformer

from hkgeocode import CELL_SIZE_M, hkgeocode_cell_origin

from .charts import CLUSTER_COLORS, COLOR_A, COLOR_B
from .correlation import CorrelationResult

_TO_LL = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)

LANDSD_BASEMAP = "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/basemap/wgs84/{z}/{x}/{y}.png"
LANDSD_ATTR = (
    '<a href="https://api.portal.hkmapservice.gov.hk/disclaimer" target="_blank">Map from Lands Department</a>'
)

CLASS_COLORS = {"A only": COLOR_A, "B only": COLOR_B, "both": "#6a3d9a", "empty": "#ffffff"}
CLUSTER_LABELS = {
    "HH": "HH: high A, high B around",
    "LL": "LL: low A, low B around",
    "HL": "HL: high A, low B around",
    "LH": "LH: low A, high B around",
}

LEGEND_TEMPLATE = """
{% macro html(this, kwargs) %}
<div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white;
            padding: 10px 12px; border: 1px solid #999; border-radius: 4px; font: 12px sans-serif;
            max-width: 300px; line-height: 1.4;">
  <b>{{ this.title }}</b><br>
  {% for label, color in this.items %}
  <span style="display:inline-block;width:12px;height:12px;background:{{ color }};border:1px solid #666;margin-right:6px;vertical-align:middle;"></span>{{ label }}<br>
  {% endfor %}
  <div style="margin-top:6px;color:#333">{{ this.note }}</div>
</div>
{% endmacro %}
"""


class Legend(MacroElement):
    def __init__(self, title: str, items: list[tuple[str, str]], note: str = ""):
        super().__init__()
        self._name = "Legend"
        self.title = title
        self.items = items
        self.note = note
        self._template = Template(LEGEND_TEMPLATE)


def cells_geojson(cells: pd.DataFrame, cell_size: float, color_fn, props: list[str]) -> dict:
    """Build a GeoJSON FeatureCollection of square cells in WGS84."""
    e = cells["easting"].to_numpy(float) - cell_size / 2
    n = cells["northing"].to_numpy(float) - cell_size / 2
    corners = []
    for de, dn in ((0, 0), (cell_size, 0), (cell_size, cell_size), (0, cell_size)):
        lon, lat = _TO_LL.transform(e + de, n + dn)
        corners.append((lon, lat))
    features = []
    for i, rec in enumerate(cells.itertuples(index=False)):
        ring = [[float(corners[k][0][i]), float(corners[k][1][i])] for k in range(4)]
        ring.append(ring[0])
        p = {k: _json_safe(getattr(rec, k)) for k in props}
        p["color"] = color_fn(rec)
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": p})
    return {"type": "FeatureCollection", "features": features}


def _json_safe(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return round(float(v), 4)
    return v


def _parent_centre(code: str) -> tuple[float, float]:
    e, n, size = hkgeocode_cell_origin(code)
    return e + size / 2, n + size / 2


def _ramp(t: float) -> str:
    """Light yellow to dark purple (ColorBrewer-like) for t in [0, 1]."""
    stops = [(255, 255, 204), (253, 187, 132), (227, 74, 51), (106, 61, 154)]
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    r, g, b = (round(stops[i][k] + (stops[i + 1][k] - stops[i][k]) * f) for k in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


def _bounds(cells: pd.DataFrame, cell_size: float) -> list[list[float]]:
    lon0, lat0 = _TO_LL.transform(cells.easting.min() - cell_size, cells.northing.min() - cell_size)
    lon1, lat1 = _TO_LL.transform(cells.easting.max() + cell_size, cells.northing.max() + cell_size)
    return [[float(lat0), float(lon0)], [float(lat1), float(lon1)]]


def build_map(
    result: CorrelationResult,
    df_a: pd.DataFrame | None = None,
    df_b: pd.DataFrame | None = None,
    show_points: bool = True,
    max_cells: int = 15000,
) -> folium.Map:
    size = CELL_SIZE_M[result.length]
    cells = result.cells
    occupied = cells[(cells.a > 0) | (cells.b > 0)]
    m = folium.Map(location=[22.35, 114.15], zoom_start=11, tiles=None, control_scale=True)
    # Leaflet shows the last base layer added, so the default (grey OSM) goes last.
    folium.TileLayer(
        LANDSD_BASEMAP,
        name="Lands Department topographic map",
        attr=LANDSD_ATTR,
        max_zoom=20,
    ).add_to(m)
    folium.TileLayer("openstreetmap", name="OpenStreetMap (colour)").add_to(m)
    folium.map.CustomPane("grey", z_index=200).add_to(m)
    folium.TileLayer("openstreetmap", name="OpenStreetMap (grey)", pane="grey").add_to(m)
    m.get_root().header.add_child(folium.Element("<style>.leaflet-grey-pane { filter: grayscale(100%); }</style>"))

    # Layer 0 (fine resolutions only): 2 km overview of shared cells so the
    # territory-wide picture is readable before zooming in.
    if result.length > 2 and len(result.by_parent):
        bp = result.by_parent.copy()
        origins = np.array([_parent_centre(p) for p in bp["parent"]])
        bp["easting"], bp["northing"] = origins[:, 0], origins[:, 1]
        vmax = max(1, int(bp["cells_both"].max()))

        def shade(r):
            t = r.cells_both / vmax
            if r.cells_both == 0:
                return "#f0f0f0"
            return _ramp(t)

        gj0 = cells_geojson(bp, 2000.0, shade, ["parent", "cells_a", "cells_b", "cells_both", "points_a", "points_b", "hh"])
        folium.GeoJson(
            gj0,
            name="2 km overview: shared cells per district cell",
            style_function=lambda f: {
                "fillColor": f["properties"]["color"],
                "color": "#888",
                "weight": 0.6,
                "fillOpacity": 0.45,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["parent", "cells_a", "cells_b", "cells_both", "points_a", "points_b", "hh"],
                aliases=[
                    "2 km cell",
                    f"cells with {result.name_a}",
                    f"cells with {result.name_b}",
                    "cells with both",
                    f"{result.name_a} points",
                    f"{result.name_b} points",
                    "HH cells",
                ],
            ),
        ).add_to(m)

    # Layer 1: co-location classes on occupied cells.
    occ = occupied if len(occupied) <= max_cells else occupied.nlargest(max_cells, ["a", "b"])
    gj = cells_geojson(occ, size, lambda r: CLASS_COLORS[r.cls], ["code", "a", "b", "cls"])
    folium.GeoJson(
        gj,
        name=f"Co-location ({result.cell_label})",
        style_function=lambda f: {
            "fillColor": f["properties"]["color"],
            "color": f["properties"]["color"],
            "weight": 0.5,
            "fillOpacity": 0.6,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["code", "a", "b", "cls"],
            aliases=["HKGeoCode", result.name_a, result.name_b, "class"],
        ),
    ).add_to(m)

    # Layer 2: significant local Moran clusters (skip LL at fine resolution: mostly empty land).
    sig = cells[cells.cluster != "ns"]
    if result.length > 2:
        sig = sig[sig.cluster != "LL"]
    if len(sig) > max_cells:
        sig = sig.nsmallest(max_cells, "local_p")
    if len(sig):
        gj2 = cells_geojson(
            sig, size, lambda r: CLUSTER_COLORS[r.cluster], ["code", "a", "b", "cluster", "local_I", "local_p"]
        )
        folium.GeoJson(
            gj2,
            name="Local bivariate Moran clusters (p <= 0.05)",
            show=result.length == 2,
            style_function=lambda f: {
                "fillColor": f["properties"]["color"],
                "color": "#333",
                "weight": 0.4,
                "fillOpacity": 0.55,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["code", "a", "b", "cluster", "local_I", "local_p"],
                aliases=["HKGeoCode", result.name_a, result.name_b, "cluster", "local I", "pseudo p"],
            ),
        ).add_to(m)

    # Layer 3: points.
    if show_points and df_a is not None and df_b is not None:
        for df, name, color in ((df_a, result.name_a, COLOR_A), (df_b, result.name_b, COLOR_B)):
            lon, lat = _TO_LL.transform(df["easting"].to_numpy(float), df["northing"].to_numpy(float))
            pts = np.column_stack([lat, lon]).tolist()
            # FastMarkerCluster ignores show=False, so wrap it in a hidden FeatureGroup.
            group = folium.FeatureGroup(name=f"{name} points ({len(df):,})", show=False)
            FastMarkerCluster(
                pts,
                options={"disableClusteringAtZoom": 16, "maxClusterRadius": 40},
                control=False,
            ).add_to(group)
            group.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    legend_items = [
        (f"{result.name_a} only", CLASS_COLORS["A only"]),
        (f"{result.name_b} only", CLASS_COLORS["B only"]),
        ("both", CLASS_COLORS["both"]),
    ] + [(CLUSTER_LABELS[k], CLUSTER_COLORS[k]) for k in ("HH", "HL", "LH", "LL")]
    cc = result.cluster_counts()
    note = (
        f"{result.cell_label}s: A {result.cells_a:,}, B {result.cells_b:,}, both {result.cells_both:,} "
        f"(Jaccard {result.jaccard:.2f}). Moran's I {result.moran['I']:.2f} (p {result.moran['p_value']:.3f}). "
        f"HH {cc.get('HH', 0):,}, LH {cc.get('LH', 0):,}, HL {cc.get('HL', 0):,}."
    )
    Legend(f"{result.name_a} (A) vs {result.name_b} (B)", legend_items, note).add_to(m)
    m.fit_bounds(_bounds(occupied, size))
    return m


def map_html(m: folium.Map) -> str:
    return m.get_root().render()


def save_map(m: folium.Map, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(path))
    return path


def dump_cells_geojson(result: CorrelationResult, path: Path) -> Path:
    """Write occupied cells with counts and cluster classes as GeoJSON (WGS84)."""
    size = CELL_SIZE_M[result.length]
    cells = result.cells[(result.cells.a > 0) | (result.cells.b > 0)]
    gj = cells_geojson(cells, size, lambda r: CLASS_COLORS[r.cls], ["code", "a", "b", "cls", "cluster", "local_I", "local_p"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gj), encoding="utf-8")
    return path
