import { describe, expect, it } from "vitest";
import {
  CellLimitError,
  VIEWPORT_CELL_LIMIT,
  cellFeature,
  gridForHk80Bounds,
  gridForLngLatBounds,
  projectToHk80,
  toCsv,
} from "../src/grid";
import { hk80ToHkgeocode, hkgeocodeCellOrigin } from "../src/hkgeocode";

describe("cell polygons", () => {
  it("projects the Sharp Peak cell and round-trips its south-west corner", () => {
    const feature = cellFeature("WG73JD");
    expect(feature.properties.hkgeocode).toBe("WG73JD");
    expect(feature.properties.length).toBe(6);
    const ring = feature.geometry.coordinates[0];
    expect(ring[0]).toEqual(ring[ring.length - 1]);
    const origin = hkgeocodeCellOrigin("WG73JD");
    const projected = projectToHk80(ring[0][0], ring[0][1]);
    expect(projected).not.toBeNull();
    expect(Math.abs((projected?.[0] ?? 0) - origin.easting)).toBeLessThan(0.05);
    expect(Math.abs((projected?.[1] ?? 0) - origin.northing)).toBeLessThan(0.05);
    expect(hk80ToHkgeocode(856_793.009, 832_366.405, 6)).toBe("WG73JD");
  });

  it("includes WG73JD in a small WGS84 window around Sharp Peak", () => {
    const origin = hkgeocodeCellOrigin("WG73JD");
    const center = cellFeature("WG73JD").properties;
    const collection = gridForLngLatBounds(
      center.center_lng - 0.0002,
      center.center_lat - 0.0002,
      center.center_lng + 0.0002,
      center.center_lat + 0.0002,
      6,
    );
    const codes = collection.features.map((feature) => feature.properties.hkgeocode);
    expect(codes).toContain("WG73JD");
    expect(origin.cellSize).toBe(5);
  });
});

describe("viewport cell limit", () => {
  it("refuses a 100 m window above 12,000 cells", () => {
    const bounds = {
      west: 800_000,
      south: 800_000,
      east: 811_000,
      north: 811_000,
    };
    expect(() => gridForHk80Bounds(bounds, 4)).toThrow(CellLimitError);
    expect(() => gridForHk80Bounds(bounds, 4)).toThrow(
      `HKGeoCode cell limit exceeded: ${VIEWPORT_CELL_LIMIT}`,
    );
  });

  it("draws one 2 km cell and writes a CSV row", () => {
    const collection = gridForHk80Bounds(
      { west: 800_000, south: 800_000, east: 801_000, north: 801_000 },
      2,
    );
    expect(collection.features).toHaveLength(1);
    expect(collection.features[0].properties.hkgeocode).toBe("00");
    expect(toCsv(collection).split("\n")[1]?.startsWith("00,2,")).toBe(true);
  });
});
