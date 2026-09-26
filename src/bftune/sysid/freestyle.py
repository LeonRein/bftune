"""Low-confidence plant identification from ordinary flying (no chirp in the log).

The pilot's stick input (setpoint) is used as the instrument: above ~2 Hz it is essentially
exogenous to the rate loop. Stick moves carry little energy above ~15 Hz, so only the loop's
*gain* K is identifiable, and only to about ±40 % (synthetic twins, all craft classes). The motor
lag comes from the eRPM motor model (if available) and the pure delay from craft-class priors.
The robustness cases are widened (Identification.uncertainty) and the workbench switches to a
relative gate: no worse than the flown tune. Always recommend a chirp flight afterwards.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares

from ..flight import AXES, Flight
from ..model.controller import OperatingPoint, controller_fr
from ..model.params import Tune
from ..model.plant import Plant, plant_fr
from .fit import FitResult
from .frf import BandFRF, band_edges, saturation_mask
from .identify import AxisIdent, ChainCheck, Identification, _rms_err
from .motor import identify_motors
from .validate import freestyle_windows

PRIOR_T = 0.0015  # s, typical total transport delay (ESC + gyro + loop) — craft-priors.md
PRIOR_TZ = {"small": 0.05, "mid": 0.11, "large": 0.25}  # yaw reaction-torque zero by class


def freestyle_frf(fl: Flight, axis: int, seg_s: float = 2.0, min_sp_std: float = 20.0,
                  max_sat: float = 0.08) -> tuple[BandFRF, list]:
    n = int(seg_s * fl.fs)
    w = np.hanning(n)
    f = np.fft.rfftfreq(n, 1 / fl.fs)
    edges = band_edges(2.0, min(150.0, 0.45 * fl.fs), 8)
    nb = len(edges) - 1
    bi = np.digitize(f, edges) - 1
    inb = (bi >= 0) & (bi < nb)
    b_ = bi[inb]
    sig = {"y": fl.gyro_unfilt[:, axis], "yf": fl.gyro[:, axis], "u": fl.pidsum[:, axis], "D": fl.D[:, axis]}
    S = {k: np.zeros(nb, complex) for k in sig}
    P = {k: np.zeros(nb) for k in sig}
    Szz = np.zeros(nb)
    used = []
    for a, b in freestyle_windows(fl, min_len_s=seg_s):
        for s in range(a, b - n, n // 4):
            sl = slice(s, s + n)
            z = fl.setpoint[sl, axis]
            if np.std(z) < min_sp_std or np.mean(~saturation_mask(fl, sl)) > max_sat:
                continue
            used.append(s)
            Z = np.fft.rfft((z - z.mean()) * w)[inb]
            Szz += np.bincount(b_, weights=np.abs(Z) ** 2, minlength=nb)
            for k, x in sig.items():
                xs = x[sl]
                X = np.fft.rfft((xs - xs.mean()) * w)[inb]
                c = X * np.conj(Z)
                S[k] += np.bincount(b_, weights=c.real, minlength=nb) + 1j * np.bincount(b_, weights=c.imag, minlength=nb)
                P[k] += np.bincount(b_, weights=np.abs(X) ** 2, minlength=nb)
    ref = np.where(np.abs(S["u"]) > 0, S["u"], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        H = {k: v / ref for k, v in S.items()}
    coh = {k: np.abs(S[k]) ** 2 / np.maximum(Szz * P[k], 1e-30) for k in sig}
    fc = np.sqrt(edges[:-1] * edges[1:])
    return BandFRF(f=fc, H=H, coh=coh, valid_frac=np.ones(nb), n_runs=len(used), fs=fl.fs, extras={"edges": edges}), used


def _fit_gain_only(structure: str, fixed: dict, f, G, w) -> FitResult:
    ok = np.isfinite(G) & (w > 0)
    ff, GG, ww = f[ok], G[ok], w[ok]
    if len(ff) < 2:
        raise ValueError("not enough coherent stick-input bands (fly more varied stick moves, or a chirp)")

    def resid(x):
        p = dict(fixed, K=float(np.exp(x[0])))
        r = np.log(plant_fr(structure, p, ff) / GG)
        return ww * r.real  # magnitude only: phase carries the (fixed) lag/delay assumptions

    k0 = float(np.median(np.abs(GG) * 2 * np.pi * ff))
    sol = least_squares(resid, [np.log(max(k0, 1e-3))])
    p = dict(fixed, K=float(np.exp(sol.x[0])))
    r = np.log(plant_fr(structure, p, ff) / GG)
    return FitResult(Plant(structure, p), float(2 * sol.cost), 0.0, len(ff), 20 / np.log(10) * r.real,
                     np.degrees(r.imag), ff, ww)


def identify_freestyle(fl: Flight, tune: Tune, prior_T: float = PRIOR_T) -> Identification:
    motor = identify_motors(fl)
    dt = 1.0 / fl.loop_hz
    time_scale = fl.loop_hz / (fl.fs * fl.log_ratio)
    notes = ["NO CHIRP in this log: plant gain identified from stick inputs only (low confidence); motor lag from "
             "eRPM telemetry, delay from priors. Robustness cases are widened. Fly a chirp set for a precise model."]
    axes: dict[int, AxisIdent] = {}
    armed = fl.mode(0)
    for axis in range(3):
        frf, used = freestyle_frf(fl, axis)
        if not used:
            notes.append(f"{AXES[axis]}: no usable stick activity")
            continue
        idx = np.unique(np.concatenate([np.arange(s, s + int(2 * fl.fs)) for s in used]))
        idx = idx[idx < fl.n]
        # the estimate is dominated by the samples where the stick moves fast: weight the operating
        # point the same way (flips at low throttle otherwise bias the scheduled gain)
        wgt = np.gradient(fl.setpoint[:, axis])[idx] ** 2
        wgt = wgt / wgt.sum() if wgt.sum() > 0 else np.full(len(idx), 1 / len(idx))
        mhz = (wgt @ fl.motor_hz[idx]) if fl.motor_hz is not None else np.full(4, 200.0)
        op = OperatingPoint(throttle=float(wgt @ fl.throttle[idx]), motor_hz=[float(x) for x in mhz],
                            vbat=float(wgt @ fl.vbat[idx]), label="freestyle")
        hz = float(np.mean(mhz))
        tau = float(motor.tau_at(hz)) if motor is not None else 0.02
        m_hz = float(np.median(fl.motor_hz[armed].mean(axis=1))) if fl.motor_hz is not None else hz
        size = "small" if m_hz > 450 else ("large" if m_hz < 120 else "mid")
        if axis < 2:
            structure, fixed = "integ_lag", {"tau": tau, "T": prior_T}
        else:
            structure, fixed = "integ_lag_zero", {"tau": tau, "T": prior_T, "tz": PRIOR_TZ[size]}
        use = (frf.f >= 3.0) & (frf.coh["y"] >= 0.4) & (frf.coh["u"] >= 0.4)
        w = np.sqrt(frf.coh["y"] * frf.coh["u"]) * use
        try:
            fit = _fit_gain_only(structure, fixed, frf.f, frf.H["y"], w)
        except ValueError as e:
            notes.append(f"{AXES[axis]}: {e}")
            continue
        C = controller_fr(tune, axis, op, frf.f, dt, fl.loop_hz, time_scale=time_scale)
        m = use & (frf.coh["yf"] >= 0.6)
        with np.errstate(invalid="ignore", divide="ignore"):
            fg = frf.H["yf"] / frf.H["y"]
        fg_db, fg_deg = _rms_err(fg, C.Fg, m.astype(float)) if m.any() else (float("nan"),) * 2
        plant = fit.plant
        plant.ref_motor_hz, plant.ref_vbat, plant.ref_throttle = hz, op.vbat, op.throttle
        band = (float(frf.f[use].min()) if use.any() else 3.0, float(frf.f[use].max()) if use.any() else 3.0)
        chain = ChainCheck(fg_db, fg_deg, 0.0, 0.0, band, bool(np.isnan(fg_db) or (fg_db < 1.5 and fg_deg < 12)),
                           ["freestyle mode: chain check on stick band only"])
        axes[axis] = AxisIdent(axis, frf, op, fit, {structure: fit}, band, chain, frf.H["y"], frf.H["yf"] / C.Fg,
                               band[1])
    idn = Identification(axes=axes, motor=motor, runs=[], dt=dt, time_scale=time_scale, notes=notes,
                         thrust_linear=tune.i("thrust_linear"), motor_output_limit=tune.i("motor_output_limit"))
    idn.source = "freestyle"
    idn.uncertainty = {"k_hi": 1.4, "k_lo": 0.7, "dT": 0.0008,  # synthetic twins: K within about ±40 %
                       "source": "no chirp: stick-input identification (±40 %, from synthetic twins)"}
    return idn
