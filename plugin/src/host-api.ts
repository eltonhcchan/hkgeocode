/**
 * Slice of the GeoLibre host contract this plugin uses.
 * Field names match opengeos/geolibre-plugin-template and docs/plugin-api.md.
 */

import type { IControl } from "maplibre-gl";
import type { CellFeatureCollection } from "./grid";

export type GeoLibreMapControlPosition =
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right";

export interface GeoLibreNativeLayerStyle {
  fillColor?: string;
  strokeColor?: string;
  strokeWidth?: number;
  fillOpacity?: number;
}

export interface GeoLibreNativeLayerRegistration {
  id: string;
  name: string;
  geojson?: CellFeatureCollection;
  nativeLayerIds: string[];
  sourceIds: string[];
  opacity: number;
  style: GeoLibreNativeLayerStyle;
}

export interface GeoLibreRightPanelRegistration {
  id: string;
  title: string;
  defaultWidth?: number;
  render: (container: HTMLElement) => void | (() => void);
}

export interface GeoLibreAppAPI {
  addMapControl: (control: IControl, position?: GeoLibreMapControlPosition) => boolean;
  removeMapControl: (control: IControl) => void;
  addGeoJsonLayer?: (name: string, data: CellFeatureCollection, sourcePath?: string) => string;
  registerExternalNativeLayer?: (layer: GeoLibreNativeLayerRegistration) => void;
  registerRightPanel?: (panel: GeoLibreRightPanelRegistration) => () => void;
  openRightPanel?: (id: string) => boolean;
}

export interface GeoLibrePlugin {
  id: string;
  name: string;
  version: string;
  urlParameterNames?: string[];
  activate: (app: GeoLibreAppAPI) => boolean | void;
  deactivate: (app: GeoLibreAppAPI) => void;
  handleUrlParameters?: (app: GeoLibreAppAPI, params: URLSearchParams) => void | Promise<void>;
  getMapControlPosition?: () => GeoLibreMapControlPosition;
  setMapControlPosition?: (
    app: GeoLibreAppAPI,
    position: GeoLibreMapControlPosition,
  ) => boolean | void;
  getProjectState?: () => unknown;
  applyProjectState?: (app: GeoLibreAppAPI, state: unknown) => boolean | void;
}
