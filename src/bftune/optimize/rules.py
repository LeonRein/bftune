"""Settings chosen by physics rules and expert judgement rather than by the loop optimizer.

Each rule returns (value, reason) so the report can explain every change.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..analysis.loop import rc_smoothing_cutoff, step_metrics, step_response
from ..flight import AXES, Flight
from ..model.controller import OperatingPoint
from ..model.params import FEEDFORWARD_SCALE, Tune, thrust_linear_slope
from ..sysid.identify import Identification


@dataclass
class Decision:
    key: str
    value: str
    reason: str


def rx_rate_hz(fl: Flight) -> float:
    v = fl.log.header_int("rc_smoothing_rx_smoothed", 0) or 0
    return float(v) if v > 0 else 250.0


def max_rate(fl: Flight, axis: int) -> float:
    """Max stick rate [deg/s] from the rate profile (ACTUAL rates; conservative fallback 670)."""
    ax = AXES[axis]
    c = fl.cfg
    if c.str("rates_type", "ACTUAL").upper() == "ACTUAL" and c.get(f"{ax}_srate") is not None:
        return float(max(c.int(f"{ax}_rc_rate") * 10, c.int(f"{ax}_srate") * 10))
    return 670.0


def tune_feedforward(fl: Flight, idn: Identification, tune: Tune, style: str = "freestyle",
                     overshoot_max: float | None = None) -> list[Decision]:
    """Pick F per axis: minimum tracking lag for a fast stick move without exceeding the
    overshoot target. Evaluated with the identified plant (I relaxed, D at d_max, as in flight).

    The physics reference is F_ideal = 1/(K * FEEDFORWARD_SCALE * 0.01): feedforward that
    alone produces the requested angular acceleration on an integrator plant with gain K.
    """
    rx = rx_rate_hz(fl)
    target = overshoot_max if overshoot_max is not None else {"race": 12.0, "freestyle": 10.0, "cinematic": 3.0}.get(style, 10.0)
    out = []
    for axis, ai in idn.axes.items():
        ax = AXES[axis]
        op = OperatingPoint(throttle=ai.op.throttle, motor_hz=ai.op.motor_hz, vbat=ai.op.vbat, d_boost=1.0,
                            dyn_notch_hz=ai.op.dyn_notch_hz)
        plant = ai.plant
        scale = thrust_linear_slope(tune.i("thrust_linear"), op.throttle) / thrust_linear_slope(idn.thrust_linear, op.throttle)
        p = dict(plant.params)
        p["K"] *= scale
        from ..model.plant import Plant

        pl = Plant(plant.structure, p)
        f_ideal = 1.0 / (p["K"] * FEEDFORWARD_SCALE * 0.01)
        # stay below the feedforward_max_rate_limit region (clips FF near max rate, not modelled)
        a_fast = min(600.0, 0.6 * max_rate(fl, axis))
        best = None
        rows = []
        for F in range(40, 261, 5):
            t = tune.copy().set(f"f_{ax}", F)
            # a crisp flick (300 deg/s in 50 ms) and a full-rate snap (600 deg/s in 30 ms)
            m1 = step_metrics(step_response(t, axis, pl, op, idn.dt, fl.loop_hz, rx, amplitude=300, ramp_s=0.05,
                                            time_scale=idn.time_scale))
            m2 = step_metrics(step_response(t, axis, pl, op, idn.dt, fl.loop_hz, rx, amplitude=a_fast, ramp_s=0.03,
                                            time_scale=idn.time_scale))
            ov = max(m1["overshoot_pct"], m2["overshoot_pct"])
            lag = 0.5 * (m1["tracking_lag_ms"] + m2["tracking_lag_ms"])
            rows.append((F, ov, lag))
            if ov <= target and (best is None or lag < best[2] - 1e-6):
                best = (F, ov, lag)
        if best is None:
            best = min(rows, key=lambda r: r[1])
        F, ov, lag = best
        if axis == 2:
            reason = (f"yaw FF {F}: yaw plant is proportional above {1/(2*np.pi*p.get('tz', 0.1)):.1f} Hz "
                      f"(reaction torque), so FF mainly speeds the first 50 ms; modelled overshoot {ov:.0f}%, lag {lag:.1f} ms")
        else:
            reason = (f"minimum tracking lag ({lag:.1f} ms) with ≤{target:.0f}% overshoot on 300°/s-in-50ms and "
                      f"{a_fast:.0f}°/s-in-30ms stick moves (modelled {ov:.0f}%); physics ideal F≈{f_ideal:.0f}")
        out.append(Decision(f"f_{ax}", str(int(F)), reason))
    return out


def judgement(fl: Flight, idn: Identification, tune: Tune, style: str = "freestyle") -> list[Decision]:
    """Link- and flight-style dependent settings (RC smoothing, idle, I-term details)."""
    out: list[Decision] = []
    rx = rx_rate_hz(fl)
    # RC smoothing: lower factor -> higher PT3 cutoff -> less stick latency, more RC jitter in FF.
    factor = {"race": 25, "freestyle": 35, "cinematic": 60}.get(style, 35)
    cur = tune.i("rc_smoothing_auto_factor")
    t2 = tune.copy().set("rc_smoothing_auto_factor", factor)
    c_old, c_new = rc_smoothing_cutoff(tune, rx), rc_smoothing_cutoff(t2, rx)
    if factor != cur:
        # PT3 group delay ≈ 3/(2π·fc·1.96) at low frequency
        d_old = 3 / (2 * np.pi * c_old * 1.961) * 1000
        d_new = 3 / (2 * np.pi * c_new * 1.961) * 1000
        out.append(Decision("rc_smoothing_auto_factor", str(factor),
                            f"{rx:.0f} Hz link: setpoint/FF smoothing cutoff {c_old:.0f}→{c_new:.0f} Hz, stick latency "
                            f"{d_old:.1f}→{d_new:.1f} ms"))
    # Dynamic idle: keep a speed floor in dives/propwash (authority and desync protection).
    armed = fl.mode(0)
    low = armed & (fl.throttle < 0.03)
    if fl.motor_hz is not None and low.sum() > 200:
        idle_hz = float(np.percentile(fl.motor_hz[low].mean(axis=1), 25))
        rpm = idle_hz * 60.0
        val = int(np.clip(round(0.9 * rpm / 100), 20, 120))
        if tune.i("dyn_idle_min_rpm") == 0 or abs(tune.i("dyn_idle_min_rpm") - val) > 5:
            out.append(Decision("dyn_idle_min_rpm", str(val),
                                f"natural idle ≈{rpm:.0f} rpm; floor at 90% keeps motor authority during dives and "
                                f"flips (propwash) without raising normal idle"))
    # RPM filter weights: every harmonic that exists in gyroUnfilt should be notched at full depth;
    # a notch at >= 2x the motor frequency costs < 0.5 deg of phase at crossover.
    if tune.values.get("rpm_filter_weights") != "100,100,100" and tune.i("rpm_filter_harmonics") > 0:
        out.append(Decision("rpm_filter_weights", "100,100,100",
                            "motor harmonics are visible in the unfiltered gyro; full-depth RPM notches cost "
                            "almost no phase at crossover"))
    # I-term relax cutoff: lower = less bounce-back after flips, higher = tighter tracking
    relax = {"race": 20, "freestyle": 15, "cinematic": 10}.get(style, 15)
    if tune.i("iterm_relax_cutoff") != relax:
        out.append(Decision("iterm_relax_cutoff", str(relax), f"{style}: I-term relax at {relax} Hz"))
    return out
