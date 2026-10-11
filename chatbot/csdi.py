"""CSDI portal access: catalogue keyword search and point-layer download.

Both functions talk directly to the public CSDI endpoints:

* Catalogue: ``https://portal.csdi.gov.hk/geoportal/rest/metadata/search``
* Data: ArcGIS REST ``FeatureServer`` / ``MapServer`` ``query`` endpoints

Points are requested in EPSG:2326 (Hong Kong 1980 Grid) so that they can be
binned onto HKGeoCode cells without reprojection.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import pandas as pd
import requests

CATALOGUE_SEARCH = "https://portal.csdi.gov.hk/geoportal/rest/metadata/search"
CSDI_REST_BASE = "https://portal.csdi.gov.hk/server/rest/services/common"

EPSG = 2326
PAGE_SIZE = 3000
USER_AGENT = "hkgeocode-chatbot/0.1"
TIMEOUT = 120

DEFAULT_LAYERS = {
    "Coordinates of Bus Stops": f"{CSDI_REST_BASE}/td_rcd_1638874475129_49745/FeatureServer/0",
    "Wi-Fi.HK": f"{CSDI_REST_BASE}/dpo_rcd_1629267205215_74392/FeatureServer/0",
}

_ARCGIS_SERVICE = re.compile(
    r"(?P<base>.+/(?:FeatureServer|MapServer))(?:/(?P<layer>\d+))?/?$",
    re.IGNORECASE,
)
_ESRI_POINT = {"esriGeometryPoint", "esriGeometryMultipoint"}


class CSDIError(RuntimeError):
    """Raised when the CSDI portal cannot satisfy a request."""


@dataclass
class CatalogueItem:
    dataset_id: str
    title: str
    feature_server_url: str
    point_layers: list[tuple[int, str]] = field(default_factory=list)

    @property
    def layer_url(self) -> str:
        """URL of the first point layer in the service."""
        if not self.point_layers:
            return self.feature_server_url
        return f"{self.feature_server_url}/{self.point_layers[0][0]}"

    @property
    def label(self) -> str:
        names = ", ".join(name for _, name in self.point_layers)
        return f"{self.title} ({names})" if names else self.title


@dataclass
class LayerData:
    """A downloaded point layer with attributes and HK80 coordinates."""

    name: str
    url: str
    df: pd.DataFrame  # includes ``easting`` and ``northing`` columns
    fields: list[dict] = field(default_factory=list)
    reported_count: int = 0

    @property
    def count(self) -> int:
        return len(self.df)

    @property
    def attribute_columns(self) -> list[str]:
        return [c for c in self.df.columns if c not in ("easting", "northing")]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _json(session: requests.Session, url: str, params: dict | None = None, timeout: int = TIMEOUT) -> dict:
    params = dict(params or {})
    params.setdefault("f", "json")
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and "error" in payload:
        raise CSDIError(str(payload["error"]))
    return payload


def parse_arcgis_url(url: str) -> tuple[str, int | None]:
    """Split an ArcGIS REST URL into (service_url, layer_id or None)."""
    parsed = urlparse(url.strip())
    match = _ARCGIS_SERVICE.search(parsed.path.rstrip("/"))
    if not match:
        raise CSDIError(f"{url!r} is not an ArcGIS FeatureServer or MapServer URL")
    layer_id = int(match.group("layer")) if match.group("layer") is not None else None
    service = urlunparse(parsed._replace(path=match.group("base"), query="", fragment=""))
    return service.rstrip("/"), layer_id


def service_point_layers(service_url: str, session: requests.Session | None = None) -> list[tuple[int, str]]:
    """Return [(layer_id, name)] for the point layers of a service."""
    session = session or _session()
    service = _json(session, service_url, timeout=30)
    layers = []
    for lyr in service.get("layers") or []:
        if lyr.get("subLayerIds"):
            continue
        if lyr.get("geometryType") in _ESRI_POINT:
            layers.append((int(lyr["id"]), str(lyr.get("name", lyr["id"]))))
    return layers


def search_catalogue(keyword: str, max_results: int = 20, point_only: bool = True) -> list[CatalogueItem]:
    """Search the CSDI catalogue and return datasets that expose a FeatureServer.

    When ``point_only`` is true each service is probed and only those with at
    least one point layer are kept.
    """
    session = _session()
    keyword = keyword.strip()
    if not keyword:
        return []

    # The portal's full-text ranking is weak for multi-word queries, so run a
    # quoted phrase query plus the raw query, then rank by title matches.
    queries = [f'"{keyword}"', keyword] if " " in keyword or "-" in keyword else [keyword]
    if "wifi" in keyword.lower():
        queries.append(keyword.lower().replace("wifi", "wi-fi"))
    seen: dict[str, dict] = {}
    for q in queries:
        try:
            payload = _json(
                session,
                CATALOGUE_SEARCH,
                {"q": q, "max": max(max_results * 3, 30), "lang": "en"},
            )
        except (requests.RequestException, CSDIError, ValueError):
            continue
        for result in payload.get("results") or []:
            rid = str(result.get("id", ""))
            if rid and rid not in seen:
                seen[rid] = result

    tokens = [t for t in re.split(r"[\s\-_.]+", keyword.lower()) if t]

    def score(result: dict) -> tuple[int, int]:
        title = str(result.get("title", "")).lower()
        desc = str(result.get("description", "")).lower()
        title_hits = sum(1 for t in tokens if t in title)
        desc_hits = sum(1 for t in tokens if t in desc)
        return (title_hits, desc_hits)

    ranked = sorted(seen.values(), key=score, reverse=True)

    candidates: list[CatalogueItem] = []
    for result in ranked:
        href = next(
            (
                link.get("href")
                for link in result.get("links") or []
                if link.get("dctype") == "FeatureServer" and link.get("href")
            ),
            None,
        )
        if not href:
            continue
        candidates.append(
            CatalogueItem(
                dataset_id=str(result.get("id", "")),
                title=str(result.get("title", result.get("id", ""))).strip(),
                feature_server_url=href.rstrip("/"),
            )
        )
        if len(candidates) >= (max_results * 2 if point_only else max_results):
            break

    if not point_only:
        return candidates[:max_results]

    def probe(item: CatalogueItem) -> CatalogueItem:
        try:
            item.point_layers = service_point_layers(item.feature_server_url, _session())
        except (requests.RequestException, CSDIError, ValueError):
            item.point_layers = []
        return item

    with ThreadPoolExecutor(max_workers=8) as pool:
        probed = list(pool.map(probe, candidates))
    return [item for item in probed if item.point_layers][:max_results]


def resolve_point_layer(url: str, session: requests.Session | None = None) -> tuple[str, str]:
    """Return (layer_url, layer_name) for a service or layer URL, checking it is a point layer."""
    session = session or _session()
    service_url, layer_id = parse_arcgis_url(url)
    points = service_point_layers(service_url, session)
    if layer_id is None:
        if len(points) != 1:
            names = ", ".join(f"{i}:{n}" for i, n in points) or "none"
            raise CSDIError(
                f"{url} must contain exactly one point layer; point layers found: {names}"
            )
        layer_id, name = points[0]
    else:
        match = next((n for i, n in points if i == layer_id), None)
        if match is None:
            layer = _json(session, f"{service_url}/{layer_id}")
            if layer.get("geometryType") not in _ESRI_POINT:
                raise CSDIError(
                    f"{url} layer {layer_id} is {layer.get('geometryType')}, not a point layer"
                )
            match = str(layer.get("name", layer_id))
        name = match
    return f"{service_url}/{layer_id}", name


def fetch_layer(url: str, name: str | None = None, progress=None) -> LayerData:
    """Download every feature of a point layer as a DataFrame in EPSG:2326.

    ``progress`` is an optional callable ``(downloaded, expected)``.
    """
    session = _session()
    layer_url, layer_name = resolve_point_layer(url, session)
    meta = _json(session, layer_url)
    fields = [
        {"name": f["name"], "type": f.get("type", ""), "alias": f.get("alias", f["name"])}
        for f in meta.get("fields") or []
        if f.get("type") not in ("esriFieldTypeGeometry",)
    ]
    page_size = min(PAGE_SIZE, int(meta.get("maxRecordCount") or PAGE_SIZE))

    count = _json(session, f"{layer_url}/query", {"where": "1=1", "returnCountOnly": "true"})
    expected = int(count.get("count", 0))

    rows: list[dict] = []
    offset = 0
    while True:
        payload = _json(
            session,
            f"{layer_url}/query",
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": str(EPSG),
                "resultOffset": offset,
                "resultRecordCount": page_size,
            },
        )
        features = payload.get("features") or []
        if not features:
            break
        for feat in features:
            attrs = dict(feat.get("attributes") or {})
            geom = feat.get("geometry") or {}
            if "x" in geom and "y" in geom:
                row = dict(attrs)
                row["easting"] = float(geom["x"])
                row["northing"] = float(geom["y"])
                rows.append(row)
            for pt in geom.get("points") or []:
                row = dict(attrs)
                row["easting"] = float(pt[0])
                row["northing"] = float(pt[1])
                rows.append(row)
        offset += len(features)
        if progress:
            progress(offset, expected)
        if expected and offset >= expected:
            break
        if len(features) < page_size and not payload.get("exceededTransferLimit"):
            break

    df = pd.DataFrame(rows)
    if df.empty:
        raise CSDIError(f"{layer_url} returned no point features")
    _coerce_types(df, fields)
    return LayerData(
        name=name or layer_name,
        url=layer_url,
        df=df,
        fields=fields,
        reported_count=expected,
    )


def _coerce_types(df: pd.DataFrame, fields: list[dict]) -> None:
    """Convert date fields from epoch-ms and numeric-looking strings to numbers."""
    for f in fields:
        col = f["name"]
        if col not in df.columns:
            continue
        if f["type"] == "esriFieldTypeDate":
            df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce")
        elif f["type"] == "esriFieldTypeString":
            series = df[col]
            if series.dtype == object:
                stripped = series.astype(str).str.strip()
                numeric = pd.to_numeric(stripped, errors="coerce")
                non_empty = stripped.ne("") & stripped.ne("None")
                # Only convert when every non-empty value is numeric.
                if non_empty.any() and numeric[non_empty].notna().all():
                    df[col] = numeric
