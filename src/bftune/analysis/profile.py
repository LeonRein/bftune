"""How this quad was flown, measured from the log: the scales every judgement should use instead of 5" numbers.

- hover throttle and hover motor frequency (frequency bands of the diagnostics scale with the latter: prop size
  sets motor speed and, with it, the control bandwidth);
- the throttle the pilot actually uses (percentiles: the "mid" design case sits where the pilot flies, not at 50 %);
- the pilot's real stick moves (amplitude and rise time of typical and fast moves: the step tests use them);
- the RC link rate, from the log header or estimated from how often rcCommand changes.

Every value says where it came from, so the agent can check it and override it.
"""

from __future__ import annotations

import numpy as np

from ..flight import AXES, BOX_ANGLE, BOX_HORIZON, Flight

# used only when the log cannot tell (labelled "convention" wherever they are used)
CONVENTION_FLICK = {"deg_s": 300.0, "ramp_ms": 50.0}
CONVENTION_SNAP_RAMP_MS = 30.0


def _acro(fl: Flight) -> np.ndarray:
    return fl.mode(0) & ~fl.mode(BOX_ANGLE) & ~fl.mode(BOX_HORIZON)


def hover(fl: Flight) -> tuple[float, float | None]:
    """(hover throttle, hover motor Hz) from armed samples with the sticks near centre."""
    armed = fl.mode(0)
    calm = armed & np.all(np.abs(fl.setpoint[:, :3]) < 30, axis=1) & (fl.throttle > 0.05)
    sel = calm if calm.sum() > fl.fs else armed
    if not sel.any():
        return 0.3, None
    # p30, not the median: in freestyle-only logs "sticks centred" is mostly forward cruise at more than hover throttle
    thr = float(np.percentile(fl.throttle[sel], 30))
    near = sel & (np.abs(fl.throttle - thr) < 0.05)
    hz = None
    if fl.motor_hz is not None and fl.motor_hz.size and near.any():
        hz = float(np.median(fl.motor_hz[near].mean(axis=1)))
    return thr, hz


def stick_moves(fl: Flight, axis: int) -> dict | None:
    """The pilot's stick moves on this axis: "flick" = a typical move (median amplitude and rise time), "snap" = a
    fast, large one (p90 amplitude, p10 rise time). Amplitude from the setpoint, rise time (0-100 %, from the
    20-80 % part) from the raw stick (rcCommand, before RC smoothing). A move = |rcCommand| rising past 100."""
    ok = _acro(fl)
    rc = np.abs(fl.rc[:, axis].astype(float))
    sp = fl.setpoint[:, axis]
    if not ok.any() or np.ptp(rc[ok]) < 50:
        return None
    n_look = int(0.3 * fl.fs)
    amps, rises = [], []
    last = -n_look
    for i in np.flatnonzero(ok[1:] & (rc[1:] >= 100) & (rc[:-1] < 100)) + 1:
        if i - last < n_look or i + n_look >= fl.n:
            continue
        peak = float(rc[i : i + n_look].max())
        if peak < 150:
            continue
        j20 = i - 1
        while j20 > 0 and rc[j20] > 0.2 * peak and i - j20 < n_look:
            j20 -= 1
        j80 = i + int(np.argmax(rc[i : i + n_look] >= 0.8 * peak))
        rises.append((j80 - j20) / fl.fs / 0.6 * 1000)
        amps.append(float(np.max(np.abs(sp[i : i + n_look]))))
        last = i
    if len(amps) < 5:
        return None
    a, r = np.array(amps), np.array(rises)
    return {"moves": len(a),
            "flick": {"deg_s": round(float(np.median(a))), "ramp_ms": round(float(np.median(r)), 1)},
            "snap": {"deg_s": round(float(np.percentile(a, 90))), "ramp_ms": round(float(np.percentile(r, 10)), 1)}}


def rx_rate(fl: Flight) -> tuple[float | None, str]:
    """RC link rate: the firmware's own estimate in the header, else how often rcCommand changes while the sticks move."""
    v = fl.log.header_int("rc_smoothing_rx_smoothed", 0) if hasattr(fl.log, "header_int") else 0
    if v and v > 0:
        return float(v), "log header (rc_smoothing_rx_smoothed)"
    rc = fl.rc[:, :3]
    ch = np.any(np.diff(rc, axis=0) != 0, axis=1)
    sec = int(fl.fs)
    if len(ch) < 5 * sec:
        return None, "unknown"
    per_s = np.add.reduceat(ch.astype(int), np.arange(0, len(ch) - sec, sec))
    est = float(np.percentile(per_s, 95))
    if est < 20:
        return None, "unknown"
    return min(est, fl.fs), "estimated from rcCommand updates (a lower bound if the log rate is below the link rate)"


def flight_profile(fl: Flight) -> dict:
    thr_h, hz_h = hover(fl)
    armed = fl.mode(0)
    thr = fl.throttle[armed] if armed.any() else fl.throttle
    rx, rx_src = rx_rate(fl)
    return {
        "hover_throttle": round(thr_h, 3),
        "hover_motor_hz": None if hz_h is None else round(hz_h, 1),
        "throttle_pct": {str(q): round(float(np.percentile(thr, q)), 3) for q in (50, 75, 90, 99)} if thr.size else {},
        "stick_moves": {AXES[a]: m for a in range(3) if (m := stick_moves(fl, a)) is not None},
        "rx_rate_hz": rx, "rx_rate_source": rx_src,
    }


def scaled_band(hover_motor_hz: float | None, lo_ratio: float, hi_ratio: float, fs: float,
                fallback: tuple[float, float]) -> tuple[float, float]:
    """A frequency band that scales with the hover motor frequency (ratios chosen so a 5" at ~170 Hz gets the
    band that was tuned on it); the fallback is used when the log has no rpm telemetry."""
    if not hover_motor_hz:
        lo, hi = fallback
    else:
        lo, hi = lo_ratio * hover_motor_hz, hi_ratio * hover_motor_hz
    return max(1.0, lo), min(hi, 0.45 * fs)
