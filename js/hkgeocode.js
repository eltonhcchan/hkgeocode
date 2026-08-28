/**
 * HKGeoCode encode/decode for Hong Kong 1980 Grid (EPSG:2326).
 * Matches hkgeocode.py: Crockford Base32 pairs at 2 km, 100 m, and 5 m.
 */
(function (root) {
  "use strict";

  const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  const INVERSE = Object.fromEntries([...CROCKFORD].map((ch, i) => [ch, i]));
  const ORIGIN_EASTING = 800000;
  const ORIGIN_NORTHING = 800000;
  const LEVELS = [
    { size: 2000, xCount: 32, yCount: 24 },
    { size: 100, xCount: 20, yCount: 20 },
    { size: 5, xCount: 20, yCount: 20 },
  ];
  const LENGTHS = new Set([2, 4, 6]);
  const COVERAGE_EASTING = ORIGIN_EASTING + LEVELS[0].size * LEVELS[0].xCount;
  const COVERAGE_NORTHING = ORIGIN_NORTHING + LEVELS[0].size * LEVELS[0].yCount;

  class HKGeoCodeError extends Error {
    constructor(message) {
      super(message);
      this.name = "HKGeoCodeError";
    }
  }

  function encodeIndex(index) {
    if (index < 0 || index >= CROCKFORD.length) {
      throw new HKGeoCodeError(`index ${index} is outside Crockford Base32 (0–31)`);
    }
    return CROCKFORD[index];
  }

  function cellSizeForLength(length) {
    if (!LENGTHS.has(length)) {
      throw new HKGeoCodeError("length must be 2, 4 or 6");
    }
    return LEVELS[length / 2 - 1].size;
  }

  function coverageGridShape(cellSize) {
    return {
      nCols: Math.round((COVERAGE_EASTING - ORIGIN_EASTING) / cellSize),
      nRows: Math.round((COVERAGE_NORTHING - ORIGIN_NORTHING) / cellSize),
    };
  }

  function hk80ToHkgeocode(easting, northing, length = 6) {
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
    const chars = [];
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

  function normalizeCode(code) {
    const normalized = String(code || "").trim().toUpperCase();
    if (!LENGTHS.has(normalized.length)) {
      throw new HKGeoCodeError("HKGeoCode must be 2, 4 or 6 characters");
    }
    for (const ch of normalized) {
      if (!(ch in INVERSE)) {
        throw new HKGeoCodeError(
          `HKGeoCode ${normalized} contains characters outside Crockford Base32`
        );
      }
    }
    return normalized;
  }

  function hkgeocodeCellOrigin(code) {
    const normalized = normalizeCode(code);
    let easting = ORIGIN_EASTING;
    let northing = ORIGIN_NORTHING;
    let cellSize = LEVELS[0].size;
    for (let level = 0; level < LEVELS.length; level += 1) {
      const offset = level * 2;
      if (offset >= normalized.length) {
        break;
      }
      const { size, xCount, yCount } = LEVELS[level];
      const xIndex = INVERSE[normalized[offset]];
      const yIndex = INVERSE[normalized[offset + 1]];
      if (xIndex >= xCount || yIndex >= yCount) {
        throw new HKGeoCodeError(
          `HKGeoCode ${normalized} has an out-of-range index at level ${level + 1}`
        );
      }
      easting += xIndex * size;
      northing += yIndex * size;
      cellSize = size;
    }
    return { easting, northing, cellSize };
  }

  function parentCode(code) {
    const normalized = normalizeCode(code);
    if (normalized.length === 2) {
      return null;
    }
    return normalized.slice(0, normalized.length - 2);
  }

  function neighbourCodes(code) {
    const normalized = normalizeCode(code);
    const { easting, northing, cellSize } = hkgeocodeCellOrigin(normalized);
    const half = cellSize / 2;
    const neighbours = [];
    for (const de of [-1, 0, 1]) {
      for (const dn of [-1, 0, 1]) {
        if (de === 0 && dn === 0) {
          continue;
        }
        try {
          neighbours.push(
            hk80ToHkgeocode(
              easting + half + de * cellSize,
              northing + half + dn * cellSize,
              normalized.length
            )
          );
        } catch (err) {
          if (!(err instanceof HKGeoCodeError)) {
            throw err;
          }
        }
      }
    }
    return neighbours;
  }

  function childCodes(code) {
    const normalized = normalizeCode(code);
    if (normalized.length === 6) {
      return [];
    }
    const nextLength = normalized.length + 2;
    const { easting, northing, cellSize: parentSize } = hkgeocodeCellOrigin(normalized);
    const childSize = cellSizeForLength(nextLength);
    const n = Math.round(parentSize / childSize);
    const children = [];
    for (let i = 0; i < n; i += 1) {
      for (let j = 0; j < n; j += 1) {
        children.push(
          hk80ToHkgeocode(
            easting + (i + 0.5) * childSize,
            northing + (j + 0.5) * childSize,
            nextLength
          )
        );
      }
    }
    return children;
  }

  function cellIntersects(code, west, south, east, north) {
    const { easting, northing, cellSize } = hkgeocodeCellOrigin(code);
    return (
      easting < east &&
      easting + cellSize > west &&
      northing < north &&
      northing + cellSize > south
    );
  }

  function codesInBounds(west, south, east, north, length) {
    const size = cellSizeForLength(length);
    const { nCols, nRows } = coverageGridShape(size);
    const col0 = Math.max(0, Math.floor((west - ORIGIN_EASTING) / size));
    const row0 = Math.max(0, Math.floor((south - ORIGIN_NORTHING) / size));
    const col1 = Math.min(nCols - 1, Math.floor((east - ORIGIN_EASTING - 1e-9) / size));
    const row1 = Math.min(nRows - 1, Math.floor((north - ORIGIN_NORTHING - 1e-9) / size));
    if (col1 < col0 || row1 < row0) {
      return [];
    }
    const codes = [];
    for (let row = row0; row <= row1; row += 1) {
      for (let col = col0; col <= col1; col += 1) {
        const e = ORIGIN_EASTING + (col + 0.5) * size;
        const n = ORIGIN_NORTHING + (row + 0.5) * size;
        try {
          codes.push(hk80ToHkgeocode(e, n, length));
        } catch (err) {
          if (!(err instanceof HKGeoCodeError)) {
            throw err;
          }
        }
      }
    }
    return codes;
  }

  function levelLabel(length) {
    if (length === 2) {
      return "District (2 km)";
    }
    if (length === 4) {
      return "Neighbourhood (100 m)";
    }
    return "Standard (5 m)";
  }

  const api = {
    CROCKFORD,
    ORIGIN_EASTING,
    ORIGIN_NORTHING,
    COVERAGE_EASTING,
    COVERAGE_NORTHING,
    LEVELS,
    HKGeoCodeError,
    cellSizeForLength,
    coverageGridShape,
    hk80ToHkgeocode,
    hkgeocodeCellOrigin,
    parentCode,
    childCodes,
    neighbourCodes,
    cellIntersects,
    codesInBounds,
    levelLabel,
    normalizeCode,
  };

  root.HKGeoCode = api;
})(typeof window !== "undefined" ? window : globalThis);
