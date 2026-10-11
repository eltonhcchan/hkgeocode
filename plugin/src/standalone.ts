import maplibregl, { type IControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { plugin } from "./geolibre";
import type { GeoLibreAppAPI, GeoLibreMapControlPosition } from "./host-api";
import type { CellFeatureCollection } from "./grid";
import "./standalone.css";

const mapContainer = document.querySelector<HTMLElement>("#map");
const sidebar = document.querySelector<HTMLElement>("#sidebar");
if (!mapContainer || !sidebar) {
  throw new Error("Standalone page is missing #map or #sidebar");
}

const map = new maplibregl.Map({
  container: mapContainer,
  style: "https://tiles.openfreemap.org/styles/positron",
  center: [114.17, 22.32],
  zoom: 11,
});

const app: GeoLibreAppAPI = {
  addMapControl(control: IControl, position?: GeoLibreMapControlPosition) {
    map.addControl(control, position ?? "top-right");
    return true;
  },
  removeMapControl(control: IControl) {
    map.removeControl(control);
  },
  registerRightPanel(panel) {
    const cleanup = panel.render(sidebar);
    return () => {
      if (typeof cleanup === "function") {
        cleanup();
      }
      sidebar.replaceChildren();
    };
  },
  openRightPanel() {
    return true;
  },
  registerExternalNativeLayer(layer) {
    const sourceId = `hkgeocode-added-${layer.id}`;
    const data = layer.geojson ?? { type: "FeatureCollection", features: [] };
    const existing = map.getSource(sourceId) as maplibregl.GeoJSONSource | undefined;
    if (existing) {
      existing.setData(data as CellFeatureCollection);
      return;
    }
    map.addSource(sourceId, { type: "geojson", data });
    map.addLayer({
      id: `${sourceId}-fill`,
      type: "fill",
      source: sourceId,
      paint: {
        "fill-color": layer.style.fillColor ?? "#b45309",
        "fill-opacity": Math.max(layer.style.fillOpacity ?? 0.35, 0.35),
      },
    });
    map.addLayer({
      id: `${sourceId}-line`,
      type: "line",
      source: sourceId,
      paint: {
        "line-color": layer.style.strokeColor ?? "#b45309",
        "line-width": Math.max(layer.style.strokeWidth ?? 2, 2),
      },
    });
  },
};

plugin.activate(app);
const params = new URLSearchParams(window.location.search);
if (params.has("hkgeocode")) {
  plugin.handleUrlParameters?.(app, params);
}
