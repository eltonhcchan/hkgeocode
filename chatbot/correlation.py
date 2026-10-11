"""Spatial correlation between two point layers on the HKGeoCode grid.

Everything is numpy/scipy only (no PySAL). Measures:

* Per-cell count correlation (Pearson, Spearman), both on the union of
  occupied cells and on the full study area (every cell inside the 2 km cells
  occupied by either layer, zeros included)
* Co-location overlap (Jaccard, conditional shares)
* Bivariate Moran's I (Wartenberg) with queen contiguity on the grid and a
  permutation pseudo p-value, plus local bivariate Moran cluster classes
  (the permutation shuffles the lag variable across all cells, an
  approximation of the conditional scheme used by PySAL)
* Nearest-neighbour distances between the layers against a random baseline
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse, stats
from scipy.spatial import cKDTree

from hkgeocode import CELL_SIZE_M, hkgeocode_cell_origin

from .grid import LEVEL_LABEL, cell_counts, encode_vectorised, parent_code

NN_BANDS_M = (100, 200, 500)


# --------------------------------------------------------------------------- #
# Spatial weights
# --------------------------------------------------------------------------- #
def queen_weights(cols: np.ndarray, rows: np.ndarray, row_standardise: bool = True) -> sparse.csr_matrix:
    """Queen contiguity between grid cells identified by (col, row) indices."""
    n = len(cols)
    index = {(int(c), int(r)): i for i, (c, r) in enumerate(zip(cols, rows))}
    src: list[int] = []
    dst: list[int] = []
    for i, (c, r) in enumerate(zip(cols, rows)):
        for dc in (-1, 0, 1):
            for dr in (-1, 0, 1):
                if dc == 0 and dr == 0:
                    continue
                j = index.get((int(c) + dc, int(r) + dr))
                if j is not None:
                    src.append(i)
                    dst.append(j)
    data = np.ones(len(src), dtype=float)
    w = sparse.csr_matrix((data, (src, dst)), shape=(n, n))
    if row_standardise:
        row_sum = np.asarray(w.sum(axis=1)).ravel()
        inv = np.divide(1.0, row_sum, out=np.zeros_like(row_sum), where=row_sum > 0)
        w = sparse.diags(inv) @ w
    return w.tocsr()


def _zscore(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    sd = x.std()
    if sd == 0:
        return np.zeros_like(x)
    return (x - x.mean()) / sd


def bivariate_moran(
    x: np.ndarray,
    y: np.ndarray,
    w: sparse.csr_matrix,
    permutations: int = 999,
    seed: int = 42,
) -> dict:
    """Global bivariate Moran's I of x against the spatial lag of y.

    With z-scores and a row-standardised W: I = (zx . W zy) / n.
    Also returns local I_i = zx_i * (W zy)_i with a permutation pseudo p-value
    obtained by shuffling y (the lag variable) across all cells.
    """
    zx = _zscore(x)
    zy = _zscore(y)
    n = len(zx)
    lag = w @ zy
    local = zx * lag
    I_obs = float(local.sum() / n)

    rng = np.random.default_rng(seed)
    perm_I = np.empty(permutations)
    extreme = np.zeros(n, dtype=int)
    positive = local >= 0
    for k in range(permutations):
        lag_p = w @ zy[rng.permutation(n)]
        local_p = zx * lag_p
        perm_I[k] = local_p.sum() / n
        extreme += np.where(positive, local_p >= local, local_p <= local)

    if I_obs >= 0:
        p_global = (np.count_nonzero(perm_I >= I_obs) + 1) / (permutations + 1)
    else:
        p_global = (np.count_nonzero(perm_I <= I_obs) + 1) / (permutations + 1)
    p_local = (extreme + 1) / (permutations + 1)
    expected = -1.0 / (n - 1) if n > 1 else 0.0

    return {
        "I": I_obs,
        "expected_I": expected,
        "p_value": float(p_global),
        "z_score": float((I_obs - perm_I.mean()) / perm_I.std()) if perm_I.std() > 0 else 0.0,
        "permutations": permutations,
        "zx": zx,
        "lag_y": lag,
        "local_I": local,
        "local_p": p_local,
    }


def local_cluster_labels(zx: np.ndarray, lag_y: np.ndarray, p: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    labels = np.full(len(zx), "ns", dtype=object)
    sig = p <= alpha
    labels[sig & (zx > 0) & (lag_y > 0)] = "HH"
    labels[sig & (zx < 0) & (lag_y < 0)] = "LL"
    labels[sig & (zx > 0) & (lag_y < 0)] = "HL"
    labels[sig & (zx < 0) & (lag_y > 0)] = "LH"
    return labels


# --------------------------------------------------------------------------- #
# Nearest neighbours
# --------------------------------------------------------------------------- #
def _random_points_in_cells(codes: list[str], n: int, rng: np.random.Generator) -> np.ndarray:
    """Uniform random points inside the union of the given HKGeoCode cells."""
    origins = np.array([hkgeocode_cell_origin(c) for c in codes])  # e, n, size
    pick = rng.integers(0, len(origins), n)
    size = origins[pick, 2]
    e = origins[pick, 0] + rng.random(n) * size
    nn = origins[pick, 1] + rng.random(n) * size
    return np.column_stack([e, nn])


def nearest_neighbour_stats(
    src: np.ndarray,
    dst: np.ndarray,
    study_cells: list[str],
    seed: int = 42,
) -> dict:
    """Distances from each src point to its nearest dst point, vs. a random baseline."""
    tree = cKDTree(dst)
    d, _ = tree.query(src, k=1)
    rng = np.random.default_rng(seed)
    rand = _random_points_in_cells(study_cells, len(src), rng)
    d_rand, _ = tree.query(rand, k=1)
    out = {
        "n": int(len(src)),
        "median_m": float(np.median(d)),
        "mean_m": float(d.mean()),
        "random_median_m": float(np.median(d_rand)),
        "ratio_median": float(np.median(d) / np.median(d_rand)) if np.median(d_rand) > 0 else float("nan"),
        "distances": d,
    }
    for band in NN_BANDS_M:
        out[f"pct_within_{band}m"] = float((d <= band).mean() * 100)
        out[f"random_pct_within_{band}m"] = float((d_rand <= band).mean() * 100)
    return out


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class CorrelationResult:
    name_a: str
    name_b: str
    length: int
    cells: pd.DataFrame  # code, col, row, a, b, cls, zx, lag_y, local_I, local_p, cluster
    pearson_r: float
    pearson_p: float
    spearman_rho: float
    spearman_p: float
    cells_a: int
    cells_b: int
    cells_both: int
    cells_union: int
    jaccard: float
    share_a_with_b: float
    share_b_with_a: float
    moran: dict
    nn_a_to_b: dict
    nn_b_to_a: dict
    by_parent: pd.DataFrame  # 2 km summary
    islands: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def cell_label(self) -> str:
        return LEVEL_LABEL[self.length]

    def cluster_counts(self) -> dict[str, int]:
        return self.cells["cluster"].value_counts().to_dict()

    @property
    def study_area(self) -> dict:
        return self.extra.get("study_area", {"available": False})

    @property
    def headline_r(self) -> float:
        """Pearson r used for the verdict: zero-inclusive study area when available."""
        sa = self.study_area
        return sa["pearson_r"] if sa.get("available") else self.pearson_r

    @staticmethod
    def strength_of(r: float) -> str:
        r = abs(r)
        if r < 0.1:
            return "negligible"
        if r < 0.3:
            return "weak"
        if r < 0.5:
            return "moderate"
        return "strong"

    def strength_word(self) -> str:
        return self.strength_of(self.headline_r)

    def to_text(self) -> str:
        m = self.moran
        cc = self.cluster_counts()
        nn_ab = self.nn_a_to_b
        nn_ba = self.nn_b_to_a
        lines = [
            f"Spatial correlation of {self.name_a} (A) and {self.name_b} (B) on {self.cell_label}s:",
            f"- Occupied cells: A {self.cells_a:,}, B {self.cells_b:,}, both {self.cells_both:,} "
            f"(Jaccard {self.jaccard:.3f}; {self.share_a_with_b*100:.1f}% of A-cells also hold B, "
            f"{self.share_b_with_a*100:.1f}% of B-cells also hold A).",
            f"- Per-cell counts over the union of {self.cells_union:,} occupied cells: Pearson r = {self.pearson_r:.3f} "
            f"(p = {self.pearson_p:.3g}), Spearman rho = {self.spearman_rho:.3f} (p = {self.spearman_p:.3g}).",
        ]
        sa = self.study_area
        if sa.get("available"):
            lines.append(
                f"- Per-cell counts over all {sa['n_cells']:,} cells of the study area (occupied 2 km cells, zeros "
                f"included): Pearson r = {sa['pearson_r']:.3f} (p = {sa['pearson_p']:.3g}), "
                f"Spearman rho = {sa['spearman_rho']:.3f} (p = {sa['spearman_p']:.3g})."
            )
        lines += [
            f"- Bivariate Moran's I (A vs spatial lag of B, queen contiguity, {len(self.cells):,} cells of the "
            f"{self.extra.get('universe', 'occupied').replace('_', ' ')} universe) = {m['I']:.3f} "
            f"(expected {m['expected_I']:.4f}, pseudo p = {m['p_value']:.3f}, z = {m['z_score']:.1f}, "
            f"{m['permutations']} permutations; {self.islands:,} cells without neighbours).",
            f"- Local clusters (p <= 0.05): HH {cc.get('HH', 0):,}, LL {cc.get('LL', 0):,}, "
            f"HL {cc.get('HL', 0):,}, LH {cc.get('LH', 0):,}, not significant {cc.get('ns', 0):,}.",
            f"- Nearest B from each A: median {nn_ab['median_m']:.0f} m (random baseline "
            f"{nn_ab['random_median_m']:.0f} m, ratio {nn_ab['ratio_median']:.2f}); "
            + ", ".join(f"{nn_ab[f'pct_within_{b}m']:.1f}% within {b} m" for b in NN_BANDS_M)
            + ".",
            f"- Nearest A from each B: median {nn_ba['median_m']:.0f} m (random baseline "
            f"{nn_ba['random_median_m']:.0f} m, ratio {nn_ba['ratio_median']:.2f}); "
            + ", ".join(f"{nn_ba[f'pct_within_{b}m']:.1f}% within {b} m" for b in NN_BANDS_M)
            + ".",
        ]
        return "\n".join(lines)

    def interpret(self) -> str:
        m = self.moran
        parts = []
        r = self.headline_r
        sign = "positive" if r > 0 else "negative"
        sa = self.study_area
        if sa.get("available") and self.length > 2:
            parts.append(
                f"Across the study area ({sa['n_cells']:,} {self.cell_label}s, empty cells included) the association "
                f"between {self.name_a} and {self.name_b} counts is {self.strength_word()} and {sign} (r = {r:.2f}). "
                f"Restricted to occupied cells only, r = {self.pearson_r:.2f}; that figure is pushed down because most "
                "occupied cells hold just one layer, so the overlap and distance measures below are more informative at this scale."
            )
        else:
            parts.append(
                f"Cell-level association between {self.name_a} and {self.name_b} is {self.strength_word()} and {sign} "
                f"(r = {r:.2f}) over {len(self.cells):,} {self.cell_label}s."
                if r == r
                else f"Cell-level correlation between {self.name_a} and {self.name_b} could not be computed."
            )
        if m["p_value"] <= 0.05 and m["I"] > 0:
            parts.append(
                f"Bivariate Moran's I is significantly positive (I = {m['I']:.2f}), so cells with many "
                f"{self.name_a} tend to be surrounded by cells with many {self.name_b}: the two layers cluster together."
            )
        elif m["p_value"] <= 0.05 and m["I"] < 0:
            parts.append(
                f"Bivariate Moran's I is significantly negative (I = {m['I']:.2f}): where {self.name_a} is dense, "
                f"the surrounding {self.name_b} is sparse (spatial repulsion)."
            )
        else:
            parts.append(
                f"Bivariate Moran's I ({m['I']:.2f}, p = {m['p_value']:.2f}) is not significant: no spatial "
                "spill-over between the layers beyond what chance would give."
            )
        nn = self.nn_a_to_b
        if nn["ratio_median"] < 0.8:
            parts.append(
                f"Each {self.name_a} point is typically {nn['median_m']:.0f} m from the nearest {self.name_b}, "
                f"about {1/nn['ratio_median']:.1f}x closer than random placement in the same study area, which indicates attraction."
            )
        elif nn["ratio_median"] > 1.25:
            parts.append(
                f"{self.name_a} points are further from {self.name_b} than random placement would be "
                f"({nn['median_m']:.0f} m vs {nn['random_median_m']:.0f} m), suggesting the layers avoid each other."
            )
        else:
            parts.append(
                f"Nearest-neighbour distances ({nn['median_m']:.0f} m median) are close to the random baseline, "
                "so the layers are neither strongly attracted nor repelled at point level."
            )
        cc = self.cluster_counts()
        if cc.get("HH", 0):
            if self.length > 2:
                top = self.by_parent.head(3)
                where = "co-location concentrates in 2 km district cells " + ", ".join(
                    f"{r.parent} ({int(r.cells_both)} shared {self.cell_label}s)" for r in top.itertuples()
                )
            else:
                top = self.cells[self.cells.cluster == "HH"].assign(m=lambda d: d[["a", "b"]].min(axis=1))
                top = top.sort_values("m", ascending=False).head(3)
                where = "the strongest shared district cells are " + ", ".join(
                    f"{r.code} (A {int(r.a)}, B {int(r.b)})" for r in top.itertuples()
                )
            parts.append(
                f"{cc['HH']:,} HH cells (high {self.name_a} surrounded by high {self.name_b}) mark shared hotspots; "
                + where
                + "."
            )
        gap_a = self.cells_a - self.cells_both
        gap_b = self.cells_b - self.cells_both
        parts.append(
            f"Gaps: {gap_a:,} cells have {self.name_a} but no {self.name_b}, and {gap_b:,} cells have "
            f"{self.name_b} but no {self.name_a}; these are candidate sites if the goal is to pair the two services."
        )
        return " ".join(parts)


# --------------------------------------------------------------------------- #
# Universe of cells
# --------------------------------------------------------------------------- #
def study_area_cells(study_codes: list[str], length: int) -> pd.DataFrame:
    """Every cell of the given length inside the listed 2 km cells."""
    size = CELL_SIZE_M[length]
    per_side = int(round(2000.0 / size))
    origins = np.array([hkgeocode_cell_origin(c)[:2] for c in study_codes], dtype=float)
    c0 = np.round((origins[:, 0] - 800_000.0) / size).astype(int)
    r0 = np.round((origins[:, 1] - 800_000.0) / size).astype(int)
    dc, dr = np.meshgrid(np.arange(per_side), np.arange(per_side))
    cols = (c0[:, None] + dc.ravel()[None, :]).ravel()
    rows = (r0[:, None] + dr.ravel()[None, :]).ravel()
    easting = 800_000.0 + cols * size + size / 2
    northing = 800_000.0 + rows * size + size / 2
    codes = encode_vectorised(easting, northing, length)
    return pd.DataFrame(
        {"code": codes, "col": cols, "row": rows, "easting": easting, "northing": northing}
    )


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def correlate(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    name_a: str,
    name_b: str,
    length: int = 4,
    permutations: int = 999,
    seed: int = 42,
    universe: str = "study_area",
) -> CorrelationResult:
    """Correlate two point layers (DataFrames with easting/northing and code columns).

    ``universe`` is the set of cells on which correlation and Moran's I are
    evaluated: ``"study_area"`` = every cell inside the 2 km cells occupied by
    either layer (zeros included; 2 km / 100 m only), ``"occupied"`` = only
    cells holding at least one point of either layer.
    """
    ca = cell_counts(df_a, length).rename(columns={"count": "a"})
    cb = cell_counts(df_b, length).rename(columns={"count": "b"})
    study = sorted(set(df_a["code2"]).union(df_b["code2"]))

    if universe == "study_area" and length <= 4:
        geo = study_area_cells(study, length)
    else:
        universe = "occupied"
        geo = pd.concat(
            [ca[["code", "col", "row", "easting", "northing"]], cb[["code", "col", "row", "easting", "northing"]]]
        ).drop_duplicates("code")
    cells = geo.merge(ca[["code", "a"]], on="code", how="left").merge(cb[["code", "b"]], on="code", how="left")
    cells[["a", "b"]] = cells[["a", "b"]].fillna(0).astype(int)
    cells["cls"] = np.select(
        [(cells.a > 0) & (cells.b > 0), cells.a > 0, cells.b > 0],
        ["both", "A only", "B only"],
        default="empty",
    )
    cells = cells.sort_values(["row", "col"]).reset_index(drop=True)

    a = cells["a"].to_numpy(float)
    b = cells["b"].to_numpy(float)
    occupied = (cells.a > 0) | (cells.b > 0)
    ao, bo = a[occupied.to_numpy()], b[occupied.to_numpy()]
    pearson = stats.pearsonr(ao, bo) if len(ao) > 2 else (float("nan"), float("nan"))
    spearman = stats.spearmanr(ao, bo) if len(ao) > 2 else (float("nan"), float("nan"))

    study_corr: dict = {"available": False}
    if universe == "study_area" and len(cells) > 2 and a.std() > 0 and b.std() > 0:
        pr = stats.pearsonr(a, b)
        sr = stats.spearmanr(a, b)
        study_corr = {
            "available": True,
            "n_cells": int(len(cells)),
            "pearson_r": float(pr[0]),
            "pearson_p": float(pr[1]),
            "spearman_rho": float(sr[0]),
            "spearman_p": float(sr[1]),
        }

    cells_a = int((cells.a > 0).sum())
    cells_b = int((cells.b > 0).sum())
    both = int(((cells.a > 0) & (cells.b > 0)).sum())
    union = int(occupied.sum())

    w = queen_weights(cells["col"].to_numpy(), cells["row"].to_numpy())
    islands = int((np.asarray(w.sum(axis=1)).ravel() == 0).sum())
    moran = bivariate_moran(a, b, w, permutations=permutations, seed=seed)
    cells["zx"] = moran["zx"]
    cells["lag_y"] = moran["lag_y"]
    cells["local_I"] = moran["local_I"]
    cells["local_p"] = moran["local_p"]
    cells["cluster"] = local_cluster_labels(moran["zx"], moran["lag_y"], moran["local_p"])

    pa = df_a[["easting", "northing"]].to_numpy(float)
    pb = df_b[["easting", "northing"]].to_numpy(float)
    nn_ab = nearest_neighbour_stats(pa, pb, study, seed)
    nn_ba = nearest_neighbour_stats(pb, pa, study, seed)

    cells["parent"] = [parent_code(c) if length > 2 else c for c in cells["code"]]
    by_parent = (
        cells.groupby("parent")
        .agg(
            cells_a=("a", lambda s: int((s > 0).sum())),
            cells_b=("b", lambda s: int((s > 0).sum())),
            cells_both=("cls", lambda s: int((s == "both").sum())),
            points_a=("a", "sum"),
            points_b=("b", "sum"),
            hh=("cluster", lambda s: int((s == "HH").sum())),
        )
        .reset_index()
        .sort_values(["cells_both", "points_a"], ascending=False)
        .reset_index(drop=True)
    )

    return CorrelationResult(
        name_a=name_a,
        name_b=name_b,
        length=length,
        cells=cells,
        pearson_r=float(pearson[0]),
        pearson_p=float(pearson[1]),
        spearman_rho=float(spearman[0]),
        spearman_p=float(spearman[1]),
        cells_a=cells_a,
        cells_b=cells_b,
        cells_both=both,
        cells_union=union,
        jaccard=both / union if union else 0.0,
        share_a_with_b=both / cells_a if cells_a else 0.0,
        share_b_with_a=both / cells_b if cells_b else 0.0,
        moran={k: v for k, v in moran.items()},
        nn_a_to_b=nn_ab,
        nn_b_to_a=nn_ba,
        by_parent=by_parent,
        islands=islands,
        extra={"cell_size_m": CELL_SIZE_M[length], "study_area": study_corr, "universe": universe},
    )

