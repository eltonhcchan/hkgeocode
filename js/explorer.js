/**
 * Viewport-only HKGeoCode explorer (Leaflet).
 * Cell boundaries and identifiers are drawn on a canvas covering the current
 * map view so labels stay on every visible cell while panning.
 */
(function () {
  "use strict";

  const MAX_GRID_CELLS = 12000;
  const MAP_MAX_ZOOM = 22;
  const TILE_NATIVE_ZOOM = 19;
  const HK80 =
    "+proj=tmerc +lat_0=22.31213333333334 +lon_0=114.1785555555556 +k=1 " +
    "+x_0=836694.05 +y_0=819069.8 +ellps=intl " +
    "+towgs84=-162.619,-276.959,-161.764,0.067753,-2.24365,-1.15883,-1.09425 " +
    "+units=m +no_defs";

  proj4.defs("EPSG:2326", HK80);

  const HKG = window.HKGeoCode;
  const els = {
    code: document.getElementById("cell-code"),
    level: document.getElementById("cell-level"),
    origin: document.getElementById("cell-origin"),
    parent: document.getElementById("cell-parent"),
    status: document.getElementById("grid-status"),
    search: document.getElementById("search-code"),
    resolution: document.getElementById("resolution"),
    basemap: document.getElementById("basemap"),
    showGrid: document.getElementById("show-grid"),
    showLabels: document.getElementById("show-labels"),
    showParent: document.getElementById("show-parent"),
    showChildren: document.getElementById("show-children"),
    showNeighbours: document.getElementById("show-neighbours"),
    neighbours: document.getElementById("cell-neighbours"),
    goParent: document.getElementById("go-parent"),
    fitCell: document.getElementById("fit-cell"),
    copy: document.getElementById("copy-code"),
    clear: document.getElementById("clear-cell"),
    hover: document.getElementById("hover-code"),
    polyCells: document.getElementById("poly-cells"),
    esriUrl: document.getElementById("esri-layer-url"),
    loadEsri: document.getElementById("load-esri-layer"),
    classField: document.getElementById("class-field"),
    classLegend: document.getElementById("class-legend"),
    console: document.getElementById("message-console"),
  };

  const map = L.map("map", {
    zoomControl: true,
    maxZoom: MAP_MAX_ZOOM,
  }).setView([22.32, 114.17], 12);

  const LANDSD_ATTR =
    "<a href='https://api.portal.hkmapservice.gov.hk/disclaimer' target='_blank' rel='noopener'>Map from Lands Department</a>";
  const basemapVTURL = "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/vt/basemap/HK80";
  const mapLabelVTUrl = "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/vt/label/hk/tc/HK80";

  function landsdXyzUrl(vtRoot) {
    return `${vtRoot.replace("/vt/", "/xyz/").replace(/HK80$/, "WGS84")}/{z}/{x}/{y}.png`;
  }

  const osmLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxNativeZoom: TILE_NATIVE_ZOOM,
    maxZoom: MAP_MAX_ZOOM,
    attribution: "&copy; OpenStreetMap",
  });
  const esriStreetsLayer = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    {
      maxNativeZoom: TILE_NATIVE_ZOOM,
      maxZoom: MAP_MAX_ZOOM,
      attribution:
        "Tiles &copy; <a href='https://www.esri.com/' target='_blank' rel='noopener'>Esri</a> &mdash; Esri, TomTom, Garmin, FAO, NOAA, USGS, OpenStreetMap contributors, and the GIS User Community",
    }
  );

  map.createPane("landsdLabel");
  const labelPane = map.getPane("landsdLabel");
  labelPane.style.zIndex = "250";
  labelPane.style.pointerEvents = "none";

  const landsdBaseLayer = L.tileLayer(landsdXyzUrl(basemapVTURL), {
    minZoom: 8,
    maxNativeZoom: 20,
    maxZoom: MAP_MAX_ZOOM,
    attribution: LANDSD_ATTR,
  });
  const landsdLabelLayer = L.tileLayer(landsdXyzUrl(mapLabelVTUrl), {
    minZoom: 8,
    maxNativeZoom: 20,
    maxZoom: MAP_MAX_ZOOM,
    pane: "landsdLabel",
    attribution: LANDSD_ATTR,
  });

  const LandsdLogo = L.Control.extend({
    onAdd: function onAdd() {
      const wrap = L.DomUtil.create("div", "landsd-logo");
      wrap.setAttribute("role", "img");
      wrap.setAttribute("aria-label", "Lands Department");
      wrap.textContent = "";
      wrap.insertAdjacentHTML(
        "afterbegin",
        "Lands Department<small>Map from LandsD</small>"
      );
      return wrap;
    },
  });
  const landsdLogoControl = new LandsdLogo({ position: "bottomleft" });

  function setBasemap(id) {
    const useLandsd = id === "landsd" || id === "landsd-grey";
    document.body.classList.toggle("basemap-landsd", useLandsd);
    map.getContainer().classList.toggle("basemap-landsd", useLandsd);
    map.getContainer().classList.toggle("basemap-landsd-grey", id === "landsd-grey");

    [osmLayer, esriStreetsLayer, landsdBaseLayer, landsdLabelLayer].forEach((layer) => {
      if (map.hasLayer(layer)) {
        map.removeLayer(layer);
      }
    });
    if (landsdLogoControl._map) {
      map.removeControl(landsdLogoControl);
    }

    if (useLandsd) {
      landsdBaseLayer.addTo(map);
      landsdLabelLayer.addTo(map);
      landsdLogoControl.addTo(map);
    } else if (id === "esri-streets") {
      esriStreetsLayer.addTo(map);
    } else {
      osmLayer.addTo(map);
    }
  }

  map.createPane("hkgGrid");
  const gridPane = map.getPane("hkgGrid");
  gridPane.style.zIndex = "350";
  gridPane.style.pointerEvents = "none";

  map.createPane("hkgPolygons");
  const polygonPane = map.getPane("hkgPolygons");
  polygonPane.style.zIndex = "400";

  const canvasRenderer = L.canvas({ padding: 0.4 });
  const parentLayer = L.layerGroup().addTo(map);
  const childLayer = L.layerGroup().addTo(map);
  const neighbourLayer = L.layerGroup().addTo(map);
  const selectedLayer = L.layerGroup().addTo(map);

  let selected = null;
  let extraLabelCodes = [];
  let cellFillByCode = {};

  function toHk80(latlng) {
    return proj4("EPSG:4326", "EPSG:2326", [latlng.lng, latlng.lat]);
  }

  function toLatLng(easting, northing) {
    const [lng, lat] = proj4("EPSG:2326", "EPSG:4326", [easting, northing]);
    return [lat, lng];
  }

  function cellLatLngs(code) {
    const { easting, northing, cellSize } = HKG.hkgeocodeCellOrigin(code);
    return [
      toLatLng(easting, northing),
      toLatLng(easting + cellSize, northing),
      toLatLng(easting + cellSize, northing + cellSize),
      toLatLng(easting, northing + cellSize),
    ];
  }

  function viewportHk80() {
    const bounds = map.getBounds().pad(0.05);
    const corners = [
      bounds.getSouthWest(),
      bounds.getNorthWest(),
      bounds.getNorthEast(),
      bounds.getSouthEast(),
    ].map(toHk80);
    const eastings = corners.map((p) => p[0]);
    const northings = corners.map((p) => p[1]);
    return {
      west: Math.max(HKG.ORIGIN_EASTING, Math.min(...eastings)),
      south: Math.max(HKG.ORIGIN_NORTHING, Math.min(...northings)),
      east: Math.min(HKG.COVERAGE_EASTING, Math.max(...eastings)),
      north: Math.min(HKG.COVERAGE_NORTHING, Math.max(...northings)),
    };
  }

  function currentLength() {
    const value = els.resolution.value;
    if (value !== "auto") {
      return Number(value);
    }
    const z = map.getZoom();
    if (z >= 17) {
      return 6;
    }
    if (z >= 13) {
      return 4;
    }
    return 2;
  }

  function addRect(layer, code, style) {
    const options = Object.assign({ renderer: canvasRenderer }, style);
    return L.polygon(cellLatLngs(code), options).addTo(layer);
  }

  function clipRingToRect(points, x0, y0, x1, y1) {
    const clipEdge = (pts, inside, intersect) => {
      const out = [];
      for (let i = 0; i < pts.length; i += 1) {
        const a = pts[i];
        const b = pts[(i + 1) % pts.length];
        const aIn = inside(a);
        const bIn = inside(b);
        if (aIn && bIn) {
          out.push(b);
        } else if (aIn && !bIn) {
          out.push(intersect(a, b));
        } else if (!aIn && bIn) {
          out.push(intersect(a, b));
          out.push(b);
        }
      }
      return out;
    };
    let pts = points;
    pts = clipEdge(
      pts,
      (p) => p.x >= x0,
      (a, b) => {
        const t = (x0 - a.x) / (b.x - a.x || 1e-9);
        return { x: x0, y: a.y + t * (b.y - a.y) };
      }
    );
    pts = clipEdge(
      pts,
      (p) => p.x <= x1,
      (a, b) => {
        const t = (x1 - a.x) / (b.x - a.x || 1e-9);
        return { x: x1, y: a.y + t * (b.y - a.y) };
      }
    );
    pts = clipEdge(
      pts,
      (p) => p.y >= y0,
      (a, b) => {
        const t = (y0 - a.y) / (b.y - a.y || 1e-9);
        return { x: a.x + t * (b.x - a.x), y: y0 };
      }
    );
    pts = clipEdge(
      pts,
      (p) => p.y <= y1,
      (a, b) => {
        const t = (y1 - a.y) / (b.y - a.y || 1e-9);
        return { x: a.x + t * (b.x - a.x), y: y1 };
      }
    );
    return pts;
  }

  function ringCentroid(pts) {
    let area2 = 0;
    let cx = 0;
    let cy = 0;
    for (let i = 0; i < pts.length; i += 1) {
      const p = pts[i];
      const q = pts[(i + 1) % pts.length];
      const cross = p.x * q.y - q.x * p.y;
      area2 += cross;
      cx += (p.x + q.x) * cross;
      cy += (p.y + q.y) * cross;
    }
    if (Math.abs(area2) < 1e-6) {
      return {
        x: pts.reduce((sum, p) => sum + p.x, 0) / pts.length,
        y: pts.reduce((sum, p) => sum + p.y, 0) / pts.length,
      };
    }
    return { x: cx / (3 * area2), y: cy / (3 * area2) };
  }

  function visibleLabelPoint(ring, width, height) {
    const clipped = clipRingToRect(ring, 0, 0, width, height);
    if (clipped.length < 3) {
      return null;
    }
    return ringCentroid(clipped);
  }

  function drawCodeLabel(ctx, code, point, fontPx) {
    ctx.font = `600 ${fontPx}px ui-monospace, Consolas, monospace`;
    ctx.strokeStyle = "rgba(255,255,255,0.95)";
    ctx.lineWidth = 3;
    ctx.strokeText(code, point.x, point.y);
    ctx.fillStyle = "#111";
    ctx.fillText(code, point.x, point.y);
    ctx.strokeStyle = "#111";
    ctx.lineWidth = 1.5;
  }

  const CellViewCanvas = L.Layer.extend({
    onAdd: function onAdd(leafletMap) {
      this._map = leafletMap;
      this._canvas = L.DomUtil.create("canvas", "hkg-cell-canvas");
      this._canvas.style.pointerEvents = "none";
      leafletMap.getPane("hkgGrid").appendChild(this._canvas);
      leafletMap.on("move zoom viewreset resize", this._schedule, this);
      this._drawNow();
    },
    onRemove: function onRemove(leafletMap) {
      leafletMap.off("move zoom viewreset resize", this._schedule, this);
      L.DomUtil.remove(this._canvas);
    },
    _schedule: function _schedule() {
      if (this._frame) {
        return;
      }
      this._frame = L.Util.requestAnimFrame(() => {
        this._frame = null;
        this._drawNow();
      }, this);
    },
    _redraw: function _redraw() {
      this._drawNow();
    },
    _drawNow: function _drawNow() {
      const leafletMap = this._map;
      const size = leafletMap.getSize();
      const dpr = window.devicePixelRatio || 1;
      L.DomUtil.setPosition(
        this._canvas,
        leafletMap.containerPointToLayerPoint([0, 0])
      );
      this._canvas.width = Math.max(1, Math.round(size.x * dpr));
      this._canvas.height = Math.max(1, Math.round(size.y * dpr));
      this._canvas.style.width = `${size.x}px`;
      this._canvas.style.height = `${size.y}px`;
      const ctx = this._canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.x, size.y);

      const view = viewportHk80();
      if (view.east <= view.west || view.north <= view.south) {
        return;
      }
      const length = currentLength();
      const codes = HKG.codesInBounds(
        view.west,
        view.south,
        view.east,
        view.north,
        length
      );
      const showGrid = els.showGrid.checked;
      const showLabels = els.showLabels.checked;
      const showFills = Object.keys(cellFillByCode).length > 0;
      if ((!showGrid && !showLabels && !showFills) || codes.length > MAX_GRID_CELLS) {
        this._lastCount = codes.length;
        this._tooMany = codes.length > MAX_GRID_CELLS;
        return;
      }
      this._tooMany = false;
      this._lastCount = codes.length;

      ctx.lineJoin = "round";
      ctx.strokeStyle = "#111";
      ctx.lineWidth = 1.5;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      const toPoint = (latlng) => leafletMap.latLngToContainerPoint(latlng);

      codes.forEach((code) => {
        const ring = cellLatLngs(code).map(toPoint);
        const fill = cellFillByCode[code];
        ctx.beginPath();
        ctx.moveTo(ring[0].x, ring[0].y);
        for (let i = 1; i < ring.length; i += 1) {
          ctx.lineTo(ring[i].x, ring[i].y);
        }
        ctx.closePath();
        if (fill) {
          ctx.fillStyle = fill.color;
          ctx.globalAlpha = 0.55;
          ctx.fill();
          ctx.globalAlpha = 1;
        }
        if (showGrid) {
          ctx.stroke();
        }
        if (showLabels) {
          const point = visibleLabelPoint(ring, size.x, size.y);
          if (point) {
            const width = Math.hypot(ring[1].x - ring[0].x, ring[1].y - ring[0].y);
            const height = Math.hypot(ring[3].x - ring[0].x, ring[3].y - ring[0].y);
            const fontPx = Math.max(
              8,
              Math.min(14, Math.floor(Math.min(width / (code.length * 0.72), height * 0.4)))
            );
            drawCodeLabel(ctx, code, point, fontPx);
          }
        }
      });

      const codeSet = new Set(codes);
      if (showLabels) {
        extraLabelCodes.forEach((code) => {
          if (codeSet.has(code)) {
            return;
          }
          const ring = cellLatLngs(code).map(toPoint);
          const point = visibleLabelPoint(ring, size.x, size.y);
          if (point) {
            drawCodeLabel(ctx, code, point, 12);
          }
        });
      }
    },
  });

  const viewCanvas = new CellViewCanvas();
  map.addLayer(viewCanvas);

  const POLY = window.HKGPolygonCells;
  const polygonLayer = L.geoJSON(null, {
    pane: "hkgPolygons",
    style: function styleFeature(feature) {
      const field = els.classField.value;
      const value = feature.properties ? feature.properties[field] : null;
      const { color } = POLY.colorForValue(value, polygonColorByValue);
      return {
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.22,
      };
    },
    onEachFeature: function onEachFeature(feature, layer) {
      const field = els.classField.value;
      const value = feature.properties ? feature.properties[field] : "";
      if (value !== "" && value != null) {
        layer.bindTooltip(String(value), { sticky: true });
      }
      layer.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        try {
          const [easting, northing] = toHk80(event.latlng);
          selectCode(HKG.hk80ToHkgeocode(easting, northing, currentLength()));
        } catch (err) {
          if (err instanceof HKG.HKGeoCodeError) {
            els.status.textContent = "Click is outside HKGeoCode coverage.";
          } else {
            throw err;
          }
        }
      });
    },
  }).addTo(map);

  let polygonLayerInfo = null;
  let polygonFeatures = [];
  let polygonColorByValue = new Map();
  let polygonRequestId = 0;

  function logConsole(message, isError) {
    const line = document.createElement("div");
    if (isError) {
      line.className = "err";
    }
    line.textContent = message;
    els.console.appendChild(line);
    while (els.console.childNodes.length > 24) {
      els.console.removeChild(els.console.firstChild);
    }
    els.console.scrollTop = els.console.scrollHeight;
  }

  function clearConsole() {
    els.console.textContent = "";
  }

  function cellCentroidLngLat(code) {
    const { easting, northing, cellSize } = HKG.hkgeocodeCellOrigin(code);
    const [lat, lng] = toLatLng(easting + cellSize / 2, northing + cellSize / 2);
    return [lng, lat];
  }

  function viewportWgs84() {
    const bounds = map.getBounds().pad(0.05);
    return {
      west: bounds.getWest(),
      south: bounds.getSouth(),
      east: bounds.getEast(),
      north: bounds.getNorth(),
    };
  }

  function renderClassLegend() {
    els.classLegend.textContent = "";
    if (!polygonColorByValue.size) {
      return;
    }
    const field = els.classField.value;
    const active = new Set();
    polygonFeatures.forEach((feature) => {
      const value = feature.properties ? feature.properties[field] : null;
      active.add(value == null || value === "" ? "(empty)" : String(value));
    });
    const entries = [...polygonColorByValue.entries()].filter(([value]) =>
      active.has(value)
    );
    if (!entries.length) {
      return;
    }
    const shown = entries.slice(0, 16);
    shown.forEach(([value, color]) => {
      const row = document.createElement("div");
      const swatch = document.createElement("span");
      swatch.className = "swatch-sel";
      swatch.style.background = color;
      swatch.style.border = "none";
      row.appendChild(swatch);
      row.appendChild(document.createTextNode(` ${value}`));
      els.classLegend.appendChild(row);
    });
    if (entries.length > shown.length) {
      const more = document.createElement("div");
      more.textContent = `… and ${entries.length - shown.length} more`;
      els.classLegend.appendChild(more);
    }
  }

  function clearPolygonDisplay() {
    cellFillByCode = {};
    polygonFeatures = [];
    polygonLayer.clearLayers();
    els.classLegend.textContent = "";
    viewCanvas._redraw();
  }

  function resetPolygonColors() {
    polygonColorByValue = new Map();
  }

  function applyPolygonAssignment(features, codes) {
    const field = els.classField.value;
    features.forEach((feature) => {
      const value = feature.properties ? feature.properties[field] : null;
      POLY.colorForValue(value, polygonColorByValue);
    });
    const result = POLY.assignCells(
      features,
      field,
      codes,
      cellCentroidLngLat,
      polygonColorByValue
    );
    cellFillByCode = result.fillByCode;
    polygonLayer.clearLayers();
    polygonLayer.addData({ type: "FeatureCollection", features });
    renderClassLegend();
    return result.assigned;
  }

  async function syncPolygonCells() {
    if (!els.polyCells.checked) {
      if (Object.keys(cellFillByCode).length || polygonFeatures.length) {
        clearPolygonDisplay();
      }
      return;
    }
    if (!polygonLayerInfo) {
      logConsole("Load a polygon layer first.", true);
      els.polyCells.checked = false;
      return;
    }
    if (!els.classField.value) {
      logConsole("Select a classification field before creating cells.", true);
      els.polyCells.checked = false;
      return;
    }
    const view = viewportHk80();
    const length = currentLength();
    if (view.east <= view.west || view.north <= view.south) {
      logConsole("Map is outside HKGeoCode coverage.", true);
      clearPolygonDisplay();
      return;
    }
    const codes = HKG.codesInBounds(view.west, view.south, view.east, view.north, length);
    if (codes.length > MAX_GRID_CELLS) {
      logConsole(
        `${codes.length.toLocaleString()} cells in view — zoom in before converting polygons to cells.`,
        true
      );
      clearPolygonDisplay();
      return;
    }
    const requestId = (polygonRequestId += 1);
    try {
      logConsole(`Querying ${polygonLayerInfo.name} in the current view…`);
      const features = await POLY.queryPolygons(polygonLayerInfo, viewportWgs84());
      if (requestId !== polygonRequestId) {
        return;
      }
      polygonFeatures = features;
      const assigned = applyPolygonAssignment(features, codes);
      logConsole(
        `${features.length} polygon(s) in view. ${assigned} cell(s) included by centroid containment on “${els.classField.value}”.`
      );
      viewCanvas._redraw();
    } catch (err) {
      if (requestId !== polygonRequestId) {
        return;
      }
      clearPolygonDisplay();
      logConsole(err.message || String(err), true);
    }
  }

  async function loadEsriLayer() {
    try {
      els.polyCells.checked = false;
      resetPolygonColors();
      clearPolygonDisplay();
      clearConsole();
      logConsole("Checking layer…");
      const info = await POLY.inspectLayer(els.esriUrl.value);
      polygonLayerInfo = info;
      els.classField.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Select a field";
      els.classField.appendChild(placeholder);
      info.fields.forEach((field) => {
        const option = document.createElement("option");
        option.value = field.name;
        option.textContent = field.alias && field.alias !== field.name
          ? `${field.alias} (${field.name})`
          : field.name;
        els.classField.appendChild(option);
      });
      els.classField.disabled = false;
      logConsole(
        `Polygon layer “${info.name}”. ${info.fields.length} attribute(s) available — select a classification field, then turn on Polygon to cells.`
      );
    } catch (err) {
      polygonLayerInfo = null;
      els.classField.innerHTML = "";
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Load a polygon layer first";
      els.classField.appendChild(option);
      els.classField.disabled = true;
      logConsole(err.message || String(err), true);
    }
  }

  function redrawOverlays() {
    parentLayer.clearLayers();
    childLayer.clearLayers();
    neighbourLayer.clearLayers();
    selectedLayer.clearLayers();
    extraLabelCodes = [];

    const length = currentLength();
    const view = viewportHk80();
    if (view.east <= view.west || view.north <= view.south) {
      els.status.textContent = "Map is outside HKGeoCode coverage.";
      viewCanvas._redraw();
      return;
    }

    const inView = HKG.codesInBounds(view.west, view.south, view.east, view.north, length);
    if (inView.length > MAX_GRID_CELLS) {
      els.status.textContent =
        `${inView.length.toLocaleString()} ${length}-char cells in view — zoom in to draw all boundaries and identifiers.`;
    } else if (els.showGrid.checked || els.showLabels.checked) {
      const parts = [];
      if (els.showGrid.checked) {
        parts.push(`all ${inView.length} ${length}-char boundaries`);
      }
      if (els.showLabels.checked) {
        parts.push(`${inView.length} identifiers`);
      }
      els.status.textContent = `Showing ${parts.join(" and ")} in view.`;
    } else {
      els.status.textContent = `${HKG.levelLabel(length)} — boundaries and identifiers hidden.`;
    }

    if (selected) {
      addRect(selectedLayer, selected, {
        color: "#c1121f",
        weight: 3,
        fillColor: "#c1121f",
        fillOpacity: 0.18,
        interactive: false,
      });
      extraLabelCodes.push(selected);

      if (els.showParent.checked) {
        const parent = HKG.parentCode(selected);
        if (parent && HKG.cellIntersects(parent, view.west, view.south, view.east, view.north)) {
          addRect(parentLayer, parent, {
            color: "#1d4ed8",
            weight: 3,
            dashArray: "8 6",
            fill: false,
            interactive: false,
          });
          extraLabelCodes.push(parent);
        }
      }

      if (els.showNeighbours.checked) {
        const neighbours = HKG.neighbourCodes(selected).filter((code) =>
          HKG.cellIntersects(code, view.west, view.south, view.east, view.north)
        );
        neighbours.forEach((code) => {
          addRect(neighbourLayer, code, {
            color: "#b45309",
            weight: 2,
            fillColor: "#d97706",
            fillOpacity: 0.12,
            interactive: true,
          }).on("click", (event) => {
            L.DomEvent.stopPropagation(event);
            selectCode(code);
          });
          extraLabelCodes.push(code);
        });
        if (neighbours.length) {
          els.status.textContent += ` ${neighbours.length} neighbour(s) in view.`;
        }
      }

      if (els.showChildren.checked) {
        const children = HKG.childCodes(selected).filter((code) =>
          HKG.cellIntersects(code, view.west, view.south, view.east, view.north)
        );
        if (children.length > MAX_GRID_CELLS) {
          els.status.textContent =
            `${children.length.toLocaleString()} child cells in view — zoom in to draw them.`;
        } else {
          children.forEach((code) => {
            addRect(childLayer, code, {
              color: "#0f766e",
              weight: 1,
              fillColor: "#0f766e",
              fillOpacity: 0.06,
              interactive: false,
            });
            extraLabelCodes.push(code);
          });
          if (children.length) {
            els.status.textContent += ` ${children.length} child cell(s) in view.`;
          }
        }
      }
    }

    viewCanvas._redraw();
    updatePanel();
  }

  function updatePanel() {
    if (!selected) {
      els.code.textContent = "Click the map";
      els.level.textContent = HKG.levelLabel(currentLength());
      els.origin.textContent = "—";
      els.parent.textContent = "—";
      els.neighbours.textContent = "—";
      els.goParent.disabled = true;
      els.fitCell.disabled = true;
      els.copy.disabled = true;
      els.clear.disabled = true;
      return;
    }
    const { easting, northing, cellSize } = HKG.hkgeocodeCellOrigin(selected);
    const parent = HKG.parentCode(selected);
    els.code.textContent = selected;
    els.level.textContent = HKG.levelLabel(selected.length);
    els.origin.textContent = `SW E ${easting.toFixed(0)}, N ${northing.toFixed(0)}  ·  ${cellSize} m`;
    els.parent.textContent = parent || "none (district cell)";
    const neighbours = HKG.neighbourCodes(selected);
    els.neighbours.textContent = neighbours.length ? neighbours.join(" ") : "none";
    els.goParent.disabled = !parent;
    els.fitCell.disabled = false;
    els.copy.disabled = false;
    els.clear.disabled = false;
  }

  function fitSelectedCell() {
    if (!selected) {
      return;
    }
    map.fitBounds(cellLatLngs(selected), {
      paddingTopLeft: [56, 56],
      paddingBottomRight: [320, 56],
      maxZoom: MAP_MAX_ZOOM,
      animate: true,
    });
  }

  function clearSelection() {
    selected = null;
    history.replaceState(null, "", location.pathname + location.search);
    redrawOverlays();
  }

  function selectCode(code, { fly = false, writeHash = true } = {}) {
    selected = HKG.normalizeCode(code);
    if (writeHash) {
      history.replaceState(null, "", `#code=${selected}`);
    }
    if (fly) {
      fitSelectedCell();
    }
    redrawOverlays();
  }

  let lastHoverLatLng = null;

  function updateHoverCode(latlng) {
    if (!latlng) {
      els.hover.textContent = "—";
      return;
    }
    try {
      const [easting, northing] = toHk80(latlng);
      els.hover.textContent = HKG.hk80ToHkgeocode(easting, northing, currentLength());
    } catch (err) {
      els.hover.textContent = "—";
    }
  }

  map.on("mousemove", (event) => {
    lastHoverLatLng = event.latlng;
    updateHoverCode(event.latlng);
  });

  map.on("mouseout", () => {
    lastHoverLatLng = null;
    els.hover.textContent = "—";
  });

  map.on("click", (event) => {
    try {
      const [easting, northing] = toHk80(event.latlng);
      const code = HKG.hk80ToHkgeocode(easting, northing, currentLength());
      selectCode(code);
    } catch (err) {
      if (err instanceof HKG.HKGeoCodeError) {
        els.status.textContent = "Click is outside HKGeoCode coverage.";
      } else {
        throw err;
      }
    }
  });

  map.on("moveend zoomend", () => {
    updateHoverCode(lastHoverLatLng);
    redrawOverlays();
    syncPolygonCells();
  });

  els.basemap.addEventListener("change", () => {
    setBasemap(els.basemap.value);
  });

  els.clear.addEventListener("click", clearSelection);

  els.resolution.addEventListener("change", () => {
    updateHoverCode(lastHoverLatLng);
    redrawOverlays();
    syncPolygonCells();
  });
  els.showGrid.addEventListener("change", redrawOverlays);
  els.showLabels.addEventListener("change", redrawOverlays);
  els.showParent.addEventListener("change", redrawOverlays);
  els.showChildren.addEventListener("change", redrawOverlays);
  els.showNeighbours.addEventListener("change", redrawOverlays);

  els.loadEsri.addEventListener("click", loadEsriLayer);
  els.esriUrl.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      loadEsriLayer();
    }
  });
  els.polyCells.addEventListener("change", () => {
    syncPolygonCells();
  });
  els.classField.addEventListener("change", () => {
    if (!els.classField.value) {
      return;
    }
    resetPolygonColors();
    if (els.polyCells.checked) {
      syncPolygonCells();
    } else {
      logConsole(
        `Classification field “${els.classField.value}”. Turn on Polygon to cells to draw.`
      );
    }
  });

  els.fitCell.addEventListener("click", fitSelectedCell);

  els.goParent.addEventListener("click", () => {
    if (!selected) {
      return;
    }
    const parent = HKG.parentCode(selected);
    if (parent) {
      els.resolution.value = String(parent.length);
      selectCode(parent);
    }
  });

  els.copy.addEventListener("click", async () => {
    if (!selected) {
      return;
    }
    try {
      await navigator.clipboard.writeText(selected);
      els.copy.textContent = "Copied";
      setTimeout(() => {
        els.copy.textContent = "Copy";
      }, 1200);
    } catch (err) {
      els.search.value = selected;
      els.search.select();
    }
  });

  els.search.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") {
      return;
    }
    try {
      const code = HKG.normalizeCode(els.search.value);
      els.resolution.value = String(code.length);
      selectCode(code, { fly: true });
    } catch (err) {
      els.status.textContent = err.message;
    }
  });

  setBasemap(els.basemap.value);

  const hash = new URLSearchParams(location.hash.replace(/^#/, "")).get("code");
  if (hash) {
    try {
      const code = HKG.normalizeCode(hash);
      els.resolution.value = String(code.length);
      selectCode(code, { fly: true, writeHash: false });
    } catch (err) {
      redrawOverlays();
    }
  } else {
    redrawOverlays();
  }
})();
