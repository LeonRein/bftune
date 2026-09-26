"""Measured setpoint->gyro step response from ordinary flying (Wiener deconvolution).

Same idea as PIDtoolbox / PIDtoolbox-style "step response" plots: for overlapping windows
with enough stick activity, estimate the impulse response h from setpoint x to gyro y as
H = Y X* / (|X|^2 + eps), integrate to a step response, and average over windows.
This includes feedforward, iterm relax and all nonlinearities as flown.
"""

from __future__ import annotations

import numpy as np

from ..flight import Flight
from ..sysid.validate import freestyle_windows

# Wiener regularisation relative to the peak setpoint power. 1e-6 recovers a known pure delay to ~1 ms on real
# freestyle setpoints (1e-4 smears 5 ms into 14 ms); the price is ~5 % artefact overshoot: compare logs, do not
# read single-digit overshoot differences.
EPS = 1e-6


def shape_metrics(t: np.ndarray, y: np.ndarray, level: float = 1.0) -> dict:
    """50 % time (interpolated between samples), peak overshoot and the undershoot after the peak (the dip
    below the settled level that pilots see as a small bounce at the end of a flick), for a step normalised to `level`."""
    yn = y / level
    i = int(np.argmax(yn >= 0.5))
    t50 = t[i] if i == 0 else t[i - 1] + (0.5 - yn[i - 1]) / max(yn[i] - yn[i - 1], 1e-9) * (t[i] - t[i - 1])
    k = int(np.argmax(yn))
    end = int(np.searchsorted(t, t[k] + 0.1))
    dip = float(np.min(yn[k:max(end, k + 1)]))
    return {"delay_50_ms": float(t50 * 1000), "overshoot_pct": float(max(0.0, (yn[k] - 1) * 100)),
            "undershoot_pct": float(max(0.0, (1 - dip) * 100)) if yn[k] > 1 else 0.0}


def log_step_response(
    fl: Flight, axis: int, win_s: float = 2.0, resp_s: float = 0.5, min_sp_rms: float = 30.0,
    max_throttle: float | None = None, gyro: np.ndarray | None = None,
) -> dict | None:
    """`gyro` (N,) replaces the logged gyro of this axis, e.g. the model's answer to the logged setpoint: the same
    windows and estimator then give a measured-vs-model comparison free of estimator bias."""
    n = int(win_s * fl.fs)
    m = int(resp_s * fl.fs)
    steps = []
    for a, b in freestyle_windows(fl, min_len_s=win_s):
        for s in range(a, b - n, n // 2):
            x = fl.setpoint[s : s + n, axis]
            y = (fl.gyro_unfilt[:, axis] if gyro is None else gyro)[s : s + n]
            if np.std(x) < min_sp_rms:
                continue
            if max_throttle is not None and np.mean(fl.throttle[s : s + n]) > max_throttle:
                continue
            w = np.hanning(n)
            X = np.fft.rfft((x - x.mean()) * w, 2 * n)
            Y = np.fft.rfft((y - y.mean()) * w, 2 * n)
            eps = EPS * np.max(np.abs(X) ** 2)
            h = np.fft.irfft(Y * np.conj(X) / (np.abs(X) ** 2 + eps), 2 * n)[:m]
            st = np.cumsum(h)
            # the settled level is biased low (little setpoint power near DC after windowing), so only the shape
            # and timing are meaningful: normalise each window by its own settled level, drop implausible ones
            level = st[-m // 4 :].mean()
            if 0.3 < level < 1.5:
                steps.append(st / level)
    if len(steps) < 3:
        return None
    S = np.array(steps)
    t = np.arange(m) / fl.fs
    mean = S.mean(axis=0)
    ss = mean[int(0.6 * m) :].mean()
    # standard error of the mean response over the first 150 ms: how far to trust delay and peak
    se = float(S.std(axis=0)[: int(0.15 * fl.fs)].mean() / np.sqrt(len(steps)))
    return {
        "t": t,
        "step": mean,
        "std": S.std(axis=0),
        "n": len(steps),
        **shape_metrics(t, mean, ss),
        "steady_state": float(ss),  # ~1 by construction (normalised)
        "se": se,
        "confidence": "good" if se < 0.05 else ("fair" if se < 0.1 else "low"),
    }
