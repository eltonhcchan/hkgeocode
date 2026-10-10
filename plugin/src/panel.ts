import type { CodeLength } from "./hkgeocode";
import type { PluginState } from "./state";

export interface SelectedCellView {
  code: string;
  level: string;
  parent: string | null;
  childCount: number;
  neighbors: string[];
  easting: number;
  northing: number;
  lat: number;
  lng: number;
}

export interface PanelView extends PluginState {
  /** Length currently drawn, including the automatic zoom rule. */
  effectiveLength: CodeLength;
  status: string;
  statusWarn: boolean;
  message: string | null;
  selected: SelectedCellView | null;
}

export interface PanelModel {
  getView(): PanelView;
  subscribe(listener: () => void): () => void;
  setAutoResolution(value: boolean): void;
  setLength(length: CodeLength): void;
  setFillColor(value: string): void;
  setFillOpacity(value: number): void;
  setLineColor(value: string): void;
  setLineWidth(value: number): void;
  setShowLabels(value: boolean): void;
  setIncludeNeighbors(value: boolean): void;
  setIncludeParent(value: boolean): void;
  goToCode(code: string): void;
  zoomToSelected(): void;
  addAsLayer(): void;
  exportGeoJson(): void;
  exportCsv(): void;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function row(labelText: string, control: HTMLElement): HTMLLabelElement {
  const label = el("label", "hkgeocode-row");
  label.append(el("span", undefined, labelText), control);
  return label;
}

function setIdleValue(input: HTMLInputElement, value: string): void {
  if (document.activeElement === input) {
    return;
  }
  if (input.value !== value) {
    input.value = value;
  }
}

function formatMetres(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

export function mountPanel(container: HTMLElement, model: PanelModel): () => void {
  container.replaceChildren();
  const root = el("div", "hkgeocode-panel");

  const auto = el("input") as HTMLInputElement;
  auto.type = "checkbox";
  const autoLabel = el("label", "hkgeocode-check");
  autoLabel.append(auto, el("span", undefined, "Automatic resolution"));

  const length = el("select") as HTMLSelectElement;
  for (const [value, label] of [
    ["2", "2 km"],
    ["4", "100 m"],
    ["6", "5 m"],
  ] as const) {
    const option = el("option", undefined, label);
    option.value = value;
    length.append(option);
  }

  const fillColor = el("input") as HTMLInputElement;
  fillColor.type = "color";
  const fillOpacity = el("input") as HTMLInputElement;
  fillOpacity.type = "range";
  fillOpacity.min = "0";
  fillOpacity.max = "0.6";
  fillOpacity.step = "0.02";

  const lineColor = el("input") as HTMLInputElement;
  lineColor.type = "color";
  const lineWidth = el("input") as HTMLInputElement;
  lineWidth.type = "range";
  lineWidth.min = "0.5";
  lineWidth.max = "4";
  lineWidth.step = "0.25";

  const showLabels = el("input") as HTMLInputElement;
  showLabels.type = "checkbox";
  const labelsLabel = el("label", "hkgeocode-check");
  labelsLabel.append(showLabels, el("span", undefined, "Show cell IDs"));

  const status = el("p", "hkgeocode-status");
  status.setAttribute("role", "status");

  const message = el("p", "hkgeocode-message");

  const codeInput = el("input") as HTMLInputElement;
  codeInput.type = "text";
  codeInput.spellcheck = false;
  codeInput.autocomplete = "off";
  codeInput.placeholder = "WG73JD";
  codeInput.maxLength = 6;
  const go = el("button", undefined, "Go");
  go.type = "button";
  const goRow = el("div", "hkgeocode-go");
  goRow.append(codeInput, go);

  const selectedCode = el("p", "hkgeocode-code", "No cell selected");
  const copy = el("button", undefined, "Copy ID");
  copy.type = "button";
  const codeRow = el("div", "hkgeocode-code-row");
  codeRow.append(selectedCode, copy);

  const level = el("p", "hkgeocode-detail");
  const parent = el("p", "hkgeocode-detail");
  const children = el("p", "hkgeocode-detail");
  const neighbors = el("p", "hkgeocode-detail");
  const origin = el("p", "hkgeocode-detail");
  const center = el("p", "hkgeocode-detail");

  const zoom = el("button", undefined, "Zoom to cell");
  zoom.type = "button";

  const includeNeighbors = el("input") as HTMLInputElement;
  includeNeighbors.type = "checkbox";
  const neighborsLabel = el("label", "hkgeocode-check");
  neighborsLabel.append(includeNeighbors, el("span", undefined, "Include selected cell neighbors"));

  const includeParent = el("input") as HTMLInputElement;
  includeParent.type = "checkbox";
  const parentLabel = el("label", "hkgeocode-check");
  parentLabel.append(includeParent, el("span", undefined, "Include selected cell parent"));

  const addLayer = el("button", undefined, "Add grid as layer");
  addLayer.type = "button";
  const exportJson = el("button", undefined, "Export GeoJSON");
  exportJson.type = "button";
  const exportCsv = el("button", undefined, "Export CSV");
  exportCsv.type = "button";
  const actions = el("div", "hkgeocode-actions");
  actions.append(addLayer, exportJson, exportCsv);

  const hint = el("p", "hkgeocode-hint", "Click the map to identify an HKGeoCode cell.");

  root.append(
    autoLabel,
    row("Length", length),
    row("Fill color", fillColor),
    row("Fill opacity", fillOpacity),
    row("Outline color", lineColor),
    row("Outline width", lineWidth),
    labelsLabel,
    status,
    el("h3", undefined, "Selected cell"),
    row("Code", goRow),
    codeRow,
    level,
    parent,
    children,
    neighbors,
    origin,
    center,
    zoom,
    neighborsLabel,
    parentLabel,
    message,
    actions,
    hint,
  );
  container.append(root);

  auto.addEventListener("change", () => model.setAutoResolution(auto.checked));
  length.addEventListener("change", () => {
    const next = Number(length.value);
    if (next === 2 || next === 4 || next === 6) {
      model.setLength(next);
    }
  });
  fillColor.addEventListener("input", () => model.setFillColor(fillColor.value));
  fillOpacity.addEventListener("input", () => model.setFillOpacity(Number(fillOpacity.value)));
  lineColor.addEventListener("input", () => model.setLineColor(lineColor.value));
  lineWidth.addEventListener("input", () => model.setLineWidth(Number(lineWidth.value)));
  showLabels.addEventListener("change", () => model.setShowLabels(showLabels.checked));
  includeNeighbors.addEventListener("change", () =>
    model.setIncludeNeighbors(includeNeighbors.checked),
  );
  includeParent.addEventListener("change", () => model.setIncludeParent(includeParent.checked));

  const submitCode = () => model.goToCode(codeInput.value);
  go.addEventListener("click", submitCode);
  codeInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitCode();
    }
  });
  copy.addEventListener("click", () => {
    const view = model.getView();
    if (!view.selected) {
      return;
    }
    const text = view.selected.code;
    const done = () => {
      copy.textContent = "Copied";
      window.setTimeout(() => {
        copy.textContent = "Copy ID";
      }, 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
      return;
    }
    fallbackCopy(text, done);
  });
  zoom.addEventListener("click", () => model.zoomToSelected());
  addLayer.addEventListener("click", () => model.addAsLayer());
  exportJson.addEventListener("click", () => model.exportGeoJson());
  exportCsv.addEventListener("click", () => model.exportCsv());

  const sync = () => {
    const view = model.getView();
    auto.checked = view.autoResolution;
    length.disabled = view.autoResolution;
    if (length.value !== String(view.effectiveLength)) {
      length.value = String(view.effectiveLength);
    }
    setIdleValue(fillColor, view.fillColor);
    setIdleValue(fillOpacity, String(view.fillOpacity));
    setIdleValue(lineColor, view.lineColor);
    setIdleValue(lineWidth, String(view.lineWidth));
    showLabels.checked = view.showLabels;
    includeNeighbors.checked = view.includeNeighbors;
    includeParent.checked = view.includeParent;
    status.textContent = view.status;
    status.classList.toggle("hkgeocode-status-warn", view.statusWarn);
    message.textContent = view.message ?? "";
    message.hidden = !view.message;
    const selected = view.selected;
    copy.disabled = !selected;
    zoom.disabled = !selected;
    if (!selected) {
      selectedCode.textContent = "No cell selected";
      level.textContent = "";
      parent.textContent = "";
      children.textContent = "";
      neighbors.textContent = "";
      origin.textContent = "";
      center.textContent = "";
      return;
    }
    selectedCode.textContent = selected.code;
    level.textContent = selected.level;
    parent.textContent = `Parent: ${selected.parent ?? "—"}`;
    children.textContent = `Children: ${selected.childCount}`;
    neighbors.textContent = `Neighbors: ${
      selected.neighbors.length > 0 ? selected.neighbors.join(", ") : "—"
    }`;
    origin.textContent = `HK80 origin: E ${formatMetres(selected.easting)}  N ${formatMetres(selected.northing)}`;
    center.textContent = `Center: ${selected.lat.toFixed(6)}, ${selected.lng.toFixed(6)}`;
  };

  sync();
  const unsubscribe = model.subscribe(sync);
  return () => {
    unsubscribe();
    root.remove();
  };
}

function fallbackCopy(text: string, done: () => void): void {
  const area = document.createElement("textarea");
  area.value = text;
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
  done();
}
