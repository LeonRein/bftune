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


def log_step_response(
    fl: Flight, axis: int, win_s: float = 2.0, resp_s: float = 0.5, min_sp_rms: float = 30.0,
    max_throttle: float | None = None,
) -> dict | None:
    n = int(win_s * fl.fs)
    m = int(resp_s * fl.fs)
    steps = []
    for a, b in freestyle_windows(fl, min_len_s=win_s):
        for s in range(a, b - n, n // 2):
            x = fl.setpoint[s : s + n, axis]
            y = fl.gyro_unfilt[s : s + n, axis]
            if np.std(x) < min_sp_rms:
                continue
            if max_throttle is not None and np.mean(fl.throttle[s : s + n]) > max_throttle:
                continue
            w = np.hanning(n)
            X = np.fft.rfft((x - x.mean()) * w, 2 * n)
            Y = np.fft.rfft((y - y.mean()) * w, 2 * n)
            eps = 1e-4 * np.max(np.abs(X) ** 2)
            h = np.fft.irfft(Y * np.conj(X) / (np.abs(X) ** 2 + eps), 2 * n)[:m]
            st = np.cumsum(h)
            if 0.5 < st[-m // 4 :].mean() < 1.5:
                steps.append(st)
    if len(steps) < 3:
        return None
    S = np.array(steps)
    t = np.arange(m) / fl.fs
    mean = S.mean(axis=0)
    ss = mean[int(0.6 * m) :].mean()
    i50 = int(np.argmax(mean >= 0.5 * ss))
    return {
        "t": t,
        "step": mean,
        "std": S.std(axis=0),
        "n": len(steps),
        "overshoot_pct": float(max(0.0, (mean.max() / ss - 1) * 100)),
        "delay_50_ms": float(t[i50] * 1000),
        "steady_state": float(ss),
    }
