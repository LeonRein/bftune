"""Fit parametric plant models to an instrument-variable FRF."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from ..model.plant import STRUCTURES, Plant, plant_fr

# (initial, lower, upper) for each parameter
_BOUNDS = {
    "K": (100.0, 0.5, 1e5),
    "tau": (0.02, 0.001, 0.3),
    "tau2": (0.003, 0.0002, 0.1),
    "T": (0.0015, 0.0, 0.012),
    "a": (1.0, 0.0, 30.0),
    "tz": (0.02, 0.0005, 0.5),
}


@dataclass
class FitResult:
    plant: Plant
    cost: float  # weighted residual sum of squares
    aic: float
    n: int
    residual_db: np.ndarray
    residual_deg: np.ndarray
    f: np.ndarray
    weights: np.ndarray


def weights_from_coherence(coh_y: np.ndarray, coh_u: np.ndarray, valid: np.ndarray, n_avg: np.ndarray | None = None) -> np.ndarray:
    """Inverse-variance style weights for log-FRF residuals.

    var(log G) ≈ (1-γ²)/(2 n γ²) for each of the two cross spectra; combine both.
    """
    gy = np.clip(coh_y, 1e-4, 0.9999)
    gu = np.clip(coh_u, 1e-4, 0.9999)
    var = (1 - gy) / gy + (1 - gu) / gu
    w = 1.0 / np.sqrt(var + 1e-3)
    w = w * np.clip(valid, 0, 1)
    if n_avg is not None:
        w = w * np.sqrt(n_avg)
    return w


def fit_structure(
    structure: str, f: np.ndarray, G: np.ndarray, w: np.ndarray, init: dict[str, float] | None = None
) -> FitResult:
    names = STRUCTURES[structure]
    x0, lo, hi = [], [], []
    K0 = float(np.median(np.abs(G[: max(3, len(G) // 4)]) * 2 * np.pi * f[: max(3, len(G) // 4)]))
    for nme in names:
        i0, l, h = _BOUNDS[nme]
        if init and nme in init:
            i0 = init[nme]
        elif nme == "K":
            i0 = K0 if np.isfinite(K0) and K0 > 0 else i0
        x0.append(i0)
        lo.append(l)
        hi.append(h)
    x0 = np.clip(x0, np.array(lo) + 1e-12, np.array(hi) - 1e-12)
    ok = np.isfinite(G) & (w > 0)
    ff, GG, ww = f[ok], G[ok], w[ok]

    def resid(x):
        p = dict(zip(names, x))
        r = np.log(plant_fr(structure, p, ff) / GG)
        return np.concatenate([ww * r.real, ww * r.imag])

    best = None
    starts = [x0]
    rng = np.random.default_rng(0)
    for _ in range(6):
        starts.append(np.exp(np.log(np.maximum(x0, 1e-6)) + rng.normal(0, 0.6, len(x0))).clip(np.array(lo) + 1e-12, np.array(hi) - 1e-12))
    for s in starts:
        try:
            sol = least_squares(resid, s, bounds=(lo, hi), x_scale="jac", max_nfev=4000)
        except ValueError:
            continue
        if best is None or sol.cost < best.cost:
            best = sol
    p = dict(zip(names, best.x))
    r = np.log(plant_fr(structure, p, ff) / GG)
    n = len(ff) * 2
    cost = float(2 * best.cost)
    aic = n * np.log(cost / n + 1e-12) + 2 * len(names)
    return FitResult(
        plant=Plant(structure, p),
        cost=cost,
        aic=float(aic),
        n=n,
        residual_db=20 / np.log(10) * r.real,
        residual_deg=np.degrees(r.imag),
        f=ff,
        weights=ww,
    )


def fit_best(f, G, w, structures: list[str], min_aic_gain: float = 4.0) -> tuple[FitResult, dict[str, FitResult]]:
    """Fit all structures; pick the simplest one within `min_aic_gain` of the best AIC."""
    fits = {s: fit_structure(s, f, G, w) for s in structures}
    order = sorted(fits.values(), key=lambda r: r.aic)
    best = order[0]
    # prefer fewer params if nearly as good
    for r in sorted(fits.values(), key=lambda r: len(STRUCTURES[r.plant.structure])):
        if r.aic <= best.aic + min_aic_gain:
            return r, fits
    return best, fits
