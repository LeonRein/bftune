"""Model validation against the log.

1. Closed-loop chirp check: measured setpoint->gyro response (instrument-variable estimate
   S_zy/S_zr) vs the model G*Cr/(1+G*Cy) with the *logged* tune. Validates plant + filters +
   controller together at the identification operating point.
2. Freestyle replay: drive the linear closed-loop model with the logged setpoint and the
   logged F-term over windows of real flying; compare predicted and logged gyro.
   Throttle-dependent plant scheduling (motor model) is applied per window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..flight import BOX_ANGLE, BOX_HORIZON, Flight
from ..model.controller import OperatingPoint, controller_fr
from ..model.params import Tune
from .chirp import find_chirps
from .identify import Identification


@dataclass
class ClosedLoopCheck:
    axis: int
    f: np.ndarray
    T_meas: np.ndarray
    T_model: np.ndarray
    coh: np.ndarray
    rms_db: float
    rms_deg: float


def relaxed_axes(tune: Tune) -> set[int]:
    mode = tune.s("iterm_relax")
    return {0, 1, 2} if mode.startswith("RPY") else ({0, 1} if mode.startswith("RP") else set())


def closed_loop_check(fl: Flight, tune: Tune, idn: Identification) -> dict[int, ClosedLoopCheck]:
    """During a chirp iterm_relax (if active on the axis) suppresses I almost completely, so
    I is excluded on relaxed axes and kept on the others."""
    out = {}
    relaxed = relaxed_axes(tune)
    for axis, ai in idn.axes.items():
        fr = ai.frf
        if "r" not in fr.H:  # freestyle identification: setpoint is the instrument itself
            continue
        T_meas = fr.H["y"] / fr.H["r"]
        C = controller_fr(tune, axis, ai.op, fr.f, idn.dt, fl.loop_hz, include_i=axis not in relaxed,
                          time_scale=idn.time_scale)
        G = ai.plant.fr(fr.f)
        T_model = G * C.Cr / (1 + G * C.Cy)
        coh = np.minimum(fr.coh["y"], fr.coh["r"])
        m = (coh > 0.6) & (fr.f >= ai.fit_band[0])
        r = np.log(T_model[m] / T_meas[m])
        out[axis] = ClosedLoopCheck(
            axis, fr.f, T_meas, T_model, coh,
            float(np.sqrt(np.mean((20 / np.log(10) * r.real) ** 2))) if m.any() else float("nan"),
            float(np.sqrt(np.mean(np.degrees(r.imag) ** 2))) if m.any() else float("nan"),
        )
    return out


@dataclass
class ReplayResult:
    axis: int
    windows: list[dict]
    fit_pct: float  # overall 1 - ||e||/||y - mean|| in percent (NRMSE fit)
    t: np.ndarray
    y_log: np.ndarray
    y_pred: np.ndarray


def freestyle_windows(fl: Flight, min_len_s: float = 2.0, exclude_chirp: bool = True,
                      settle_s: float = 1.0) -> list[tuple[int, int]]:
    """Armed, acro (no angle/horizon), no chirp; skips `settle_s` after every mode change."""
    ok = fl.mode(0) & ~fl.mode(BOX_ANGLE) & ~fl.mode(BOX_HORIZON)
    changes = np.flatnonzero(np.diff(fl.mode_mask) != 0) + 1
    for c in changes:
        ok[c : c + int(settle_s * fl.fs)] = False
    if exclude_chirp:
        for r in find_chirps(fl):
            ok[r.start : r.end] = False
        if fl.debug_mode == 96:
            ok &= fl.debug[:, 1] < 0
    idx = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
    n_min = int(min_len_s * fl.fs)
    return [(a, b) for a, b in zip(idx[::2], idx[1::2]) if b - a >= n_min]


def replay(
    fl: Flight, tune: Tune, idn: Identification, axis: int, win_s: float = 0.5, include_i: bool = True
) -> ReplayResult | None:
    """Piecewise-linear replay: each window uses the plant scheduled at its mean motor speed."""
    if axis not in idn.axes:
        return None
    ai = idn.axes[axis]
    wins = freestyle_windows(fl)
    if not wins:
        return None
    n_win = int(win_s * fl.fs)
    ys, ps, ts, info = [], [], [], []
    pad = int(0.25 * fl.fs)
    for a, b in wins:
        for s in range(a + pad, b - n_win, n_win):
            e = s + n_win
            s0, e0 = s - pad, e  # include pre-roll so initial conditions settle
            r = fl.setpoint[s0:e0, axis]
            F = fl.F[s0:e0, axis]
            y = fl.gyro_unfilt[s0:e0, axis]
            n = e0 - s0
            nfft = int(2 ** np.ceil(np.log2(4 * n)))
            f = np.fft.rfftfreq(nfft, 1.0 / fl.fs)
            f[0] = 1e-3
            thr = float(np.mean(fl.throttle[s:e]))
            mhz = fl.motor_hz[s:e].mean(axis=0) if fl.motor_hz is not None else np.array(ai.op.motor_hz)
            plant = ai.plant.scaled(float(np.mean(mhz)), float(np.mean(fl.vbat[s:e])), idn.motor)
            op = OperatingPoint(throttle=thr, motor_hz=list(mhz), vbat=float(np.mean(fl.vbat[s:e])), d_boost=0.5)
            C = controller_fr(tune, axis, op, f, idn.dt, fl.loop_hz, include_i=include_i, time_scale=idn.time_scale)
            G = plant.fr(f)
            den = 1 + G * C.Cy
            # remove means / detrend to limit wrap-around, use linear-phase padding
            r0, F0 = r - r[0], F - F[0]
            R = np.fft.rfft(r0, nfft)
            FF = np.fft.rfft(F0, nfft)
            ypred = np.fft.irfft(G * (C.Cr * R + FF) / den, nfft)[:n] + y[0]
            if np.std(y[pad:]) < 15.0:  # no real maneuvering: gyro noise dominates, fit % meaningless
                continue
            ys.append(y[pad:])
            ps.append(ypred[pad:])
            ts.append(fl.t[s:e])
            err = y[pad:] - ypred[pad:]
            den_y = np.linalg.norm(y[pad:] - y[pad:].mean()) + 1e-9
            info.append(
                {
                    "t0": float(fl.t[s]),
                    "throttle": thr,
                    "motor_hz": float(np.mean(mhz)),
                    "fit_pct": float(100 * (1 - np.linalg.norm(err) / den_y)),
                    "rms_err": float(np.sqrt(np.mean(err**2))),
                    "rms_gyro": float(np.std(y[pad:])),
                }
            )
    if not ys:
        return None
    y_all, p_all = np.concatenate(ys), np.concatenate(ps)
    fit = 100 * (1 - np.linalg.norm(y_all - p_all) / (np.linalg.norm(y_all - y_all.mean()) + 1e-9))
    return ReplayResult(axis, info, float(fit), np.concatenate(ts), y_all, p_all)
