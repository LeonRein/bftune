"""Motor dynamics from bidirectional-DShot telemetry: command -> mechanical rpm.

For each motor the small-signal response of rotor speed to the DShot command is well
described by a first-order lag plus a transport delay,

    omega(s)/cmd(s) = g e^{-s Td} / (tau s + 1),

and the time constant falls with speed (linearized motor + prop load):
1/tau = c0 + c1*omega. We fit an ARX(1) model per speed bin on the logged 1 kHz
data (command and eRPM are sampled in the same PID iteration), then fit c0, c1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..flight import Flight


@dataclass
class MotorModel:
    bins_hz: np.ndarray  # centre motor speed per bin [Hz]
    tau: np.ndarray  # time constant per bin [s]
    gain: np.ndarray  # d(motor Hz)/d(normalized command) per bin
    delay: float  # common transport delay [s] (telemetry + ESC)
    n: np.ndarray  # samples per bin
    c0: float  # 1/tau = c0 + c1*hz
    c1: float
    r2: np.ndarray

    g0: float = 0.0  # gain(hz) = g0 + g1*hz  [motor Hz per unit command]
    g1: float = 0.0

    def _clamp(self, hz):
        # never extrapolate the fitted lines beyond the speeds actually flown
        return np.clip(np.asarray(hz, dtype=float), self.bins_hz.min(), self.bins_hz.max())

    def tau_at(self, hz: float | np.ndarray) -> np.ndarray:
        return 1.0 / (self.c0 + self.c1 * self._clamp(hz))

    def gain_at(self, hz: float | np.ndarray) -> np.ndarray:
        g = self.g0 + self.g1 * self._clamp(hz)
        return np.maximum(g, 0.2 * float(np.min(self.gain)))

    def authority_ratio(self, hz: float, ref_hz: float) -> float:
        """Relative torque authority d(thrust)/d(cmd) ∝ omega * domega/dcmd (thrust ∝ omega²)."""
        return float(hz * self.gain_at(hz) / (ref_hz * self.gain_at(ref_hz)))

    def covers(self, hz: float, margin: float = 0.15) -> bool:
        return self.bins_hz.min() * (1 - margin) <= hz <= self.bins_hz.max() * (1 + margin)

    def tau_ratio(self, hz: float, ref_hz: float) -> float:
        return float(self.tau_at(hz) / self.tau_at(ref_hz))

    def summary(self) -> str:
        rows = [f"{b:7.0f} Hz  tau={t*1000:6.1f} ms  gain={g:7.0f} Hz/1  n={n}" for b, t, g, n in zip(self.bins_hz, self.tau, self.gain, self.n)]
        return "\n".join(rows)


def _arx_fit(cmd: np.ndarray, hz: np.ndarray, d: int):
    """y[k] = a y[k-1] + b u[k-1-d] + c ; segments given as list of (u, y) arrays."""
    X, Y = [], []
    for u, y in zip(cmd, hz):
        if len(y) < d + 5:
            continue
        m = len(y) - d - 1
        X.append(np.column_stack([y[d : d + m], u[:m], np.ones(m)]))
        Y.append(y[d + 1 :])
    if not X:
        return None
    X = np.vstack(X)
    Y = np.concatenate(Y)
    coef, res, *_ = np.linalg.lstsq(X, Y, rcond=None)
    pred = X @ coef
    r2 = 1 - np.sum((Y - pred) ** 2) / max(np.sum((Y - Y.mean()) ** 2), 1e-12)
    return coef, r2, len(Y)


def identify_motors(fl: Flight, n_bins: int = 8, max_delay_samples: int = 4, min_samples: int = 3000) -> MotorModel | None:
    if fl.motor_hz is None or fl.motor.shape[1] == 0:
        return None
    ts = 1.0 / fl.fs
    armed = fl.mode(0)
    hz_all = fl.motor_hz
    lo, hi = np.percentile(hz_all[armed], [2, 99.5])
    edges = np.geomspace(max(lo, 30.0), hi, n_bins + 1)
    # contiguous armed runs
    idx = np.flatnonzero(np.diff(np.concatenate([[0], armed.astype(int), [0]])))
    segments = [(a, b) for a, b in zip(idx[::2], idx[1::2]) if b - a > 200]

    # pick the transport delay that maximizes fit over all data
    best_d, best_r2 = 0, -np.inf
    for d in range(max_delay_samples + 1):
        us, ys = [], []
        for a, b in segments:
            for m in range(hz_all.shape[1]):
                us.append(fl.motor[a:b, m])
                ys.append(hz_all[a:b, m])
        out = _arx_fit(us, ys, d)
        if out and out[1] > best_r2:
            best_d, best_r2 = d, out[1]

    centers, taus, gains, ns, r2s = [], [], [], [], []
    for k in range(n_bins):
        us, ys = [], []
        for a, b in segments:
            for m in range(hz_all.shape[1]):
                y = hz_all[a:b, m]
                u = fl.motor[a:b, m]
                inb = (y >= edges[k]) & (y < edges[k + 1])
                # split into contiguous in-bin chunks
                cut = np.flatnonzero(np.diff(np.concatenate([[0], inb.astype(int), [0]])))
                for s, e in zip(cut[::2], cut[1::2]):
                    if e - s > 20:
                        us.append(u[s:e])
                        ys.append(y[s:e])
        n = sum(len(y) for y in ys)
        if n < min_samples:
            continue
        out = _arx_fit(us, ys, best_d)
        if out is None:
            continue
        (a_, b_, _c), r2, nn = out
        if not (0 < a_ < 1) or b_ <= 0:
            continue
        tau = -ts / np.log(a_)
        gain = b_ / (1 - a_)
        centers.append(np.sqrt(edges[k] * edges[k + 1]))
        taus.append(tau)
        gains.append(gain)
        ns.append(nn)
        r2s.append(r2)
    if len(centers) < 2:
        return None
    centers, taus = np.array(centers), np.array(taus)
    w = np.sqrt(np.array(ns, dtype=float))
    A = np.column_stack([np.ones_like(centers), centers])
    c0, c1 = np.linalg.lstsq(A * w[:, None], (1.0 / taus) * w, rcond=None)[0]
    lo_hz = float(centers.min())
    # physically 1/tau must stay positive and increase with speed over (and a bit beyond) the data;
    # otherwise fall back to a constant tau (e.g. narrow throttle range in the log)
    if c1 < 0 or c0 + c1 * 0.5 * lo_hz <= 0:
        c0, c1 = float(np.average(1.0 / taus, weights=w)), 0.0
    g1, g0 = np.polyfit(centers, np.array(gains), 1, w=w)
    return MotorModel(
        g0=float(g0),
        g1=float(g1),
        bins_hz=centers,
        tau=taus,
        gain=np.array(gains),
        delay=best_d * ts,
        n=np.array(ns),
        c0=float(c0),
        c1=float(c1),
        r2=np.array(r2s),
    )
