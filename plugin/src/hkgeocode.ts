/**
 * HKGeoCode encode/decode for Hong Kong 1980 Grid (EPSG:2326).
 * Matches hkgeocode.py and js/hkgeocode.js.
 */

export const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

const INVERSE = new Map<string, number>(
  [...CROCKFORD].map((character, index) => [character, index]),
);

export const ORIGIN_EASTING = 800_000;
export const ORIGIN_NORTHING = 800_000;

export const LEVELS = [
  { size: 2_000, xCount: 32, yCount: 24 },
  { size: 100, xCount: 20, yCount: 20 },
  { size: 5, xCount: 20, yCount: 20 },
] as const;

export const COVERAGE_EASTING = ORIGIN_EASTING + LEVELS[0].size * LEVELS[0].xCount;
export const COVERAGE_NORTHING = ORIGIN_NORTHING + LEVELS[0].size * LEVELS[0].yCount;

export type CodeLength = 2 | 4 | 6;

const LENGTHS = new Set<number>([2, 4, 6]);

export class HKGeoCodeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HKGeoCodeError";
  }
}

export function isCodeLength(value: number): value is CodeLength {
  return value === 2 || value === 4 || value === 6;
}

function encodeIndex(index: number): string {
  if (index < 0 || index >= CROCKFORD.length) {
    throw new HKGeoCodeError(`index ${index} is outside Crockford Base32 (0–31)`);
  }
  return CROCKFORD[index];
}

export function cellSizeForLength(length: CodeLength): number {
  return LEVELS[length / 2 - 1].size;
}

export function coverageGridShape(cellSize: number): { nCols: number; nRows: number } {
  return {
    nCols: Math.round((COVERAGE_EASTING - ORIGIN_EASTING) / cellSize),
    nRows: Math.round((COVERAGE_NORTHING - ORIGIN_NORTHING) / cellSize),
  };
}

export function hk80ToHkgeocode(
  easting: number,
  northing: number,
  length: CodeLength = 6,
): string {
  if (!LENGTHS.has(length)) {
    throw new HKGeoCodeError("length must be 2, 4 or 6");
  }
  const deltaE = easting - ORIGIN_EASTING;
  const deltaN = northing - ORIGIN_NORTHING;
  if (deltaE < 0 || deltaE >= COVERAGE_EASTING - ORIGIN_EASTING) {
    throw new HKGeoCodeError("easting is outside HKGeoCode coverage");
  }
  if (deltaN < 0 || deltaN >= COVERAGE_NORTHING - ORIGIN_NORTHING) {
    throw new HKGeoCodeError("northing is outside HKGeoCode coverage");
  }
  const chars: string[] = [];
  let remE = deltaE;
  let remN = deltaN;
  const levelsNeeded = length / 2;
  for (let i = 0; i < levelsNeeded; i += 1) {
    const { size, xCount, yCount } = LEVELS[i];
    const xIndex = Math.floor(remE / size);
    const yIndex = Math.floor(remN / size);
    if (xIndex < 0 || yIndex < 0 || xIndex >= xCount || yIndex >= yCount) {
      throw new HKGeoCodeError("coordinates are outside HKGeoCode coverage");
    }
    chars.push(encodeIndex(xIndex), encodeIndex(yIndex));
    remE -= xIndex * size;
    remN -= yIndex * size;
  }
  return chars.join("");
}

export function normalizeCode(code: string): string {
  const normalized = String(code || "").trim().toUpperCase();
  if (!LENGTHS.has(normalized.length)) {
    throw new HKGeoCodeError("HKGeoCode must be 2, 4 or 6 characters");
  }
  for (const character of normalized) {
    if (!INVERSE.has(character)) {
      throw new HKGeoCodeError(
        `HKGeoCode ${normalized} contains characters outside Crockford Base32`,
      );
    }
  }
  return normalized;
}

export interface CellOrigin {
  easting: number;
  northing: number;
  cellSize: number;
}

export function hkgeocodeCellOrigin(code: string): CellOrigin {
  const normalized = normalizeCode(code);
  let easting = ORIGIN_EASTING;
  let northing = ORIGIN_NORTHING;
  let cellSize: number = LEVELS[0].size;
  for (let level = 0; level < LEVELS.length; level += 1) {
    const offset = level * 2;
    if (offset >= normalized.length) {
      break;
    }
    const { size, xCount, yCount } = LEVELS[level];
    const xIndex = INVERSE.get(normalized[offset]) ?? -1;
    const yIndex = INVERSE.get(normalized[offset + 1]) ?? -1;
    if (xIndex >= xCount || yIndex >= yCount) {
      throw new HKGeoCodeError(
        `HKGeoCode ${normalized} has an out-of-range index at level ${level + 1}`,
      );
    }
    easting += xIndex * size;
    northing += yIndex * size;
    cellSize = size;
  }
  return { easting, northing, cellSize };
}

export function parentCode(code: string): string | null {
  const normalized = normalizeCode(code);
  if (normalized.length === 2) {
    return null;
  }
  return normalized.slice(0, normalized.length - 2);
}

export function neighborCodes(code: string): string[] {
  const normalized = normalizeCode(code);
  const { easting, northing, cellSize } = hkgeocodeCellOrigin(normalized);
  const half = cellSize / 2;
  const neighbors: string[] = [];
  for (const de of [-1, 0, 1]) {
    for (const dn of [-1, 0, 1]) {
      if (de === 0 && dn === 0) {
        continue;
      }
      try {
        neighbors.push(
          hk80ToHkgeocode(
            easting + half + de * cellSize,
            northing + half + dn * cellSize,
            normalized.length as CodeLength,
          ),
        );
      } catch (error) {
        if (!(error instanceof HKGeoCodeError)) {
          throw error;
        }
      }
    }
  }
  return neighbors;
}

/** Each finer level splits a cell into a 20 by 20 block. */
export function childCount(code: string): number {
  const normalized = normalizeCode(code);
  return normalized.length === 6 ? 0 : 400;
}

export function childCodes(code: string): string[] {
  const normalized = normalizeCode(code);
  if (normalized.length === 6) {
    return [];
  }
  const nextLength = (normalized.length + 2) as CodeLength;
  const { easting, northing, cellSize: parentSize } = hkgeocodeCellOrigin(normalized);
  const childSize = cellSizeForLength(nextLength);
  const steps = Math.round(parentSize / childSize);
  const children: string[] = [];
  for (let i = 0; i < steps; i += 1) {
    for (let j = 0; j < steps; j += 1) {
      children.push(
        hk80ToHkgeocode(
          easting + (i + 0.5) * childSize,
          northing + (j + 0.5) * childSize,
          nextLength,
        ),
      );
    }
  }
  return children;
}

export interface CellWindow {
  col0: number;
  col1: number;
  row0: number;
  row1: number;
}

export function cellWindow(
  west: number,
  south: number,
  east: number,
  north: number,
  length: CodeLength,
): CellWindow | null {
  const size = cellSizeForLength(length);
  const { nCols, nRows } = coverageGridShape(size);
  const col0 = Math.max(0, Math.floor((west - ORIGIN_EASTING) / size));
  const row0 = Math.max(0, Math.floor((south - ORIGIN_NORTHING) / size));
  const col1 = Math.min(nCols - 1, Math.floor((east - ORIGIN_EASTING - 1e-9) / size));
  const row1 = Math.min(nRows - 1, Math.floor((north - ORIGIN_NORTHING - 1e-9) / size));
  if (col1 < col0 || row1 < row0) {
    return null;
  }
  return { col0, col1, row0, row1 };
}

export function countCodesInBounds(
  west: number,
  south: number,
  east: number,
  north: number,
  length: CodeLength,
): number {
  const window = cellWindow(west, south, east, north, length);
  if (!window) {
    return 0;
  }
  return (window.col1 - window.col0 + 1) * (window.row1 - window.row0 + 1);
}

export function codesInBounds(
  west: number,
  south: number,
  east: number,
  north: number,
  length: CodeLength,
): string[] {
  const window = cellWindow(west, south, east, north, length);
  if (!window) {
    return [];
  }
  const size = cellSizeForLength(length);
  const codes: string[] = [];
  for (let row = window.row0; row <= window.row1; row += 1) {
    for (let col = window.col0; col <= window.col1; col += 1) {
      const easting = ORIGIN_EASTING + (col + 0.5) * size;
      const northing = ORIGIN_NORTHING + (row + 0.5) * size;
      try {
        codes.push(hk80ToHkgeocode(easting, northing, length));
      } catch (error) {
        if (!(error instanceof HKGeoCodeError)) {
          throw error;
        }
      }
    }
  }
  return codes;
}

export function levelLabel(length: CodeLength): string {
  if (length === 2) {
    return "District (2 km)";
  }
  if (length === 4) {
    return "Neighbourhood (100 m)";
  }
  return "Standard (5 m)";
}

/** Same zoom steps as the map explorer. */
export function lengthForZoom(zoom: number): CodeLength {
  if (zoom >= 17) {
    return 6;
  }
  if (zoom >= 13) {
    return 4;
  }
  return 2;
}
