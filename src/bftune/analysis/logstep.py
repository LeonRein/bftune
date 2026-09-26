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
    # the peak of the response itself (within 4 x the 50 % time + 10 ms): on slow quads a later I-term hump would
    # otherwise take over the maximum and make the number jump between candidates
    k_end = max(int(np.searchsorted(t, 4 * t50 + 0.01)), i + 2)
    k = int(np.argmax(yn[:k_end]))
    end = int(np.searchsorted(t, t[k] + 0.1))
    dip = float(np.min(yn[k:max(end, k + 1)]))
    return {"delay_50_ms": float(t50 * 1000), "overshoot_pct": float(max(0.0, (yn[k] - 1) * 100)),
            "undershoot_pct": float(max(0.0, (1 - dip) * 100)) if yn[k] > 1 else 0.0}


def step_windows(fl: Flight, axis: int, win_s: float = 2.0, min_sp_rms: float = 30.0,
                 max_throttle: float | None = None) -> list[int]:
    """Start indices of the analysis windows: acro, no chirp, enough stick activity on this axis."""
    n = int(win_s * fl.fs)
    out = []
    for a, b in freestyle_windows(fl, min_len_s=win_s):
        for s in range(a, b - n, n // 2):
            if np.std(fl.setpoint[s : s + n, axis]) < min_sp_rms:
                continue
            if max_throttle is not None and np.mean(fl.throttle[s : s + n]) > max_throttle:
                continue
            out.append(int(s))
    return out


def step_estimate(x: np.ndarray, y: np.ndarray, starts: list[int], fs: float, win_s: float = 2.0,
                  resp_s: float = 0.5, fixed: bool = False) -> dict | None:
    """Wiener deconvolution of setpoint x -> gyro y over the given windows, integrated to a normalised step.
    `fixed=True` keeps every given window (no plausibility filter): for comparing candidates on the exact windows
    the measurement accepted (`used` in the result), so the set can't change between candidates."""
    n, m = int(win_s * fs), int(resp_s * fs)
    w = np.hanning(n)
    steps, used = [], []
    for s in starts:
        xs, ys = x[s : s + n], y[s : s + n]
        if len(xs) < n:
            continue
        X = np.fft.rfft((xs - xs.mean()) * w, 2 * n)
        Y = np.fft.rfft((ys - ys.mean()) * w, 2 * n)
        eps = EPS * np.max(np.abs(X) ** 2)
        st = np.cumsum(np.fft.irfft(Y * np.conj(X) / (np.abs(X) ** 2 + eps), 2 * n)[:m])
        # the settled level is biased low (little setpoint power near DC after windowing), so only the shape
        # and timing are meaningful: normalise each window by its own settled level, drop implausible ones
        level = st[-m // 4 :].mean()
        if (fixed and abs(level) > 1e-3) or 0.3 < level < 1.5:
            steps.append(st / level)
            used.append(int(s))
    if len(steps) < 3:
        return None
    S = np.array(steps)
    t = np.arange(m) / fs
    mean = S.mean(axis=0)
    ss = mean[int(0.6 * m) :].mean()
    # standard error of the mean response over the first 150 ms: how far to trust delay and peak
    se = float(S.std(axis=0)[: int(0.15 * fs)].mean() / np.sqrt(len(steps)))
    return {
        "t": t,
        "step": mean,
        "std": S.std(axis=0),
        "n": len(steps),
        "used": used,
        **shape_metrics(t, mean, ss),
        "steady_state": float(ss),  # ~1 by construction (normalised)
        "se": se,
        "confidence": "good" if se < 0.05 else ("fair" if se < 0.1 else "low"),
    }


def log_step_response(
    fl: Flight, axis: int, win_s: float = 2.0, resp_s: float = 0.5, min_sp_rms: float = 30.0,
    max_throttle: float | None = None, gyro: np.ndarray | None = None,
) -> dict | None:
    """`gyro` (N,) replaces the logged gyro of this axis, e.g. the model's answer to the logged setpoint: the same
    windows and estimator then give a measured-vs-model comparison free of estimator bias."""
    starts = step_windows(fl, axis, win_s, min_sp_rms, max_throttle)
    y = fl.gyro_unfilt[:, axis] if gyro is None else gyro
    return step_estimate(fl.setpoint[:, axis], y, starts, fl.fs, win_s, resp_s)
