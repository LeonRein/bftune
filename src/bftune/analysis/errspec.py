"""Free-flight tracking-error spectrum: a model-independent check of how a tune actually flew.

For the acro (non-chirp) parts of a log, the spectrum of (setpoint - gyro) shows where the
loop leaves error: a bump at 30-60 Hz is the sensitivity peak pilots feel as propwash wobble,
a raised 5-30 Hz band means weak disturbance rejection. Comparing logs of different tunes of
the same quad cross-checks the model and the pilot's impressions.
"""

from __future__ import annotations

import numpy as np

from ..flight import AXES, Flight, motor_saturated
from ..sysid.validate import freestyle_windows

BANDS = ((2, 5), (5, 10), (10, 20), (20, 30), (30, 45), (45, 60), (60, 100), (100, 200))


def error_spectrum(fl: Flight, n: int = 512, throttle: tuple[float, float] | None = None) -> dict:
    """Tracking-error PSD per band and axis, plus how hard the flight was flown (for fair comparisons)."""
    f = np.fft.rfftfreq(n, 1 / fl.fs)
    w = np.hanning(n)
    acc = {a: [] for a in range(3)}
    used = []
    for a0, b0 in freestyle_windows(fl, min_len_s=n / fl.fs):
        for s in range(a0, b0 - n, n // 2):
            if throttle is not None and not (throttle[0] <= np.mean(fl.throttle[s : s + n]) < throttle[1]):
                continue
            used.append(s)
            for a in range(3):
                e = fl.setpoint[s : s + n, a] - fl.gyro[s : s + n, a]
                acc[a].append(np.abs(np.fft.rfft((e - e.mean()) * w)) ** 2)
    out = {"windows": len(acc[0]), "bands": [f"{lo}-{hi}" for lo, hi in BANDS], "axes": {}}
    if used:
        idx = np.concatenate([np.arange(s, s + n) for s in used])
        hi = motor_saturated(fl)[idx]
        out["activity"] = {"stick_rms_deg_s": [round(float(np.sqrt(np.mean(fl.setpoint[idx, a] ** 2))), 0) for a in range(3)],
                           "throttle_p25_p50_p75": [round(float(x), 2) for x in np.percentile(fl.throttle[idx], [25, 50, 75])],
                           "motor_saturation_pct": round(float(100 * hi.mean()), 2)}
    for a in range(3):
        if not acc[a]:
            continue
        P = np.mean(acc[a], axis=0) * 2 / (fl.fs * np.sum(w**2))
        out["axes"][AXES[a]] = [round(float(10 * np.log10(np.mean(P[(f >= lo) & (f < hi)]) + 1e-12)), 1) for lo, hi in BANDS
                                if hi < fl.fs / 2]
    return out
