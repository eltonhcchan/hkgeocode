import type { GeoJSONSource, LngLatLike, Map as MaplibreMap, MapMouseEvent } from "maplibre-gl";
import {
  CellLimitError,
  EMPTY_COLLECTION,
  VIEWPORT_CELL_LIMIT,
  cellCountLabel,
  cellFeature,
  exportBaseName,
  gridForHk80Bounds,
  hk80BoundsFromLngLatBounds,
  layerName,
  projectToHk80,
  projectToLngLat,
  toCsv,
  tooManyCellsLabel,
  type CellFeatureCollection,
} from "./grid";
import type { GeoLibreAppAPI } from "./host-api";
import {
  HKGeoCodeError,
  childCount,
  hkgeocodeCellOrigin,
  hk80ToHkgeocode,
  lengthForZoom,
  levelLabel,
  neighborCodes,
  normalizeCode,
  parentCode,
  type CodeLength,
} from "./hkgeocode";
import type { PanelModel, PanelView, SelectedCellView } from "./panel";
import { DEFAULT_STATE, normalizePluginState, type PluginState } from "./state";

const GRID_SOURCE = "hkgeocode-grid";
const FILL_LAYER = "hkgeocode-grid-fill";
const LINE_LAYER = "hkgeocode-grid-line";
const LABEL_LAYER = "hkgeocode-grid-label";
const SELECTION_SOURCE = "hkgeocode-selection";
const SELECTION_LAYER = "hkgeocode-selection-line";
const NEIGHBOR_SOURCE = "hkgeocode-neighbors";
const NEIGHBOR_LAYER = "hkgeocode-neighbors-line";
const PARENT_SOURCE = "hkgeocode-parent";
const PARENT_LAYER = "hkgeocode-parent-line";

const LAYER_IDS = [
  LABEL_LAYER,
  SELECTION_LAYER,
  NEIGHBOR_LAYER,
  PARENT_LAYER,
  LINE_LAYER,
  FILL_LAYER,
];
const SOURCE_IDS = [GRID_SOURCE, SELECTION_SOURCE, NEIGHBOR_SOURCE, PARENT_SOURCE];

export class HkgeocodeSession implements PanelModel {
  private state: PluginState;
  private collection: CellFeatureCollection = EMPTY_COLLECTION;
  private count = 0;
  private limited = false;
  private ready = false;
  private message: string | null = null;
  private pendingFit: string | null = null;
  private frame = 0;
  private bootFrame = 0;
  private fitTimer = 0;
  private fitStable = 0;
  private previousCursor = "";
  private listeners = new Set<() => void>();
  private readonly onMoveEnd = () => {
    if (this.frame) {
      cancelAnimationFrame(this.frame);
    }
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.refreshGrid();
    });
  };
  private readonly onClick = (event: MapMouseEvent) => {
    this.identify(event.lngLat.lng, event.lngLat.lat);
  };
  /**
   * `styledata` can fire before `isStyleLoaded()` flips true. Defer one frame
   * so the style has settled, and again after a basemap swap removes sources.
   */
  private readonly scheduleBoot = () => {
    if (this.ready && this.map.isStyleLoaded() && this.map.getSource(GRID_SOURCE)) {
      return;
    }
    if (this.bootFrame) {
      return;
    }
    this.bootFrame = requestAnimationFrame(() => {
      this.bootFrame = 0;
      this.boot();
      if (!this.map.isStyleLoaded() || this.map.getSource(GRID_SOURCE)) {
        return;
      }
      this.ensureLayers();
      this.pushGrid();
      this.pushSelection();
      this.applyPaint();
    });
  };

  /** Draw as soon as the style is ready. `load` waits for tiles, which may never arrive. */
  private readonly boot = () => {
    if (!this.map.isStyleLoaded() || this.ready) {
      return;
    }
    try {
      this.ensureLayers();
      this.refreshGrid();
      this.pushSelection();
      this.flushPendingFit();
    } catch (error) {
      this.ready = true;
      this.collection = EMPTY_COLLECTION;
      this.count = 0;
      this.message = error instanceof Error ? error.message : "Could not draw the HKGeoCode grid.";
      this.emit();
    }
  };

  constructor(
    private readonly map: MaplibreMap,
    initial: PluginState | null,
    private readonly app: GeoLibreAppAPI,
  ) {
    this.state = initial ? { ...initial } : { ...DEFAULT_STATE };
  }

  start(): void {
    this.previousCursor = this.map.getCanvas().style.cursor;
    this.map.getCanvas().style.cursor = "crosshair";
    this.map.on("moveend", this.onMoveEnd);
    this.map.on("zoomend", this.onMoveEnd);
    this.map.on("click", this.onClick);
    this.map.on("styledata", this.scheduleBoot);
    this.map.on("idle", this.scheduleBoot);
    this.map.on("load", this.scheduleBoot);
    this.scheduleBoot();
  }

  destroy(): void {
    if (this.frame) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
    if (this.bootFrame) {
      cancelAnimationFrame(this.bootFrame);
      this.bootFrame = 0;
    }
    if (this.fitTimer) {
      window.clearTimeout(this.fitTimer);
      this.fitTimer = 0;
    }
    this.map.off("moveend", this.onMoveEnd);
    this.map.off("zoomend", this.onMoveEnd);
    this.map.off("click", this.onClick);
    this.map.off("styledata", this.scheduleBoot);
    this.map.off("idle", this.scheduleBoot);
    this.map.off("load", this.scheduleBoot);
    this.map.getCanvas().style.cursor = this.previousCursor;
    for (const id of LAYER_IDS) {
      if (this.map.getLayer(id)) {
        this.map.removeLayer(id);
      }
    }
    for (const id of SOURCE_IDS) {
      if (this.map.getSource(id)) {
        this.map.removeSource(id);
      }
    }
    this.listeners.clear();
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getState(): PluginState {
    return {
      autoResolution: this.state.autoResolution,
      length: this.state.length,
      fillColor: this.state.fillColor,
      fillOpacity: this.state.fillOpacity,
      lineColor: this.state.lineColor,
      lineWidth: this.state.lineWidth,
      showLabels: this.state.showLabels,
      includeNeighbors: this.state.includeNeighbors,
      includeParent: this.state.includeParent,
      selectedCode: this.state.selectedCode,
    };
  }

  applyState(value: unknown): boolean {
    const next = normalizePluginState(value);
    if (!next) {
      return false;
    }
    this.state = next;
    this.message = null;
    this.refreshGrid();
    this.pushSelection();
    this.emit();
    return true;
  }

  getView(): PanelView {
    let status = "Loading grid…";
    if (this.ready && this.limited) {
      status = tooManyCellsLabel(VIEWPORT_CELL_LIMIT);
    } else if (this.ready && this.count === 0) {
      status = "Map is outside HKGeoCode coverage.";
    } else if (this.ready) {
      status = cellCountLabel(this.count);
    }
    return {
      ...this.getState(),
      effectiveLength: this.currentLength(),
      status,
      statusWarn: this.limited,
      message: this.message,
      selected: this.state.selectedCode ? describeCell(this.state.selectedCode) : null,
    };
  }

  setAutoResolution(value: boolean): void {
    if (!value) {
      this.state.length = this.currentLength();
    }
    this.state.autoResolution = value;
    this.refreshGrid();
  }

  setLength(length: CodeLength): void {
    this.state.autoResolution = false;
    this.state.length = length;
    this.refreshGrid();
  }

  setFillColor(value: string): void {
    this.state.fillColor = value;
    this.applyPaint();
  }

  setFillOpacity(value: number): void {
    this.state.fillOpacity = value;
    this.applyPaint();
  }

  setLineColor(value: string): void {
    this.state.lineColor = value;
    this.applyPaint();
  }

  setLineWidth(value: number): void {
    this.state.lineWidth = value;
    this.applyPaint();
  }

  setShowLabels(value: boolean): void {
    this.state.showLabels = value;
    this.applyPaint();
  }

  setIncludeNeighbors(value: boolean): void {
    this.state.includeNeighbors = value;
    this.pushSelection();
    this.emit();
  }

  setIncludeParent(value: boolean): void {
    this.state.includeParent = value;
    this.pushSelection();
    this.emit();
  }

  goToCode(raw: string): void {
    try {
      const code = normalizeCode(raw);
      this.state.selectedCode = code;
      this.message = null;
      if (!this.state.autoResolution) {
        this.state.length = code.length as CodeLength;
      }
      this.pendingFit = code;
      this.refreshGrid();
      this.pushSelection();
      this.flushPendingFit();
      this.emit();
    } catch (error) {
      this.message = error instanceof HKGeoCodeError ? error.message : "Invalid HKGeoCode";
      this.emit();
    }
  }

  zoomToSelected(): void {
    if (!this.state.selectedCode) {
      return;
    }
    this.pendingFit = this.state.selectedCode;
    this.flushPendingFit();
  }

  addAsLayer(): void {
    const length = this.currentLength();
    const name = layerName(length);
    const data = this.collection;
    if (this.app.registerExternalNativeLayer) {
      this.app.registerExternalNativeLayer({
        id: "hkgeocode-grid-layer",
        name,
        geojson: data,
        nativeLayerIds: [],
        sourceIds: [],
        opacity: this.state.fillOpacity,
        style: {
          fillColor: this.state.fillColor,
          strokeColor: this.state.lineColor,
          strokeWidth: this.state.lineWidth,
          fillOpacity: this.state.fillOpacity,
        },
      });
      this.message = "Added the grid as a layer.";
      this.emit();
      return;
    }
    if (this.app.addGeoJsonLayer) {
      this.app.addGeoJsonLayer(name, data);
      this.message = "Added the grid as a layer.";
      this.emit();
      return;
    }
    this.exportGeoJson();
  }

  exportGeoJson(): void {
    download(
      `${exportBaseName(this.currentLength())}.geojson`,
      "application/geo+json",
      JSON.stringify(this.collection),
    );
  }

  exportCsv(): void {
    download(
      `${exportBaseName(this.currentLength())}.csv`,
      "text/csv",
      toCsv(this.collection),
    );
  }

  private currentLength(): CodeLength {
    if (this.state.autoResolution) {
      return lengthForZoom(this.map.getZoom());
    }
    return this.state.length;
  }

  private identify(lng: number, lat: number): void {
    const projected = projectToHk80(lng, lat);
    if (!projected) {
      this.message = "Click is outside HKGeoCode coverage.";
      this.emit();
      return;
    }
    try {
      this.state.selectedCode = hk80ToHkgeocode(projected[0], projected[1], this.currentLength());
      this.message = null;
      this.pushSelection();
    } catch (error) {
      if (!(error instanceof HKGeoCodeError)) {
        throw error;
      }
      this.message = "Click is outside HKGeoCode coverage.";
    }
    this.emit();
  }

  private refreshGrid(): void {
    if (!this.map.isStyleLoaded()) {
      return;
    }
    this.ensureLayers();
    const bounds = this.map.getBounds();
    const lngPad = Math.abs(bounds.getEast() - bounds.getWest()) * 0.05;
    const latPad = Math.abs(bounds.getNorth() - bounds.getSouth()) * 0.05;
    const hk80 = hk80BoundsFromLngLatBounds(
      bounds.getWest() - lngPad,
      bounds.getSouth() - latPad,
      bounds.getEast() + lngPad,
      bounds.getNorth() + latPad,
    );
    const length = this.currentLength();
    try {
      this.collection = hk80
        ? gridForHk80Bounds(hk80, length)
        : EMPTY_COLLECTION;
      this.count = this.collection.features.length;
      this.limited = false;
    } catch (error) {
      if (!(error instanceof CellLimitError)) {
        throw error;
      }
      this.collection = EMPTY_COLLECTION;
      this.count = 0;
      this.limited = true;
    }
    this.ready = true;
    this.pushGrid();
    this.applyPaint();
    this.emit();
  }

  private ensureLayers(): void {
    if (!this.map.isStyleLoaded() || this.map.getSource(GRID_SOURCE)) {
      return;
    }
    const empty = EMPTY_COLLECTION;
    this.map.addSource(GRID_SOURCE, { type: "geojson", data: empty });
    this.map.addSource(SELECTION_SOURCE, { type: "geojson", data: empty });
    this.map.addSource(NEIGHBOR_SOURCE, { type: "geojson", data: empty });
    this.map.addSource(PARENT_SOURCE, { type: "geojson", data: empty });
    this.map.addLayer({
      id: FILL_LAYER,
      type: "fill",
      source: GRID_SOURCE,
      paint: {
        "fill-color": this.state.fillColor,
        "fill-opacity": this.state.fillOpacity,
      },
    });
    this.map.addLayer({
      id: LINE_LAYER,
      type: "line",
      source: GRID_SOURCE,
      paint: {
        "line-color": this.state.lineColor,
        "line-width": this.state.lineWidth,
      },
    });
    this.map.addLayer({
      id: PARENT_LAYER,
      type: "line",
      source: PARENT_SOURCE,
      paint: {
        "line-color": "#6d28d9",
        "line-width": 2,
        "line-dasharray": [1, 1],
      },
    });
    this.map.addLayer({
      id: NEIGHBOR_LAYER,
      type: "line",
      source: NEIGHBOR_SOURCE,
      paint: {
        "line-color": "#0369a1",
        "line-width": 2,
        "line-dasharray": [2, 1],
      },
    });
    this.map.addLayer({
      id: SELECTION_LAYER,
      type: "line",
      source: SELECTION_SOURCE,
      paint: {
        "line-color": "#b45309",
        "line-width": 2.5,
      },
    });
    try {
      this.map.addLayer({
        id: LABEL_LAYER,
        type: "symbol",
        source: GRID_SOURCE,
        layout: {
          "text-field": ["get", "hkgeocode"],
          "text-size": 12,
          "text-font": pickTextFont(this.map),
          // Other DGGS grids (Geohash, H3, …) also label cells. Stay out of
          // their collision group so each plugin's ID toggle only affects itself.
          "text-allow-overlap": true,
          "text-ignore-placement": true,
          visibility: this.state.showLabels ? "visible" : "none",
        },
        paint: {
          "text-color": "#134e4a",
          "text-halo-color": "#ffffff",
          "text-halo-width": 1.2,
        },
      });
    } catch {
      // The basemap style has no glyphs for the fallback font. Outlines still draw.
    }
  }

  private pushGrid(): void {
    setGeoJson(this.map, GRID_SOURCE, this.collection);
  }

  private pushSelection(): void {
    const code = this.state.selectedCode;
    setGeoJson(
      this.map,
      SELECTION_SOURCE,
      code ? { type: "FeatureCollection", features: [cellFeature(code)] } : EMPTY_COLLECTION,
    );
    const neighbors =
      code && this.state.includeNeighbors
        ? neighborCodes(code).map((neighbor) => cellFeature(neighbor))
        : [];
    setGeoJson(this.map, NEIGHBOR_SOURCE, { type: "FeatureCollection", features: neighbors });
    const parent = code && this.state.includeParent ? parentCode(code) : null;
    setGeoJson(
      this.map,
      PARENT_SOURCE,
      parent
        ? { type: "FeatureCollection", features: [cellFeature(parent)] }
        : EMPTY_COLLECTION,
    );
  }

  private applyPaint(): void {
    if (!this.map.getLayer(FILL_LAYER)) {
      return;
    }
    this.map.setPaintProperty(FILL_LAYER, "fill-color", this.state.fillColor);
    this.map.setPaintProperty(FILL_LAYER, "fill-opacity", this.state.fillOpacity);
    this.map.setPaintProperty(LINE_LAYER, "line-color", this.state.lineColor);
    this.map.setPaintProperty(LINE_LAYER, "line-width", this.state.lineWidth);
    if (this.map.getLayer(LABEL_LAYER)) {
      this.map.setLayoutProperty(
        LABEL_LAYER,
        "visibility",
        this.state.showLabels ? "visible" : "none",
      );
    }
  }

  /**
   * The basemap style can apply its own center after the first fit, which
   * puts the camera back on the starting view. Re-apply until it stays.
   */
  private flushPendingFit(): void {
    if (!this.pendingFit || this.fitTimer) {
      return;
    }
    let started = 0;
    const tick = () => {
      this.fitTimer = 0;
      if (!this.pendingFit) {
        return;
      }
      if (!this.map.isStyleLoaded()) {
        this.fitTimer = window.setTimeout(tick, 100);
        return;
      }
      if (!started) {
        started = performance.now();
      }
      const matched = this.jumpToPending();
      this.fitStable = matched ? this.fitStable + 1 : 0;
      if (this.fitStable >= 4 || performance.now() - started > 1500) {
        this.pendingFit = null;
        this.fitStable = 0;
        this.refreshGrid();
        return;
      }
      this.fitTimer = window.setTimeout(tick, 50);
    };
    tick();
  }

  private jumpToPending(): boolean {
    const code = this.pendingFit;
    if (!code) {
      return false;
    }
    const ring = cellFeature(code).geometry.coordinates[0];
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    for (const position of ring) {
      west = Math.min(west, position[0]);
      south = Math.min(south, position[1]);
      east = Math.max(east, position[0]);
      north = Math.max(north, position[1]);
    }
    const camera = this.map.cameraForBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding: 32, maxZoom: 22 },
    );
    if (!camera?.center || camera.zoom == null) {
      return false;
    }
    const center = this.map.getCenter();
    const target = readLngLat(camera.center);
    const close =
      Math.abs(this.map.getZoom() - camera.zoom) < 0.05 &&
      Math.abs(center.lng - target.lng) < 1e-5 &&
      Math.abs(center.lat - target.lat) < 1e-5;
    if (!close) {
      this.map.jumpTo({ center: target, zoom: camera.zoom });
      this.refreshGrid();
    }
    return close;
  }

  private emit(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }
}

function describeCell(code: string): SelectedCellView {
  const { easting, northing, cellSize } = hkgeocodeCellOrigin(code);
  const center = projectToLngLat(easting + cellSize / 2, northing + cellSize / 2) ?? [0, 0];
  return {
    code,
    level: levelLabel(code.length as CodeLength),
    parent: parentCode(code),
    childCount: childCount(code),
    neighbors: neighborCodes(code),
    easting,
    northing,
    lng: center[0],
    lat: center[1],
  };
}

function readLngLat(value: LngLatLike): { lng: number; lat: number } {
  if (Array.isArray(value)) {
    return { lng: value[0], lat: value[1] };
  }
  if ("lng" in value) {
    return { lng: value.lng, lat: value.lat };
  }
  return { lng: value.lon, lat: value.lat };
}

function setGeoJson(map: MaplibreMap, sourceId: string, data: CellFeatureCollection): void {
  const source = map.getSource(sourceId) as GeoJSONSource | undefined;
  source?.setData(data);
}

function fontNames(value: unknown): string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  if (value[0] === "literal") {
    return fontNames(value[1]);
  }
  if (value.every((item) => typeof item === "string")) {
    return value as string[];
  }
  return null;
}

function pickTextFont(map: MaplibreMap): string[] {
  const layers = map.getStyle()?.layers ?? [];
  for (const layer of layers) {
    if (layer.type !== "symbol" || !layer.layout) {
      continue;
    }
    const names = fontNames(layer.layout["text-font"]);
    if (names) {
      return names;
    }
  }
  return ["Open Sans Regular", "Arial Unicode MS Regular"];
}

function download(filename: string, mime: string, body: string): void {
  const blob = new Blob([body], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
