#!/usr/bin/env python3
"""Bin vector points onto the HKGeoCode 100 m grid and write a GeoTIFF.

Default cell size is the four-character HKGeoCode neighbourhood cell
(100 m × 100 m) on Hong Kong 1980 Grid (EPSG:2326). Pixel (0, 0) is the
north-west corner. Empty cells are nodata 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import fiona
import numpy as np
import requests
import tifffile
from PIL import Image
from pyproj import Transformer

from hkgeocode import (
    CELL_SIZE_M,
    COVERAGE_EASTING,
    COVERAGE_NORTHING,
    HKGeoCodeError,
    ORIGIN_EASTING,
    ORIGIN_NORTHING,
    coverage_grid_shape,
    hk80_to_grid_index,
    hk80_to_hkgeocode,
)

CSDI_BUS_STOPS = (
    "https://portal.csdi.gov.hk/server/rest/services/common/"
    "td_rcd_1638874475129_49745/FeatureServer"
)
NODATA = 0
EPSG = 2326
PAGE_SIZE = 3000
USER_AGENT = "hkgeocode/0.1"

_ARCGIS_SERVICE = re.compile(
    r"(?P<base>.+/(?:FeatureServer|MapServer))(?:/(?P<layer>\d+))?/?$",
    re.IGNORECASE,
)
_FIONA_POINT = {
    "Point",
    "3D Point",
    "PointZ",
    "Point25D",
    "MultiPoint",
    "3D MultiPoint",
    "MultiPointZ",
    "MultiPoint25D",
}
_ESRI_POINT = {"esriGeometryPoint", "esriGeometryMultipoint"}


class PointLayerError(ValueError):
    """Raised when the source is not exactly one point feature layer."""


def _http_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _arcgis_json(session: requests.Session, url: str) -> dict:
    resp = session.get(url, params={"f": "json"}, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        raise PointLayerError(payload["error"])
    return payload


def _parse_arcgis_url(url: str) -> tuple[str, int | None]:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    match = _ARCGIS_SERVICE.search(path)
    if not match:
        raise PointLayerError(
            f"{url!r} is not an ArcGIS FeatureServer or MapServer URL"
        )
    layer_id = int(match.group("layer")) if match.group("layer") is not None else None
    service = urlunparse(parsed._replace(path=match.group("base"), query="", fragment=""))
    return service.rstrip("/"), layer_id


def _require_single_point_layer(layers: list[tuple[str, str]], source: str) -> None:
    """layers is a list of (name, geometry_label) spatial feature layers."""
    point_layers = [
        name
        for name, geom in layers
        if geom in _ESRI_POINT or geom in _FIONA_POINT
    ]
    listing = ", ".join(f"{name} ({geom})" for name, geom in layers) or "none"
    if len(layers) != 1 or len(point_layers) != 1:
        raise PointLayerError(
            f"{source} must contain a single point feature layer; "
            f"found {len(layers)} feature layer(s): {listing}"
        )


def _resolve_arcgis_layer_url(url: str) -> str:
    session = _http_session()
    service_url, layer_id = _parse_arcgis_url(url)
    service = _arcgis_json(session, service_url)
    spatial = []
    for lyr in service.get("layers") or []:
        if lyr.get("subLayerIds"):
            continue
        geom = lyr.get("geometryType")
        if not geom:
            continue
        spatial.append((str(lyr.get("name", lyr.get("id"))), geom, int(lyr["id"])))
    if layer_id is None:
        _require_single_point_layer([(name, geom) for name, geom, _ in spatial], url)
        layer_id = spatial[0][2]
    else:
        match = next((item for item in spatial if item[2] == layer_id), None)
        if match is None:
            layer = _arcgis_json(session, f"{service_url}/{layer_id}")
            geom = layer.get("geometryType", "unknown")
            name = str(layer.get("name", layer_id))
        else:
            name, geom, _ = match
        if geom not in _ESRI_POINT:
            raise PointLayerError(
                f"{url} layer {layer_id} ({name}) is {geom}, not a point layer"
            )
    return f"{service_url}/{layer_id}"


def fetch_arcgis_points(source_url: str) -> list[tuple[float, float]]:
    """Download points from an ArcGIS service or layer URL, in EPSG:2326."""
    layer_url = _resolve_arcgis_layer_url(source_url)
    session = _http_session()
    count_resp = session.get(
        f"{layer_url}/query",
        params={"where": "1=1", "returnCountOnly": "true", "f": "json"},
        timeout=60,
    )
    count_resp.raise_for_status()
    count_payload = count_resp.json()
    if "error" in count_payload:
        raise PointLayerError(count_payload["error"])
    expected = int(count_payload.get("count", 0))
    print(f"ArcGIS layer reports {expected} features", file=sys.stderr)

    points: list[tuple[float, float]] = []
    offset = 0
    while True:
        resp = session.get(
            f"{layer_url}/query",
            params={
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": str(EPSG),
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
            },
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise PointLayerError(payload["error"])
        features = payload.get("features") or []
        if not features:
            break
        for feat in features:
            geom = feat.get("geometry") or {}
            if "x" in geom and "y" in geom:
                points.append((float(geom["x"]), float(geom["y"])))
            for pt in geom.get("points") or []:
                points.append((float(pt[0]), float(pt[1])))
        offset += len(features)
        print(f"downloaded {offset} / {expected or '?'}", file=sys.stderr)
        if expected and offset >= expected:
            break
        if len(features) < PAGE_SIZE and not payload.get("exceededTransferLimit"):
            break
    return points


def _fiona_geometry_label(src: fiona.Collection) -> str | None:
    geom = src.schema.get("geometry")
    if geom in (None, "None", "Unknown"):
        return None
    return str(geom)


def _to_hk80(
    coords: list[tuple[float, float]], crs
) -> list[tuple[float, float]]:
    if not coords:
        return []
    if crs is None:
        print("warning: layer has no CRS; assuming EPSG:2326", file=sys.stderr)
        return coords
    try:
        epsg = crs.to_epsg() if hasattr(crs, "to_epsg") else None
    except Exception:
        epsg = None
    if epsg == EPSG:
        return coords
    source_crs = f"EPSG:{epsg}" if epsg else (getattr(crs, "to_wkt", lambda: crs)())
    transformer = Transformer.from_crs(source_crs, f"EPSG:{EPSG}", always_xy=True)
    xs, ys = zip(*coords)
    east, north = transformer.transform(xs, ys)
    return list(zip((float(x) for x in east), (float(y) for y in north)))


def fetch_fgdb_points(gdb_path: Path) -> list[tuple[float, float]]:
    """Read points from a File Geodatabase that has a single point layer."""
    if not gdb_path.exists():
        raise PointLayerError(f"File Geodatabase not found: {gdb_path}")
    try:
        names = fiona.listlayers(gdb_path)
    except Exception as exc:
        raise PointLayerError(f"cannot open File Geodatabase {gdb_path}: {exc}") from exc

    spatial: list[tuple[str, str]] = []
    for name in names:
        with fiona.open(gdb_path, layer=name) as src:
            geom = _fiona_geometry_label(src)
            if geom is not None:
                spatial.append((name, geom))
    _require_single_point_layer(spatial, str(gdb_path))
    layer_name = spatial[0][0]

    raw: list[tuple[float, float]] = []
    with fiona.open(gdb_path, layer=layer_name) as src:
        crs = src.crs
        for feat in src:
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            if gtype == "Point" and coords:
                raw.append((float(coords[0]), float(coords[1])))
            elif gtype == "MultiPoint":
                for pt in coords:
                    raw.append((float(pt[0]), float(pt[1])))
    print(f"read {len(raw)} points from {gdb_path} layer {layer_name!r}", file=sys.stderr)
    return _to_hk80(raw, crs)


def load_points(source: str) -> list[tuple[float, float]]:
    """Load HK80 points from an ArcGIS REST URL or a File Geodatabase."""
    if source.lower().startswith(("http://", "https://")):
        return fetch_arcgis_points(source)
    path = Path(source)
    if path.suffix.lower() == ".gdb" or path.name.lower().endswith(".gdb"):
        return fetch_fgdb_points(path)
    raise PointLayerError(
        f"{source!r} must be an ArcGIS FeatureServer/MapServer URL or a .gdb path"
    )


def rasterize_points(
    eastings_northings: list[tuple[float, float]],
    cell_size: float = 100.0,
    full_extent: bool = False,
) -> tuple[np.ndarray, float, float, dict]:
    """Return (north-up count array, west, north, stats)."""
    n_cols_full, n_rows_full = coverage_grid_shape(cell_size)
    skipped = 0
    cols: list[int] = []
    rows: list[int] = []
    for easting, northing in eastings_northings:
        try:
            col, row_south = hk80_to_grid_index(easting, northing, cell_size)
        except HKGeoCodeError:
            skipped += 1
            continue
        cols.append(col)
        rows.append(row_south)

    if not cols:
        raise HKGeoCodeError("no points fall inside HKGeoCode coverage")

    col_arr = np.asarray(cols, dtype=np.int32)
    row_south_arr = np.asarray(rows, dtype=np.int32)

    if full_extent:
        col0, col1 = 0, n_cols_full - 1
        row0, row1 = 0, n_rows_full - 1
    else:
        col0, col1 = int(col_arr.min()), int(col_arr.max())
        row0, row1 = int(row_south_arr.min()), int(row_south_arr.max())

    width = col1 - col0 + 1
    height = row1 - row0 + 1
    counts = np.zeros((height, width), dtype=np.uint32)
    # Convert south-based row to north-up raster row.
    raster_rows = (row1 - row_south_arr).astype(np.int32)
    raster_cols = (col_arr - col0).astype(np.int32)
    np.add.at(counts, (raster_rows, raster_cols), 1)

    west = ORIGIN_EASTING + col0 * cell_size
    north = ORIGIN_NORTHING + (row1 + 1) * cell_size
    stats = {
        "points": len(eastings_northings),
        "binned": int(counts.sum()),
        "skipped": skipped,
        "occupied_cells": int(np.count_nonzero(counts)),
        "max_count": int(counts.max()),
        "width": width,
        "height": height,
        "cell_size": cell_size,
        "west": west,
        "south": ORIGIN_NORTHING + row0 * cell_size,
        "east": ORIGIN_EASTING + (col1 + 1) * cell_size,
        "north": north,
    }
    return counts, west, north, stats


def write_geotiff(
    path: Path,
    counts: np.ndarray,
    west: float,
    north: float,
    cell_size: float,
) -> None:
    """Write a single-band GeoTIFF in EPSG:2326 (pixel is area, NW origin)."""
    geokeys = np.array(
        [
            1,
            1,
            0,
            3,
            1024,
            0,
            1,
            1,  # ModelTypeProjected
            1025,
            0,
            1,
            1,  # RasterPixelIsArea
            3072,
            0,
            1,
            EPSG,  # ProjectedCSTypeGeoKey = EPSG:2326
        ],
        dtype=np.uint16,
    )
    nodata_ascii = f"{NODATA}\x00".encode("ascii")
    tifffile.imwrite(
        path,
        counts,
        photometric="minisblack",
        compression="zlib",
        extratags=[
            (33550, 12, 3, (float(cell_size), float(cell_size), 0.0), True),
            (
                33922,
                12,
                6,
                (0.0, 0.0, 0.0, float(west), float(north), 0.0),
                True,
            ),
            (34735, 3, geokeys.size, geokeys, True),
            (42113, 2, len(nodata_ascii), nodata_ascii, True),
        ],
    )


# ColorBrewer YlOrRd, one colour per integer stop count (1–6+).
COUNT_COLORS: dict[int, tuple[int, int, int, int]] = {
    1: (255, 255, 178, 210),
    2: (254, 217, 118, 220),
    3: (254, 178, 76, 225),
    4: (253, 141, 60, 230),
    5: (240, 59, 32, 235),
    6: (189, 0, 38, 240),
}


def _colorize(value: float, vmax: float) -> tuple[int, int, int, int]:
    count = int(value)
    if count <= 0:
        return (0, 0, 0, 0)
    if count in COUNT_COLORS:
        return COUNT_COLORS[count]
    return COUNT_COLORS[max(COUNT_COLORS)]


def write_viewer(
    counts: np.ndarray,
    west: float,
    south: float,
    east: float,
    north: float,
    cell_size: float,
    stats: dict,
    png_path: Path,
    html_path: Path,
    overlay_width: int = 1600,
) -> tuple[float, float, float, float]:
    """Warp the HK80 grid to a lon/lat PNG and write a Leaflet viewer."""
    to_ll = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True)
    to_en = Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True)
    corners = [
        to_ll.transform(west, south),
        to_ll.transform(west, north),
        to_ll.transform(east, south),
        to_ll.transform(east, north),
    ]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    lon0, lon1 = min(lons), max(lons)
    lat0, lat1 = min(lats), max(lats)
    height = max(1, int(round(overlay_width * (lat1 - lat0) / (lon1 - lon0))))
    vmax = max(1, int(stats["max_count"]))

    xs = np.linspace(lon0, lon1, overlay_width, endpoint=False) + (lon1 - lon0) / (
        2 * overlay_width
    )
    ys = np.linspace(lat1, lat0, height, endpoint=False) - (lat1 - lat0) / (2 * height)
    lon_grid, lat_grid = np.meshgrid(xs, ys)
    easting, northing = to_en.transform(lon_grid, lat_grid)

    col = np.floor((easting - west) / cell_size).astype(np.int32)
    row = np.floor((north - northing) / cell_size).astype(np.int32)
    valid = (
        (col >= 0)
        & (col < counts.shape[1])
        & (row >= 0)
        & (row < counts.shape[0])
    )
    sampled = np.zeros(col.shape, dtype=np.uint32)
    sampled[valid] = counts[row[valid], col[valid]]

    rgba = np.zeros((height, overlay_width, 4), dtype=np.uint8)
    unique_vals = np.unique(sampled)
    lut = {int(v): _colorize(float(v), float(vmax)) for v in unique_vals}
    for value, color in lut.items():
        rgba[sampled == value] = color
    Image.fromarray(rgba, mode="RGBA").save(png_path)

    center_lon = (lon0 + lon1) / 2
    center_lat = (lat0 + lat1) / 2
    legend_items = "\n".join(
        (
            '<div class="legend-item">'
            f'<span class="swatch" style="background:rgba({r},{g},{b},{a/255:.2f})"></span>'
            f"{count}</div>"
        )
        for count in range(1, vmax + 1)
        for r, g, b, a in [_colorize(count, vmax)]
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Points on HKGeoCode 100 m grid</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ margin: 0; height: 100%; font-family: sans-serif; }}
    .panel {{
      position: absolute; z-index: 1000; top: 12px; right: 12px;
      background: #fff; padding: 12px 14px; border: 1px solid #ccc;
      max-width: 280px; font-size: 13px; line-height: 1.4;
    }}
    .panel h1 {{ font-size: 15px; margin: 0 0 8px; }}
    .legend {{
      display: flex; flex-wrap: wrap; gap: 8px 12px; margin: 10px 0 8px;
    }}
    .legend-item {{ display: flex; align-items: center; gap: 6px; }}
    .swatch {{
      width: 14px; height: 14px; border: 1px solid #888; display: inline-block;
    }}
    .leaflet-tile-pane {{ filter: grayscale(100%); }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <h1>Point count / 100 m cell</h1>
    <div>Points binned to HKGeoCode neighbourhood cells (EPSG:2326).</div>
    <div class="legend">{legend_items}</div>
    <p>
      {stats["binned"]:,} points in {stats["occupied_cells"]:,} cells
      (max {stats["max_count"]} per cell).
    </p>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map("map").setView([{center_lat:.6f}, {center_lon:.6f}], 11);
    L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    }}).addTo(map);
    const overlay = L.imageOverlay("{png_path.name}", [
      [{lat0:.8f}, {lon0:.8f}],
      [{lat1:.8f}, {lon1:.8f}]
    ], {{ opacity: 1, interactive: true }});
    overlay.addTo(map);
    map.fitBounds(overlay.getBounds());
  </script>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")
    return lon0, lat0, lon1, lat1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate points onto the HKGeoCode 100 m grid as a GeoTIFF."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=CSDI_BUS_STOPS,
        help=(
            "point layer: ArcGIS FeatureServer/MapServer URL, or File Geodatabase "
            "(.gdb). Must contain a single point feature layer. "
            "Default: CSDI bus stops FeatureServer"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output/points_hkgcode_100m.tif"),
        help="output GeoTIFF path",
    )
    parser.add_argument(
        "--length",
        type=int,
        choices=sorted(CELL_SIZE_M),
        default=4,
        help="HKGeoCode length: 2=2 km, 4=100 m, 6=5 m (default 4)",
    )
    parser.add_argument(
        "--full-extent",
        action="store_true",
        help="write the full 64 km × 48 km HKGeoCode coverage",
    )
    parser.add_argument(
        "--no-viewer",
        action="store_true",
        help="do not write the Leaflet PNG/HTML overlay",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cell_size = CELL_SIZE_M[args.length]
    try:
        points = load_points(args.source)
    except PointLayerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"loaded {len(points)} points from {args.source}", file=sys.stderr)

    counts, west, north, stats = rasterize_points(
        points, cell_size=cell_size, full_extent=args.full_extent
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_geotiff(args.output, counts, west, north, cell_size)
    print(json.dumps(stats, indent=2))
    print(f"wrote {args.output}")

    sample = next(
        (
            hk80_to_hkgeocode(e, n, args.length)
            for e, n in points
            if ORIGIN_EASTING <= e < COVERAGE_EASTING
            and ORIGIN_NORTHING <= n < COVERAGE_NORTHING
        ),
        None,
    )
    if sample:
        print(f"example {args.length}-char cell: {sample}", file=sys.stderr)

    if not args.no_viewer:
        png_path = args.output.with_suffix(".png")
        html_path = args.output.with_name("viewer.html")
        write_viewer(
            counts,
            stats["west"],
            stats["south"],
            stats["east"],
            stats["north"],
            cell_size,
            stats,
            png_path,
            html_path,
        )
        print(f"wrote {png_path}")
        print(f"wrote {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
