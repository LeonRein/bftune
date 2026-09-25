"""Linearized Betaflight rate controller at an operating point (frequency domain).

Signal flow (betaflight/src/main/flight/pid.c, sensors/gyro_filter_impl.c):

    gyroUnfilt --Fg(z)--> gyroADCf ---> err = sp - gyroADCf
    P = Kp * err            (yaw: PT1(yaw_lowpass_hz) on the P output; TPA only in PD mode)
    I = I + Ki*dT*err       (iterm_relax scales err during fast stick moves)
    D = -Kd*dmax*TPA * (Fd(z) gyroADCf)[n]-[n-1] / dT        (D on measurement)
    F = Kf * d(setpoint_rc)/dt   (from RC, not part of the feedback loop)
    u = P + I + D + F      (pidSum, 1000 = full authority)

The plant G is identified from pidSum -> gyroUnfilt, so the loop gain is
    L(z) = G(z) * Fg(z) * [Kp*Hyaw(z) + Ki*dT/(1-z^-1) + Kd*TPA*dmax*Fd(z)*(1-z^-1)/dT]
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from . import filters as flt
from .params import Tune


@dataclass
class OperatingPoint:
    throttle: float = 0.3  # mixer throttle 0..1
    motor_hz: list[float] = field(default_factory=lambda: [200.0] * 4)  # mechanical Hz per motor
    vbat: float = 15.2
    d_boost: float = 0.0  # 0 = base D, 1 = D at d_max
    dyn_notch_hz: list[float] | None = None  # centres the dyn notch is tracking (None → worst case)
    tpa_low_active: bool = False
    label: str = ""


def rpm_notch_fr(tune: Tune, op: OperatingPoint, f: np.ndarray, dt: float) -> np.ndarray:
    """Product of all RPM notches (rpm_filter.c) at the given motor frequencies."""
    h = np.ones_like(f, dtype=complex)
    harmonics = tune.i("rpm_filter_harmonics")
    if harmonics <= 0 or str(tune.values.get("dshot_bidir", "ON")).upper() in ("OFF", "0"):
        return h  # RPM filter needs bidirectional DShot telemetry (rpm_filter.c)
    weights = tune.ints("rpm_filter_weights")
    q = tune.i("rpm_filter_q") / 100.0
    fmin = tune.i("rpm_filter_min_hz")
    fade = tune.i("rpm_filter_fade_range_hz")
    nyq_lim = 0.48 / dt
    for mhz in op.motor_hz:
        for hn in range(1, harmonics + 1):
            w = min(max(weights[hn - 1] / 100.0, 0.0), 1.0)
            if w <= 0:
                continue
            fc = min(max(hn * mhz, fmin), nyq_lim)
            fade_w = min(max((fc - fmin) / fade, 0.0), 1.0) if fade > 0 else 1.0
            w *= fade_w
            if w <= 0:
                continue
            h *= flt.fr_svf_notch(fc, q, f, dt, w)
    return h


def dyn_notch_fr(tune: Tune, op: OperatingPoint, f: np.ndarray, dt: float, loop_hz: float) -> np.ndarray:
    h = np.ones_like(f, dtype=complex)
    if not tune.dyn_notch_enabled(loop_hz):
        return h
    count = tune.i("dyn_notch_count")
    q = tune.i("dyn_notch_q") / 100.0
    fmin, fmax = tune.i("dyn_notch_min_hz"), tune.i("dyn_notch_max_hz")
    centres = op.dyn_notch_hz
    if centres is None:
        centres = [fmin] * count  # worst case for phase lag at crossover
    for c in centres[:count]:
        h *= flt.fr_svf_notch(min(max(c, fmin), fmax), q, f, dt)
    return h


def gyro_lpf1_hz(tune: Tune, throttle: float) -> float:
    if tune.i("gyro_lpf1_dyn_min_hz") > 0 and tune.i("gyro_lpf1_dyn_max_hz") >= tune.i("gyro_lpf1_dyn_min_hz"):
        return flt.dyn_lpf_cutoff(
            tune.i("gyro_lpf1_dyn_min_hz"), tune.i("gyro_lpf1_dyn_max_hz"), tune.i("gyro_lpf1_dyn_expo"), throttle
        )
    return float(tune.i("gyro_lpf1_static_hz"))


def dterm_lpf1_hz(tune: Tune, throttle: float) -> float:
    if tune.i("dterm_lpf1_dyn_min_hz") > 0 and tune.i("dterm_lpf1_dyn_max_hz") >= tune.i("dterm_lpf1_dyn_min_hz"):
        return flt.dyn_lpf_cutoff(
            tune.i("dterm_lpf1_dyn_min_hz"), tune.i("dterm_lpf1_dyn_max_hz"), tune.i("dterm_lpf1_dyn_expo"), throttle
        )
    return float(tune.i("dterm_lpf1_static_hz"))


def gyro_chain_fr(tune: Tune, op: OperatingPoint, f: np.ndarray, dt: float, loop_hz: float | None = None) -> np.ndarray:
    """gyroUnfilt -> gyroADCf: lpf2 -> RPM notches -> static notches -> lpf1 -> dyn notch."""
    loop_hz = loop_hz or 1.0 / dt
    denom = max(1, tune.i("pid_process_denom")) if "pid_process_denom" in tune.values else 1
    dt_g = dt / denom  # lpf2 runs at the gyro sample rate (gyro.c gyroUpdate)
    if tune.i("gyro_lpf2_static_hz") > 0:
        h = flt.fr_lowpass(tune.s("gyro_lpf2_type"), tune.i("gyro_lpf2_static_hz"), f, dt_g)
    else:
        # downsampling by averaging the gyro samples of one PID period (gyro_filter_impl.c)
        zg = flt.zinv(f, dt_g)
        h = sum(zg**k for k in range(denom)) / denom
    h = h * rpm_notch_fr(tune, op, f, dt)
    for n in (1, 2):
        hz, cut = tune.i(f"gyro_notch{n}_hz"), tune.i(f"gyro_notch{n}_cutoff")
        if hz > 0 and 0 < cut < hz:
            h = h * flt.fr_svf_notch(hz, flt.notch_q_from_cutoff(hz, cut), f, dt)
    h = h * flt.fr_lowpass(tune.s("gyro_lpf1_type"), gyro_lpf1_hz(tune, op.throttle), f, dt)
    h = h * dyn_notch_fr(tune, op, f, dt, loop_hz)
    return h


def dterm_chain_fr(tune: Tune, op: OperatingPoint, f: np.ndarray, dt: float) -> np.ndarray:
    """gyroADCf -> filtered D input: notch -> lpf1 -> lpf2 (pid.c)."""
    h = np.ones_like(f, dtype=complex)
    hz, cut = tune.i("dterm_notch_hz"), tune.i("dterm_notch_cutoff")
    nyq95 = 0.5 / dt * 0.95
    if hz > nyq95:
        hz = int(nyq95) if cut < nyq95 else 0
    if hz > 0 and 0 < cut < hz:
        h = h * flt.fr_svf_notch(hz, flt.notch_q_from_cutoff(hz, cut), f, dt)
    h = h * flt.fr_lowpass(tune.s("dterm_lpf1_type"), dterm_lpf1_hz(tune, op.throttle), f, dt)
    h = h * flt.fr_lowpass(tune.s("dterm_lpf2_type"), tune.i("dterm_lpf2_static_hz"), f, dt)
    return h


@dataclass
class ControllerFR:
    f: np.ndarray
    Fg: np.ndarray  # gyro filter chain
    Fd: np.ndarray  # dterm chain (after Fg)
    P: np.ndarray  # P path per unit error (includes yaw lowpass)
    I: np.ndarray  # I path per unit error
    D: np.ndarray  # D path per unit gyroADCf (sign: positive = opposes motion)
    Cy: np.ndarray  # feedback path from gyroUnfilt to pidSum (u = Cr*r - Cy*y)
    Cr: np.ndarray  # reference path from setpoint to pidSum (without feedforward)


def controller_fr(
    tune: Tune, axis: int, op: OperatingPoint, f: np.ndarray, dt: float, loop_hz: float | None = None,
    include_i: bool = True, time_scale: float = 1.0,
) -> ControllerFR:
    """Controller responses at real frequencies `f`.

    dt is the *nominal* loop period used by the firmware for all coefficients;
    time_scale = actual_loop_period / nominal (gyro-clock skew, e.g. 123/125 µs).
    """
    f_real = f
    f = np.asarray(f, dtype=float) * time_scale
    if time_scale != 1.0:
        # RPM notches (scheduler-corrected dt) and the dyn notch (tracks a measured peak)
        # sit at real frequencies; express them in the nominal-dt frequency coordinate.
        op = replace(
            op,
            motor_hz=[m * time_scale for m in op.motor_hz],
            dyn_notch_hz=None if op.dyn_notch_hz is None else [c * time_scale for c in op.dyn_notch_hz],
        )
    z1 = flt.zinv(f, dt)
    Fg = gyro_chain_fr(tune, op, f, dt, loop_hz)
    Fd = dterm_chain_fr(tune, op, f, dt)
    tpa = tune.tpa_factor(op.throttle, op.tpa_low_active)
    kp = tune.kp(axis) * (tpa if tune.s("tpa_mode") == "PD" else 1.0)
    P = kp * np.ones_like(f, dtype=complex)
    if axis == 2 and tune.i("yaw_lowpass_hz") > 0:
        P = P * flt.fr_ptn(tune.i("yaw_lowpass_hz"), f, dt, 1)
    I = tune.ki(axis) * dt / (1 - z1) if include_i else np.zeros_like(P)
    kd = tune.kd(axis) + op.d_boost * (tune.kd_max(axis) - tune.kd(axis))
    D = kd * tpa * Fd * (1 - z1) / dt
    Cy = Fg * (P + I + D)
    Cr = P + I
    return ControllerFR(f=f_real, Fg=Fg, Fd=Fd, P=P, I=I, D=D, Cy=Cy, Cr=Cr)


def loop_responses(G: np.ndarray, C: ControllerFR) -> dict[str, np.ndarray]:
    """Standard closed-loop responses for plant G (pidSum -> gyroUnfilt)."""
    L = G * C.Cy
    S = 1.0 / (1.0 + L)
    T_ry = G * C.Cr * S  # setpoint -> gyro (no feedforward)
    return {"L": L, "S": S, "T": 1 - S, "T_ry": T_ry, "CS": C.Cy * S}
