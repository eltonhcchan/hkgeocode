/**
 * HK80 ↔ WGS84 cell polygons for the current map view.
 * The PROJ string matches js/explorer.js.
 */

import proj4 from "proj4";
import {
  type CodeLength,
  type CellOrigin,
  countCodesInBounds,
  codesInBounds,
  hkgeocodeCellOrigin,
  levelLabel,
} from "./hkgeocode";

export const VIEWPORT_CELL_LIMIT = 12_000;

/** Hong Kong 1980 Grid, EPSG:2326. */
export const HK80_PROJ =
  "+proj=tmerc +lat_0=22.31213333333334 +lon_0=114.1785555555556 +k=1 " +
  "+x_0=836694.05 +y_0=819069.8 +ellps=intl " +
  "+towgs84=-162.619,-276.959,-161.764,0.067753,-2.24365,-1.15883,-1.09425 " +
  "+units=m +no_defs";

proj4.defs("EPSG:2326", HK80_PROJ);

export class CellLimitError extends Error {
  readonly limit: number;

  constructor(limit: number) {
    super(`HKGeoCode cell limit exceeded: ${limit}`);
    this.name = "CellLimitError";
    this.limit = limit;
  }
}

export interface Hk80Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface CellProperties {
  hkgeocode: string;
  length: number;
  easting: number;
  northing: number;
  center_lng: number;
  center_lat: number;
}

export interface PolygonGeometry {
  type: "Polygon";
  coordinates: number[][][];
}

export interface CellFeature {
  type: "Feature";
  id: string;
  properties: CellProperties;
  geometry: PolygonGeometry;
}

export interface CellFeatureCollection {
  type: "FeatureCollection";
  features: CellFeature[];
}

export const EMPTY_COLLECTION: CellFeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

function asPair(projected: unknown): [number, number] | null {
  if (Array.isArray(projected) && projected.length >= 2) {
    const x = Number(projected[0]);
    const y = Number(projected[1]);
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }
  if (typeof projected === "object" && projected !== null && "x" in projected && "y" in projected) {
    const point = projected as { x: unknown; y: unknown };
    const x = Number(point.x);
    const y = Number(point.y);
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }
  return null;
}

export function projectToHk80(lng: number, lat: number): [number, number] | null {
  try {
    return asPair(proj4("EPSG:4326", "EPSG:2326", [lng, lat]));
  } catch {
    return null;
  }
}

export function projectToLngLat(easting: number, northing: number): [number, number] | null {
  try {
    return asPair(proj4("EPSG:2326", "EPSG:4326", [easting, northing]));
  } catch {
    return null;
  }
}

export function hk80BoundsFromLngLatBounds(
  west: number,
  south: number,
  east: number,
  north: number,
): Hk80Bounds | null {
  const corners: Array<[number, number]> = [
    [west, south],
    [west, north],
    [east, north],
    [east, south],
  ];
  const projected: Array<[number, number]> = [];
  for (const [lng, lat] of corners) {
    const point = projectToHk80(lng, lat);
    if (point) {
      projected.push(point);
    }
  }
  if (projected.length === 0) {
    return null;
  }
  const eastings = projected.map((point) => point[0]);
  const northings = projected.map((point) => point[1]);
  return {
    west: Math.min(...eastings),
    south: Math.min(...northings),
    east: Math.max(...eastings),
    north: Math.max(...northings),
  };
}

export function cellFeature(code: string): CellFeature {
  const origin: CellOrigin = hkgeocodeCellOrigin(code);
  const { easting, northing, cellSize } = origin;
  const corners: Array<[number, number]> = [
    [easting, northing],
    [easting + cellSize, northing],
    [easting + cellSize, northing + cellSize],
    [easting, northing + cellSize],
  ];
  const ring: number[][] = [];
  for (const [cornerEasting, cornerNorthing] of corners) {
    const lngLat = projectToLngLat(cornerEasting, cornerNorthing);
    if (!lngLat) {
      throw new Error(`HKGeoCode ${code} corner did not project to WGS84`);
    }
    ring.push(lngLat);
  }
  ring.push(ring[0]);
  const center = projectToLngLat(easting + cellSize / 2, northing + cellSize / 2);
  if (!center) {
    throw new Error(`HKGeoCode ${code} center did not project to WGS84`);
  }
  return {
    type: "Feature",
    id: code,
    properties: {
      hkgeocode: code,
      length: code.length,
      easting,
      northing,
      center_lng: center[0],
      center_lat: center[1],
    },
    geometry: {
      type: "Polygon",
      coordinates: [ring],
    },
  };
}

export function gridForHk80Bounds(
  bounds: Hk80Bounds,
  length: CodeLength,
  limit = VIEWPORT_CELL_LIMIT,
): CellFeatureCollection {
  const count = countCodesInBounds(bounds.west, bounds.south, bounds.east, bounds.north, length);
  if (count > limit) {
    throw new CellLimitError(limit);
  }
  const codes = codesInBounds(bounds.west, bounds.south, bounds.east, bounds.north, length);
  return {
    type: "FeatureCollection",
    features: codes.map((code) => cellFeature(code)),
  };
}

export function gridForLngLatBounds(
  west: number,
  south: number,
  east: number,
  north: number,
  length: CodeLength,
  limit = VIEWPORT_CELL_LIMIT,
): CellFeatureCollection {
  if (!(east > west) || !(north > south)) {
    return EMPTY_COLLECTION;
  }
  const bounds = hk80BoundsFromLngLatBounds(west, south, east, north);
  if (!bounds) {
    return EMPTY_COLLECTION;
  }
  return gridForHk80Bounds(bounds, length, limit);
}

export function cellCountLabel(count: number): string {
  return `${count.toLocaleString("en-US")} cells in view`;
}

export function tooManyCellsLabel(limit: number): string {
  return `This view exceeds the ${limit.toLocaleString("en-US")} cell limit. Zoom in or lower the resolution.`;
}

export function layerName(length: CodeLength): string {
  return `HKGeoCode (${levelLabel(length)})`;
}

export function exportBaseName(length: CodeLength): string {
  if (length === 2) {
    return "hkgeocode-2km";
  }
  if (length === 4) {
    return "hkgeocode-100m";
  }
  return "hkgeocode-5m";
}

export function toCsv(collection: CellFeatureCollection): string {
  const lines = ["hkgeocode,length,easting,northing,center_lat,center_lng"];
  for (const feature of collection.features) {
    const properties = feature.properties;
    lines.push(
      [
        properties.hkgeocode,
        properties.length,
        properties.easting,
        properties.northing,
        properties.center_lat,
        properties.center_lng,
      ].join(","),
    );
  }
  return lines.join("\n");
}
