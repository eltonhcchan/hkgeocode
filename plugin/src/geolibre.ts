import type { IControl, Map as MaplibreMap } from "maplibre-gl";
import type { GeoLibreAppAPI, GeoLibreMapControlPosition, GeoLibrePlugin } from "./host-api";
import { mountPanel } from "./panel";
import { HkgeocodeSession } from "./session";
import { normalizePluginState, type PluginState } from "./state";
import "./styles.css";

export const PLUGIN_ID = "hkgeocode";
export const PLUGIN_NAME = "HKGeoCode";
export const PLUGIN_VERSION = "0.1.0";
export const URL_PARAM = "hkgeocode";
const PANEL_ID = "hkgeocode";

class HkgeocodeControl implements IControl {
  session: HkgeocodeSession | null = null;
  private container: HTMLDivElement | null = null;
  private disposePanel: (() => void) | null = null;

  constructor(
    private readonly initialState: PluginState | null,
    private readonly app: GeoLibreAppAPI,
  ) {}

  onAdd(map: MaplibreMap): HTMLElement {
    this.container = document.createElement("div");
    this.container.className = "hkgeocode-control";
    this.session = new HkgeocodeSession(map, this.initialState, this.app);
    this.session.start();
    const unregister = this.app.registerRightPanel?.({
      id: PANEL_ID,
      title: PLUGIN_NAME,
      defaultWidth: 320,
      render: (container) => {
        if (!this.session) {
          return;
        }
        return mountPanel(container, this.session);
      },
    });
    if (unregister) {
      this.disposePanel = unregister;
      this.app.openRightPanel?.(PANEL_ID);
      this.container.style.display = "none";
    } else if (this.session) {
      this.container.classList.add("maplibregl-ctrl", "maplibregl-ctrl-group", "hkgeocode-fallback");
      this.disposePanel = mountPanel(this.container, this.session);
    }
    return this.container;
  }

  onRemove(): void {
    this.disposePanel?.();
    this.disposePanel = null;
    this.session?.destroy();
    this.session = null;
    this.container?.remove();
    this.container = null;
  }
}

let control: HkgeocodeControl | null = null;
let position: GeoLibreMapControlPosition = "top-right";
let pendingState: PluginState | null = null;
let pendingCode: string | null = null;

function activeSession(): HkgeocodeSession | null {
  return control?.session ?? null;
}

export const plugin: GeoLibrePlugin = {
  id: PLUGIN_ID,
  name: PLUGIN_NAME,
  version: PLUGIN_VERSION,
  urlParameterNames: [URL_PARAM],
  activate(app) {
    control = control ?? new HkgeocodeControl(pendingState, app);
    const added = app.addMapControl(control, position);
    if (!added) {
      control = null;
      return false;
    }
    if (pendingCode && control.session) {
      control.session.goToCode(pendingCode);
      pendingCode = null;
    }
  },
  handleUrlParameters(_app, params) {
    const code = params.get(URL_PARAM);
    if (!code) {
      return;
    }
    if (control?.session) {
      control.session.goToCode(code);
      return;
    }
    pendingCode = code;
  },
  deactivate(app) {
    pendingState = activeSession()?.getState() ?? pendingState;
    if (!control) {
      return;
    }
    app.removeMapControl(control);
    control = null;
  },
  getMapControlPosition() {
    return position;
  },
  setMapControlPosition(app, nextPosition) {
    position = nextPosition;
    if (!control) {
      return;
    }
    const state = control.session?.getState() ?? pendingState;
    app.removeMapControl(control);
    control = new HkgeocodeControl(state, app);
    const added = app.addMapControl(control, position);
    if (!added) {
      pendingState = state;
      control = null;
      return false;
    }
  },
  getProjectState() {
    return activeSession()?.getState() ?? pendingState ?? undefined;
  },
  applyProjectState(_app, state) {
    const normalized = normalizePluginState(state);
    if (!normalized) {
      return false;
    }
    pendingState = normalized;
    if (control?.session) {
      return control.session.applyState(normalized);
    }
    return true;
  },
};

export default plugin;
