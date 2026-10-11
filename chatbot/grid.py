"""HKGeoCode cell assignment and per-cell counts for point layers."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from hkgeocode import (
    CELL_SIZE_M,
    COVERAGE_EASTING,
    COVERAGE_NORTHING,
    CROCKFORD,
    ORIGIN_EASTING,
    ORIGIN_NORTHING,
    hkgeocode_cell_origin,
)

LEVEL_LABEL = {2: "2 km district cell", 4: "100 m neighbourhood cell", 6: "5 m standard cell"}
CODE_COLUMNS = {2: "code2", 4: "code4", 6: "code6"}


def encode_vectorised(easting: np.ndarray, northing: np.ndarray, length: int) -> np.ndarray:
    """Vectorised HKGeoCode encoding; returns an object array of codes.

    Points outside coverage get an empty string.
    """
    if length not in CELL_SIZE_M:
        raise ValueError("length must be 2, 4 or 6")
    de = np.asarray(easting, dtype=float) - ORIGIN_EASTING
    dn = np.asarray(northing, dtype=float) - ORIGIN_NORTHING
    inside = (
        (de >= 0)
        & (de < COVERAGE_EASTING - ORIGIN_EASTING)
        & (dn >= 0)
        & (dn < COVERAGE_NORTHING - ORIGIN_NORTHING)
    )
    chars = np.array(list(CROCKFORD))
    parts: list[np.ndarray] = []
    rem_e = np.where(inside, de, 0.0)
    rem_n = np.where(inside, dn, 0.0)
    for size in (2000.0, 100.0, 5.0)[: length // 2]:
        xi = np.floor(rem_e / size).astype(int)
        yi = np.floor(rem_n / size).astype(int)
        parts.append(chars[np.clip(xi, 0, 31)])
        parts.append(chars[np.clip(yi, 0, 31)])
        rem_e = rem_e - xi * size
        rem_n = rem_n - yi * size
    codes = parts[0]
    for p in parts[1:]:
        codes = np.char.add(codes, p)
    codes = np.where(inside, codes, "")
    return codes.astype(object)


def assign_codes(df: pd.DataFrame, lengths: tuple[int, ...] = (2, 4, 6)) -> tuple[pd.DataFrame, int]:
    """Return (df with code columns, number of out-of-coverage rows dropped)."""
    out = df.copy()
    e = out["easting"].to_numpy(dtype=float)
    n = out["northing"].to_numpy(dtype=float)
    for length in lengths:
        out[CODE_COLUMNS[length]] = encode_vectorised(e, n, length)
    inside = out[CODE_COLUMNS[lengths[0]]].astype(str) != ""
    dropped = int((~inside).sum())
    return out.loc[inside].reset_index(drop=True), dropped


def cell_counts(df: pd.DataFrame, length: int) -> pd.DataFrame:
    """Count points per HKGeoCode cell. Columns: code, count, col, row, easting, northing."""
    col = CODE_COLUMNS[length]
    if col not in df.columns:
        df, _ = assign_codes(df, (length,))
    counts = df.groupby(col).size().rename("count").reset_index().rename(columns={col: "code"})
    size = CELL_SIZE_M[length]
    origins = np.array([hkgeocode_cell_origin(c)[:2] for c in counts["code"]])
    if len(origins) == 0:
        origins = np.zeros((0, 2))
    counts["easting"] = origins[:, 0] + size / 2  # cell centre
    counts["northing"] = origins[:, 1] + size / 2
    counts["col"] = np.floor((origins[:, 0] - ORIGIN_EASTING) / size).astype(int)
    counts["row"] = np.floor((origins[:, 1] - ORIGIN_NORTHING) / size).astype(int)
    return counts.sort_values("count", ascending=False).reset_index(drop=True)


@dataclass
class QuantifySummary:
    layer: str
    length: int
    points: int
    dropped: int
    occupied_cells: int
    max_count: int
    mean_count: float
    median_count: float
    distribution: pd.DataFrame  # columns: points_in_cell, cells
    top_cells: pd.DataFrame  # columns: code, count, easting, northing

    @property
    def cell_label(self) -> str:
        return LEVEL_LABEL[self.length]

    def distribution_text(self, max_rows: int = 12) -> str:
        dist = self.distribution
        if len(dist) <= max_rows:
            return ", ".join(f"{int(r.points_in_cell)} -> {int(r.cells):,}" for r in dist.itertuples())
        # Too many distinct values: bin into ranges.
        edges = [1, 2, 3, 4, 5, 6, 10, 20, 50, 100, int(dist.points_in_cell.max()) + 1]
        edges = sorted({e for e in edges if e <= int(dist.points_in_cell.max()) + 1})
        parts = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            n = int(dist.loc[(dist.points_in_cell >= lo) & (dist.points_in_cell < hi), "cells"].sum())
            if n:
                label = f"{lo}" if hi == lo + 1 else f"{lo}-{hi - 1}"
                parts.append(f"{label} -> {n:,}")
        return ", ".join(parts)

    def to_text(self) -> str:
        lines = [
            f"{self.layer}: {self.points:,} points binned to {self.occupied_cells:,} "
            f"{self.cell_label}s ({self.dropped} outside HKGeoCode coverage).",
            f"Per occupied cell: max {self.max_count}, mean {self.mean_count:.2f}, "
            f"median {self.median_count:.0f}.",
            "Distribution (points in cell -> number of cells): " + self.distribution_text(),
            "Top cells: "
            + ", ".join(f"{r.code} ({int(r.count)})" for r in self.top_cells.head(5).itertuples()),
        ]
        return "\n".join(lines)


def quantify(df: pd.DataFrame, layer_name: str, length: int = 4, dropped: int = 0, top_n: int = 10) -> QuantifySummary:
    counts = cell_counts(df, length)
    dist = (
        counts.groupby("count").size().rename("cells").reset_index().rename(columns={"count": "points_in_cell"})
    )
    return QuantifySummary(
        layer=layer_name,
        length=length,
        points=int(len(df)),
        dropped=dropped,
        occupied_cells=int(len(counts)),
        max_count=int(counts["count"].max()) if len(counts) else 0,
        mean_count=float(counts["count"].mean()) if len(counts) else 0.0,
        median_count=float(counts["count"].median()) if len(counts) else 0.0,
        distribution=dist,
        top_cells=counts.head(top_n)[["code", "count", "easting", "northing"]].reset_index(drop=True),
    )


def cell_polygon_hk80(code: str) -> list[tuple[float, float]]:
    """Return the four HK80 corners of a cell (SW, SE, NE, NW)."""
    e, n, size = hkgeocode_cell_origin(code)
    return [(e, n), (e + size, n), (e + size, n + size), (e, n + size)]


def parent_code(code: str) -> str:
    return code[:-2] if len(code) > 2 else code


def grid_shape(length: int) -> tuple[int, int]:
    size = CELL_SIZE_M[length]
    return (
        int(math.ceil((COVERAGE_EASTING - ORIGIN_EASTING) / size)),
        int(math.ceil((COVERAGE_NORTHING - ORIGIN_NORTHING) / size)),
    )
