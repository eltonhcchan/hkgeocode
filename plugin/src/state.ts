import { type CodeLength, isCodeLength, normalizeCode } from "./hkgeocode";

export interface PluginState {
  autoResolution: boolean;
  length: CodeLength;
  fillColor: string;
  fillOpacity: number;
  lineColor: string;
  lineWidth: number;
  showLabels: boolean;
  includeNeighbors: boolean;
  includeParent: boolean;
  selectedCode: string | null;
}

export const DEFAULT_STATE: PluginState = {
  autoResolution: true,
  length: 2,
  fillColor: "#0f766e",
  fillOpacity: 0.12,
  lineColor: "#0f766e",
  lineWidth: 1.25,
  showLabels: true,
  includeNeighbors: false,
  includeParent: false,
  selectedCode: null,
};

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function normalizePluginState(value: unknown): PluginState | null {
  if (!isRecord(value)) {
    return null;
  }
  if (typeof value.autoResolution !== "boolean") {
    return null;
  }
  if (typeof value.length !== "number" || !isCodeLength(value.length)) {
    return null;
  }
  if (typeof value.fillColor !== "string" || !HEX_COLOR.test(value.fillColor)) {
    return null;
  }
  if (typeof value.lineColor !== "string" || !HEX_COLOR.test(value.lineColor)) {
    return null;
  }
  if (
    typeof value.fillOpacity !== "number" ||
    !Number.isFinite(value.fillOpacity) ||
    value.fillOpacity < 0 ||
    value.fillOpacity > 1
  ) {
    return null;
  }
  if (
    typeof value.lineWidth !== "number" ||
    !Number.isFinite(value.lineWidth) ||
    value.lineWidth < 0 ||
    value.lineWidth > 12
  ) {
    return null;
  }
  if (typeof value.showLabels !== "boolean") {
    return null;
  }
  if (typeof value.includeNeighbors !== "boolean") {
    return null;
  }
  if (typeof value.includeParent !== "boolean") {
    return null;
  }
  let selectedCode: string | null = null;
  if (value.selectedCode !== null) {
    if (typeof value.selectedCode !== "string") {
      return null;
    }
    try {
      selectedCode = normalizeCode(value.selectedCode);
    } catch {
      return null;
    }
  }
  return {
    autoResolution: value.autoResolution,
    length: value.length,
    fillColor: value.fillColor,
    fillOpacity: value.fillOpacity,
    lineColor: value.lineColor,
    lineWidth: value.lineWidth,
    showLabels: value.showLabels,
    includeNeighbors: value.includeNeighbors,
    includeParent: value.includeParent,
    selectedCode,
  };
}
