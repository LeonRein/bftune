"""Loop metrics and linear closed-loop time responses."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..model import filters as flt
from ..model.controller import ControllerFR, OperatingPoint, controller_fr
from ..model.params import Tune
from ..model.plant import Plant


@dataclass
class LoopMetrics:
    fc: float  # gain crossover [Hz]
    pm: float  # phase margin [deg]
    gm_db: float  # gain margin [dB] (min over all -180° crossings)
    f180: float  # frequency of the limiting -180° crossing
    ms: float  # peak |S|
    ms_hz: float
    mt: float  # peak |T|
    dm_ms: float  # delay margin [ms]
    bw_s: float  # disturbance-rejection bandwidth: first f with |S| >= -3 dB
    ok: bool = True

    def as_dict(self) -> dict:
        return {k: float(v) if isinstance(v, (float, np.floating)) else v for k, v in self.__dict__.items()}


def freq_grid(f_max: float, n: int = 700, f_min: float = 0.5) -> np.ndarray:
    return np.geomspace(f_min, f_max, n)


def metrics(f: np.ndarray, L: np.ndarray) -> LoopMetrics:
    mag = np.abs(L)
    S = 1 / (1 + L)
    T = L * S
    # gain crossover: last downward crossing of |L| = 1 (conservative if multiple)
    idx = np.flatnonzero((mag[:-1] >= 1) & (mag[1:] < 1))
    if len(idx) == 0:
        fc, pm, dm = float("nan"), float("nan"), float("nan")
    else:
        k = idx[-1]
        t = np.log(mag[k]) / (np.log(mag[k]) - np.log(mag[k + 1]))
        fc = float(np.exp(np.log(f[k]) + t * (np.log(f[k + 1]) - np.log(f[k]))))
        ph = np.unwrap(np.angle(L))
        phc = ph[k] + t * (ph[k + 1] - ph[k])
        # phase margin relative to the nearest -180 (mod 360)
        pm = float(np.degrees(phc) + 180.0)
        pm = (pm + 180.0) % 360.0 - 180.0
        dm = pm / 360.0 / fc * 1000.0 if pm > 0 else 0.0
    # gain margin: all crossings of the real axis on the negative side (Im L changes sign with Re L < 0)
    im = L.imag
    cross = np.flatnonzero((np.sign(im[:-1]) != np.sign(im[1:])) & (L.real[:-1] < 0))
    gm, f180 = float("inf"), float("nan")
    for k in cross:
        t = im[k] / (im[k] - im[k + 1])
        re = L.real[k] + t * (L.real[k + 1] - L.real[k])
        if re < 0:
            g = -20 * np.log10(-re)
            if g < gm:
                gm, f180 = g, float(f[k] + t * (f[k + 1] - f[k]))
    sm = np.abs(S)
    ks = int(np.argmax(sm))
    bw = np.flatnonzero(sm >= 1 / np.sqrt(2))
    bw_s = float(f[bw[0]]) if len(bw) else float("nan")
    return LoopMetrics(
        fc=fc, pm=pm, gm_db=gm, f180=f180, ms=float(sm[ks]), ms_hz=float(f[ks]), mt=float(np.max(np.abs(T))),
        dm_ms=float(dm), bw_s=bw_s,
    )


def evaluate(
    tune: Tune, axis: int, plant: Plant, op: OperatingPoint, dt: float, loop_hz: float, time_scale: float = 1.0,
    f: np.ndarray | None = None,
) -> tuple[LoopMetrics, dict, ControllerFR]:
    f = f if f is not None else freq_grid(0.45 * loop_hz)
    C = controller_fr(tune, axis, op, f, dt, loop_hz, time_scale=time_scale)
    G = plant.fr(f)
    L = G * C.Cy
    S = 1 / (1 + L)
    resp = {"f": f, "L": L, "S": S, "T": L * S, "G": G, "Cy": C.Cy, "Cr": C.Cr, "Fg": C.Fg}
    return metrics(f, L), resp, C


# ----------------------------------------------------------------------------- time domain
def rc_smoothing_cutoff(tune: Tune, rx_hz: float) -> float:
    """rc.c rcSmoothingSetFilterCutoffs(): auto cutoff = max(15, rx*1.5/(1+factor/10))."""
    cut = tune.i("rc_smoothing_setpoint_cutoff")
    if cut == 0:
        cut = max(15.0, rx_hz * 1.5 / (1 + tune.i("rc_smoothing_auto_factor") / 10.0))
    return float(cut)


def rc_path_fr(tune: Tune, f: np.ndarray, rx_hz: float, dt: float, axis: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Transfer from a continuous stick-rate signal to (setpoint, feedforward) — fc/rc.c.

    Per RX frame (period Tr): speed = Δsetpoint*rx; S = PT1(speed); Δ = S - S_prev;
    boost = PT1(Δ)*rx*0.001*feedforward_boost; ff = S + boost (roll/pitch; yaw has no boost);
    lagged moving average over feedforward_averaging+1 frames. Frames are held until the
    next packet (ZOH), then setpoint and ff are PT3-smoothed at the RC-smoothing cutoff.
    Returns H_sp (deg/s per deg/s) and H_ff (deg/s^2 per deg/s) so that F = Kf * H_ff * stick.
    The jitter attenuator is taken as 1 (sticks moving) and yaw hold is ignored.
    """
    Tr = 1.0 / rx_hz
    s = 2j * np.pi * f
    zr1 = np.exp(-s * Tr)
    zoh = np.where(np.abs(s) > 0, (1 - zr1) / (s * Tr), 1.0)
    if tune.s("rc_smoothing") == "ON":
        pt3 = flt.fr_ptn(rc_smoothing_cutoff(tune, rx_hz), f, dt, 3)
    else:
        pt3 = np.ones_like(f, dtype=complex)
    H_sp = zoh * pt3
    sm = tune.i("feedforward_smooth_factor") / 100.0
    tau = (0.004 * sm) / (1 - sm) if sm < 1 else 1e9
    k = Tr / (Tr + tau) if tau > 0 else 1.0
    Hs = k / (1 - (1 - k) * zr1)
    speed = (1 - zr1) * rx_hz
    S = Hs * speed
    if axis < 2:
        boost = tune.i("feedforward_boost") * 0.001
        ff = S * (1 + boost * rx_hz * Hs * (1 - zr1))
    else:
        ff = S
    avg = {"OFF": 0, "2_POINT": 1, "3_POINT": 2, "4_POINT": 3}.get(tune.s("feedforward_averaging"), 0) + 1
    H_avg = sum(zr1**j for j in range(avg)) / avg
    H_ff = ff * H_avg * zoh * pt3
    return H_sp, H_ff


def step_response(
    tune: Tune, axis: int, plant: Plant, op: OperatingPoint, dt: float, loop_hz: float, rx_hz: float,
    amplitude: float = 500.0, ramp_s: float = 0.02, duration: float = 0.5, with_ff: bool = True,
    time_scale: float = 1.0, relax_i: bool = True,
) -> dict:
    """Response of gyro to a stick move (ramp of `ramp_s` to `amplitude` deg/s), FFT-based.

    During stick moves iterm_relax suppresses I (pid.c applyItermRelax) and d_max_advance
    raises D towards d_max, so by default the I path is removed and `op.d_boost` should be 1.
    """
    n = int(2 ** np.ceil(np.log2(4 * duration / dt)))
    f = np.fft.rfftfreq(n, dt)
    f[0] = 1e-6
    t = np.arange(n) * dt
    stick = np.clip(t / ramp_s, 0, 1) * amplitude
    stick[t > 2 * duration] = 0.0  # return to zero half way so the periodic FFT sees a pulse
    relaxed = relax_i and (tune.s("iterm_relax").startswith("RPY") or (axis < 2 and tune.s("iterm_relax").startswith("RP")))
    C = controller_fr(tune, axis, op, f, dt, loop_hz, time_scale=time_scale, include_i=not relaxed)
    G = plant.fr(f)
    H_sp, H_ff = rc_path_fr(tune, f, rx_hz, dt, axis)
    ff_path = tune.kf(axis) * H_ff if with_ff else 0.0
    U_ref = C.Cr * H_sp + ff_path
    Tfull = G * U_ref / (1 + G * C.Cy)
    Tsp = H_sp
    X = np.fft.rfft(stick)
    y = np.fft.irfft(Tfull * X, n)
    sp = np.fft.irfft(Tsp * X, n)
    u = np.fft.irfft(U_ref / (1 + G * C.Cy) * X, n)
    m = t <= duration
    return {"t": t[m], "stick": stick[m], "setpoint": sp[m], "gyro": y[m], "pidsum": u[m]}


def step_metrics(res: dict) -> dict:
    t, sp, y = res["t"], res["setpoint"], res["gyro"]
    target = sp[-1]
    if abs(target) < 1e-9:
        return {}
    yn, spn = y / target, sp / target
    i50_sp = np.argmax(spn >= 0.5)
    i50_y = np.argmax(yn >= 0.5)
    i90 = np.argmax(yn >= 0.9)
    overshoot = max(0.0, (yn.max() - 1) * 100)
    settle_idx = np.flatnonzero(np.abs(yn - 1) > 0.05)
    settle = t[settle_idx[-1]] if len(settle_idx) else 0.0
    # tracking lag: time shift minimizing error between setpoint and gyro
    lags = np.arange(0, int(0.05 / (t[1] - t[0])))
    errs = [np.mean((y[k:] - sp[: len(sp) - k]) ** 2) for k in lags]
    lag = lags[int(np.argmin(errs))] * (t[1] - t[0])
    return {
        "delay_50_ms": (t[i50_y] - t[i50_sp]) * 1000,
        "rise_90_ms": t[i90] * 1000,
        "overshoot_pct": overshoot,
        "settle_5pct_ms": settle * 1000,
        "tracking_lag_ms": lag * 1000,
        "peak_pidsum": float(np.max(np.abs(res["pidsum"]))),
    }
