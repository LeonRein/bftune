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
    gm_db: float  # gain margin [dB]: the smaller of the upward and downward margins (both >= 0 when stable)
    f180: float  # frequency of the limiting -180° crossing
    ms: float  # peak |S|
    ms_hz: float
    mt: float  # peak |T|
    dm_ms: float  # delay margin [ms]
    bw_s: float  # disturbance-rejection bandwidth: first f with |S| >= -3 dB
    gm_up_db: float = float("inf")  # how much the loop gain may rise before instability
    gm_down_db: float = float("inf")  # how much it may fall (conditionally stable loops: I-term + lag at idle)
    ok: bool = True

    def as_dict(self) -> dict:
        return {k: float(v) if isinstance(v, (float, np.floating)) else v for k, v in self.__dict__.items()}


def freq_grid(f_max: float, n: int = 700, f_min: float = 0.5) -> np.ndarray:
    return np.geomspace(f_min, f_max, n)


def metrics(f: np.ndarray, L: np.ndarray, f_min: float = 0.0) -> LoopMetrics:
    """Margins of the loop L(f). `f_min` ignores frequencies below it (the idle case: a quad never stays at idle
    long enough for slower dynamics to matter)."""
    if f_min > 0:
        m = f >= f_min
        f, L = f[m], L[m]
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
    # A crossing with |L| < 1 limits how far the gain may RISE (g > 0 dB). A crossing with |L| > 1 (at low
    # frequency in conditionally stable loops, e.g. I-term + motor lag at idle) limits how far it may FALL:
    # its margin is -g. The old min-over-all-crossings reported that as a negative "GM" (looked unstable).
    up, down, f_up, f_down = float("inf"), float("inf"), float("nan"), float("nan")
    for k in cross:
        t = im[k] / (im[k] - im[k + 1])
        re = L.real[k] + t * (L.real[k + 1] - L.real[k])
        if re < 0:
            g = -20 * np.log10(-re)
            fk = float(f[k] + t * (f[k + 1] - f[k]))
            if g >= 0 and g < up:
                up, f_up = g, fk
            elif g < 0 and -g < down:
                down, f_down = -g, fk
    gm, f180 = (up, f_up) if up <= down else (down, f_down)
    sm = np.abs(S)
    ks = int(np.argmax(sm))
    bw = np.flatnonzero(sm >= 1 / np.sqrt(2))
    bw_s = float(f[bw[0]]) if len(bw) else float("nan")
    return LoopMetrics(
        fc=fc, pm=pm, gm_db=gm, f180=f180, ms=float(sm[ks]), ms_hz=float(f[ks]), mt=float(np.max(np.abs(T))),
        dm_ms=float(dm), bw_s=bw_s, gm_up_db=float(up), gm_down_db=float(down),
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
    The jitter attenuator is taken as 1 (sticks moving); feedforward_max_rate_limit is not
    modelled, so stick tests must stay well below the craft's max rate.
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
        # yaw: no boost, plus the yaw-hold element G*(sp - PT1(sp)) (rc.c; gain rescaled below 100 ms, pid_init.c)
        hold_t = tune.i("feedforward_yaw_hold_time")
        gain = tune.i("feedforward_yaw_hold_gain") * (150.0 / (hold_t + 50) if hold_t < 100 else 1.0)
        if gain > 0 and hold_t > 0:
            kh = Tr / (Tr + hold_t / 1000.0)
            ff = S + gain * (1 - kh / (1 - (1 - kh) * zr1))
        else:
            ff = S
    avg = {"OFF": 0, "2_POINT": 1, "3_POINT": 2, "4_POINT": 3}.get(tune.s("feedforward_averaging"), 0) + 1
    H_avg = sum(zr1**j for j in range(avg)) / avg
    H_ff = ff * H_avg * zoh * pt3
    return H_sp, H_ff


def reference_fr(tune: Tune, axis: int, plant: Plant, op: OperatingPoint, f: np.ndarray, dt: float, loop_hz: float,
                 rx_hz: float, time_scale: float = 1.0, relax_i: bool = True, with_ff: bool = True):
    """(stick -> gyro, stick -> logged setpoint) frequency responses with RC smoothing, FF and the loop."""
    relaxed = relax_i and (tune.s("iterm_relax").startswith("RPY") or (axis < 2 and tune.s("iterm_relax").startswith("RP")))
    C = controller_fr(tune, axis, op, f, dt, loop_hz, time_scale=time_scale, include_i=not relaxed)
    G = plant.fr(f)
    H_sp, H_ff = rc_path_fr(tune, f, rx_hz, dt, axis)
    ff_path = tune.kf(axis) * H_ff if with_ff else 0.0
    U_ref = C.Cr * H_sp + ff_path
    return G * U_ref / (1 + G * C.Cy), H_sp, U_ref / (1 + G * C.Cy)


def step_response(
    tune: Tune, axis: int, plant: Plant, op: OperatingPoint, dt: float, loop_hz: float, rx_hz: float,
    amplitude: float = 500.0, ramp_s: float = 0.02, duration: float = 0.5, with_ff: bool = True,
    time_scale: float = 1.0, relax_i: bool = True, ref: str = "stick",
) -> dict:
    """Response of gyro to a stick move (ramp of `ramp_s` to `amplitude` deg/s), FFT-based.

    ref="setpoint": the input is the *logged* setpoint instead, which Betaflight records after RC smoothing
    (blackbox.c logs pidGetPreviousSetpoint = getSetpointRate, pid.c), so the answer is gyro/setpoint = T/H_sp with
    feedforward (from the raw stick) arriving as lead - the same definition as the step measured from a log.

    During stick moves iterm_relax suppresses I (pid.c applyItermRelax) and d_max_advance
    raises D towards d_max, so by default the I path is removed and `op.d_boost` should be 1.
    """
    n = int(2 ** np.ceil(np.log2(4 * duration / dt)))
    f = np.fft.rfftfreq(n, dt)
    f[0] = 1e-6
    t = np.arange(n) * dt
    stick = np.clip(t / ramp_s, 0, 1) * amplitude
    stick[t > 2 * duration] = 0.0  # return to zero half way so the periodic FFT sees a pulse
    Tfull, Tsp, Tu = reference_fr(tune, axis, plant, op, f, dt, loop_hz, rx_hz, time_scale, relax_i, with_ff)
    X = np.fft.rfft(stick)
    if ref == "setpoint":
        Tfull, Tu, Tsp = Tfull / Tsp, Tu / Tsp, np.ones_like(Tsp)
    y = np.fft.irfft(Tfull * X, n)
    sp = np.fft.irfft(Tsp * X, n)
    u = np.fft.irfft(Tu * X, n)
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
    k = int(np.argmax(yn))
    after = yn[k : int(np.searchsorted(t, t[k] + 0.1)) + 1]
    undershoot = max(0.0, (1 - float(after.min())) * 100) if yn[k] > 1 else 0.0  # dip after the peak: end-of-flick bounce
    settle_idx = np.flatnonzero(np.abs(yn - 1) > 0.05)
    settle = t[settle_idx[-1]] if len(settle_idx) else 0.0
    # tracking lag: time shift minimizing error between setpoint and gyro
    lags = np.arange(0, int(0.05 / (t[1] - t[0])))  # >= 0; a lead (strong FF) shows as 0 here, see as-flown
    errs = [np.mean((y[k:] - sp[: len(sp) - k]) ** 2) for k in lags]
    lag = lags[int(np.argmin(errs))] * (t[1] - t[0])
    # end-to-end latency the pilot feels: gyro vs the raw stick (includes RC smoothing)
    stick = res["stick"]
    errs_s = [np.mean((y[k:] - stick[: len(stick) - k]) ** 2) for k in lags]
    stick_lag = lags[int(np.argmin(errs_s))] * (t[1] - t[0])
    return {
        "delay_50_ms": (t[i50_y] - t[i50_sp]) * 1000,
        "rise_90_ms": t[i90] * 1000,
        "overshoot_pct": overshoot,
        "undershoot_pct": undershoot,
        "settle_5pct_ms": settle * 1000,
        "tracking_lag_ms": lag * 1000,
        "stick_lag_ms": stick_lag * 1000,
        "peak_pidsum": float(np.max(np.abs(res["pidsum"]))),
    }
