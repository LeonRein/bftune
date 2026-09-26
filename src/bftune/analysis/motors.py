"""Motor health from the log: rpm-collapse events classified, and per-motor consistency.

An rpm collapse (one motor far below the others) has three very different causes:
- `stall`: the motor was commanded high but its rpm fell (ESC desync, prop strike, bearing) - a problem;
- `mixer`: the mixer itself commanded the motor down (hard flips/rolls saturate the mixer; the
  diagonal pair drops to the idle floor) - normal, not a fault;
- `crash`: the gyro pegged or the quad was disarmed shortly after - the crash explains it.
"""

from __future__ import annotations

import numpy as np

from ..flight import Flight


def _segments(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    idx = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    return [(a, b) for a, b in zip(idx[::2], idx[1::2]) if b - a >= min_len]


def rpm_events(fl: Flight) -> list[dict]:
    if fl.motor_hz is None or fl.motor.size == 0:
        return []
    armed = fl.mode(0)
    med = np.median(fl.motor_hz, axis=1, keepdims=True)
    low = fl.motor_hz < 0.3 * np.maximum(med, 1.0)
    gyro_max = np.max(np.abs(fl.gyro), axis=1)
    out = []
    n_min = max(2, int(0.005 * fl.fs))
    for m in range(fl.motor_hz.shape[1]):
        for a, b in _segments(low[:, m] & armed & (fl.throttle > 0.05), n_min):
            cmd = float(np.mean(fl.motor[a:b, m]))
            others = float(np.median(np.delete(fl.motor[a:b], m, axis=1)))
            w0, w1 = max(0, a - int(0.5 * fl.fs)), min(fl.n, b + int(0.5 * fl.fs))
            crash = gyro_max[w0:w1].max() >= 1900 or not armed[w1 - 1]
            if crash:
                kind = "crash"
            elif cmd < 0.2 or cmd < 0.5 * others:
                kind = "mixer"
            elif cmd >= 0.35 or cmd >= others:
                kind = "stall"
            else:
                kind = "unclear"
            out.append({"motor": m + 1, "t": round(float(fl.t[a]), 2), "ms": round(1000 * (b - a) / fl.fs, 1),
                        "throttle": round(float(np.mean(fl.throttle[a:b])), 2), "command": round(cmd, 2),
                        "others_command": round(others, 2),
                        "rpm_hz": round(float(np.min(fl.motor_hz[a:b, m])), 1),
                        "others_hz": round(float(np.median(np.delete(fl.motor_hz[a:b], m, axis=1))), 1), "kind": kind})
    return sorted(out, key=lambda e: e["t"])


def motor_health(fl: Flight) -> dict:
    """Per motor: rpm per command (same command should give the same rpm), telemetry jitter, hover share."""
    if fl.motor_hz is None or fl.motor.size == 0:
        return {}
    armed = fl.mode(0)
    hover = armed & (np.abs(fl.throttle - np.median(fl.throttle[armed])) < 0.08)
    res = {"motors": []}
    for m in range(fl.motor_hz.shape[1]):
        c, h = fl.motor[armed, m], fl.motor_hz[armed, m]
        sel = (c > 0.15) & (c < 0.6) & (h > 5)
        slope = float(np.polyfit(c[sel], h[sel], 1)[0]) if sel.sum() > 100 else float("nan")
        d = np.diff(fl.motor_hz[armed, m])
        jitter = float(np.median(np.abs(d - np.median(d)))) if d.size else float("nan")
        res["motors"].append({"motor": m + 1, "hover_command": round(float(np.median(fl.motor[hover, m])), 3),
                              "hz_per_command": round(slope, 1), "telemetry_jitter_hz": round(jitter, 2),
                              "zero_rpm_samples": int(np.sum((fl.motor_hz[armed, m] < 1) & (fl.motor[armed, m] > 0.1)))})
    slopes = np.array([x["hz_per_command"] for x in res["motors"]])
    if np.all(np.isfinite(slopes)):
        res["rpm_per_command_spread_pct"] = round(float(100 * (slopes.max() - slopes.min()) / slopes.mean()), 1)
    return res


def format_motors(name: str, events: list[dict], health: dict) -> str:
    out = [f"== {name}"]
    if health:
        out.append("  motor  hover cmd  Hz per cmd  telemetry jitter  zero-rpm samples")
        for m in health["motors"]:
            out.append(f"  {m['motor']:5d}  {m['hover_command']:9.3f}  {m['hz_per_command']:10.1f}  {m['telemetry_jitter_hz']:16.2f}"
                       f"  {m['zero_rpm_samples']:16d}")
        if "rpm_per_command_spread_pct" in health:
            out.append(f"  rpm per command spread between motors: {health['rpm_per_command_spread_pct']} % "
                       "(> ~8 %: a weaker motor, bearing or prop)")
    counts = {k: sum(e["kind"] == k for e in events) for k in ("stall", "unclear", "mixer", "crash")}
    out.append(f"  rpm-collapse events: {counts['stall']} stall, {counts['unclear']} unclear, {counts['mixer']} mixer "
               f"(commanded down in hard moves: normal), {counts['crash']} crash")
    for e in events:
        if e["kind"] in ("stall", "unclear"):
            out.append(f"    {e['kind'].upper():7s} motor {e['motor']} t={e['t']} s for {e['ms']} ms: commanded {e['command']} "
                       f"(others {e['others_command']}), rpm {e['rpm_hz']} Hz vs others {e['others_hz']} Hz, throttle {e['throttle']}")
    crash = sorted({e["t"] for e in events if e["kind"] == "crash"})
    if crash:
        out.append(f"    crash at t={', '.join(str(t) for t in crash)} s: pass `--exclude T0:T1` around it to diagnose/errspec/logs/"
                   "analyze so the crash does not dominate the statistics")
    return "\n".join(out)
