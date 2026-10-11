import { describe, expect, it } from "vitest";
import {
  COVERAGE_EASTING,
  COVERAGE_NORTHING,
  HKGeoCodeError,
  ORIGIN_EASTING,
  ORIGIN_NORTHING,
  childCodes,
  childCount,
  codesInBounds,
  countCodesInBounds,
  hk80ToHkgeocode,
  hkgeocodeCellOrigin,
  lengthForZoom,
  neighborCodes,
  parentCode,
} from "../src/hkgeocode";

const CASES: Array<[number, number, 2 | 4 | 6, string]> = [
  [800_000, 800_000, 2, "00"],
  [862_000, 846_000, 2, "ZQ"],
  [856_793.009, 832_366.405, 6, "WG73JD"],
  [835_510, 817_185, 6, "H8FB2H"],
  [835_700, 817_200, 4, "H8HC"],
  [839_505, 817_015, 6, "K8FA13"],
  [828_420, 821_985, 6, "EA4K4H"],
  [802_000, 812_000, 2, "16"],
  [838_000, 820_000, 2, "KA"],
  [862_000, 844_000, 2, "ZP"],
  [844_000, 802_000, 2, "P1"],
];

describe("hk80ToHkgeocode", () => {
  it.each(CASES)("encodes E=%s N=%s length %s as %s", (easting, northing, length, expected) => {
    expect(hk80ToHkgeocode(easting, northing, length)).toBe(expected);
  });

  it("rejects a point outside coverage", () => {
    expect(() => hk80ToHkgeocode(ORIGIN_EASTING - 1, ORIGIN_NORTHING, 2)).toThrow(HKGeoCodeError);
  });
});

describe("cell relationships", () => {
  it("reads the Sharp Peak cell origin and parent chain", () => {
    const origin = hkgeocodeCellOrigin("WG73JD");
    expect(hk80ToHkgeocode(origin.easting + 1, origin.northing + 1, 6)).toBe("WG73JD");
    expect(parentCode("WG73JD")).toBe("WG73");
    expect(parentCode("WG73")).toBe("WG");
    expect(parentCode("WG")).toBeNull();
  });

  it("lists eight neighbors inside the grid and three at the south-west corner", () => {
    expect(neighborCodes("WG73")).toHaveLength(8);
    expect(neighborCodes("00").sort()).toEqual(["01", "10", "11"]);
  });

  it("counts 400 children and does not list them for a 5 m cell", () => {
    expect(childCount("WG")).toBe(400);
    expect(childCodes("H8")).toHaveLength(400);
    expect(childCount("WG73JD")).toBe(0);
    expect(childCodes("WG73JD")).toEqual([]);
  });

  it("rejects an out-of-range large-cell index", () => {
    expect(() => hkgeocodeCellOrigin("ZZ")).toThrow(HKGeoCodeError);
  });
});

describe("codesInBounds", () => {
  it("returns the south-west 2 km cell for a window inside that cell", () => {
    expect(codesInBounds(800_000, 800_000, 801_000, 801_000, 2)).toEqual(["00"]);
  });

  it("counts the full 2 km grid as 768 cells", () => {
    expect(
      countCodesInBounds(ORIGIN_EASTING, ORIGIN_NORTHING, COVERAGE_EASTING, COVERAGE_NORTHING, 2),
    ).toBe(768);
  });
});

describe("lengthForZoom", () => {
  it("follows the explorer thresholds", () => {
    expect(lengthForZoom(12)).toBe(2);
    expect(lengthForZoom(13)).toBe(4);
    expect(lengthForZoom(16.9)).toBe(4);
    expect(lengthForZoom(17)).toBe(6);
  });
});
