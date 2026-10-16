"""Motor dynamics from bidirectional-DShot telemetry: command -> mechanical rpm.

For each motor the small-signal response of rotor speed to the DShot command is well
described by a first-order lag plus a transport delay,

    omega(s)/cmd(s) = g e^{-s Td} / (tau s + 1),

where the lag is fitted on the DShot duty times the battery voltage (relative to its median): the
ESC applies duty x Vbat to the motor, and the sag during punch-outs otherwise reads as a long lag
(46-57 ms instead of ~25 ms at full throttle on a 5"). The gain stays per unit duty at the voltage
flown at that speed (sag included), as the throttle cases use it.

and the time constant falls with speed (linearized motor + prop load):
1/tau = c0 + c1*omega. Per speed bin the model is fitted to the measured frequency response
command -> eRPM (Welch, 3-60 Hz; command and eRPM are sampled in the same PID iteration), then c0, c1.
Above the corner the response is g/(tau s): torque per volt over inertia, the same at every speed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.optimize import least_squares

from ..flight import Flight
from ..gaps import segments as gap_free_segments


@dataclass
class MotorModel:
    bins_hz: np.ndarray  # centre motor speed per bin [Hz]
    tau: np.ndarray  # time constant per bin [s]
    gain: np.ndarray  # d(motor Hz)/d(normalized command) per bin
    delay: float  # common transport delay [s] (telemetry + ESC)
    n: np.ndarray  # samples per bin
    c0: float  # 1/tau = c0 + c1*hz
    c1: float
    r2: np.ndarray  # mean coherence of the fitted band per bin

    g0: float = 0.0  # gain(hz) = g0 + g1*hz  [motor Hz per unit command]
    g1: float = 0.0

    def _clamp(self, hz):
        # never extrapolate the fitted lines beyond the speeds actually flown
        return np.clip(np.asarray(hz, dtype=float), self.bins_hz.min(), self.bins_hz.max())

    def tau_at(self, hz: float | np.ndarray) -> np.ndarray:
        """Piecewise-linear in 1/tau between the measured speed bins (flat outside them).

        The global fit 1/tau = c0 + c1*hz is kept for reporting, but the bins are used directly:
        at low rpm the lag grows faster than the linear fit suggests (idle/propwash matter)."""
        return 1.0 / np.interp(self._clamp(hz), self.bins_hz, 1.0 / self.tau)

    def gain_at(self, hz: float | np.ndarray) -> np.ndarray:
        return np.interp(self._clamp(hz), self.bins_hz, self.gain)

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


def _bin_frf(us: list, ys: list, nfft: int, fs: float):
    """Welch H1 = S_uy/S_uu and coherence over windows that lie wholly in the bin (command is exact: H1 is unbiased)."""
    w = np.hanning(nfft)
    suu = suy = syy = 0.0
    for u, y in zip(us, ys):
        if len(y) < nfft:
            continue
        idx = np.arange(0, len(y) - nfft + 1, nfft // 4)[:, None] + np.arange(nfft)
        U = np.fft.rfft((u[idx] - u[idx].mean(axis=1, keepdims=True)) * w, axis=1)
        Y = np.fft.rfft((y[idx] - y[idx].mean(axis=1, keepdims=True)) * w, axis=1)
        suu, suy, syy = suu + np.sum(np.abs(U) ** 2, 0), suy + np.sum(np.conj(U) * Y, 0), syy + np.sum(np.abs(Y) ** 2, 0)
    if np.isscalar(suu):
        return None
    return np.fft.rfftfreq(nfft, 1 / fs), suy / np.maximum(suu, 1e-12), np.abs(suy) ** 2 / np.maximum(suu * syy, 1e-12)


def _fit_lag(f: np.ndarray, H: np.ndarray, coh: np.ndarray) -> tuple[float, float, float]:
    """g e^{-s Td}/(tau s + 1) fitted to the FRF (relative complex error, weighted by coherence) -> (g, tau, Td)."""
    s, w = 2j * np.pi * f, np.sqrt(coh)

    def res(p):
        e = (p[0] * np.exp(-s * p[2]) / (p[1] * s + 1) - H) / np.abs(H) * w
        return np.concatenate([e.real, e.imag])

    g0 = float(np.abs(H[0]))
    return tuple(least_squares(res, [g0, 0.02, 0.001], bounds=([0, 1e-3, 0], [np.inf, 0.3, 0.02])).x)


def identify_motors(fl: Flight, n_bins: int = 8, min_samples: int = 3000, bin_by_s: float = 0.2,
                    max_rise: float = 1.25, f_band: tuple = (3.0, 60.0)) -> MotorModel | None:
    if fl.motor_hz is None or fl.motor.shape[1] == 0:
        return None
    armed = fl.mode(0)
    hz_all = fl.motor_hz
    lo, hi = np.percentile(hz_all[armed], [2, 99.5])
    v_med = float(np.median(fl.vbat[armed]))
    volts = fl.vbat / v_med if v_med > 0 else np.ones(fl.n)  # applied voltage = duty x Vbat (see module doc)
    edges = np.geomspace(max(lo, 30.0), hi, n_bins + 1)
    # contiguous armed runs without blackbox gaps; per run and motor: (command, rpm, local mean rpm). Samples are
    # binned by the local mean speed, not by their own rpm: selecting by the fitted output truncates its fluctuations
    # and biases the lag short (narrow bins on a hover log gave 5-15 ms instead of ~25 ms)
    chunks = [(fl.motor[a:b, m] * volts[a:b], hz_all[a:b, m], uniform_filter1d(hz_all[a:b, m], max(1, int(bin_by_s * fl.fs))),
               volts[a:b]) for a, b in gap_free_segments(fl, armed, 201) for m in range(hz_all.shape[1])]

    # the lag is read where it matters for the rate loop (a few Hz to past the crossover), from windows of ~0.5 s;
    # an equation-error ARX(1) at the log rate was dominated by the high frequencies and missed d(rpm)/d(cmd) above
    # the motor corner, which is set by the motor alone (torque per volt / inertia), by up to 2x per bin
    nfft = int(2 ** np.round(np.log2(0.5 * fl.fs)))
    centers, taus, gains, ns, r2s, delays = [], [], [], [], [], []
    for k in range(n_bins):
        us, ys, vs = [], [], []
        for u, y, ym, v in chunks:
            inb = (ym >= edges[k]) & (ym < edges[k + 1])
            # split into contiguous in-bin chunks
            cut = np.flatnonzero(np.diff(np.concatenate([[0], inb.astype(int), [0]])))
            for s, e in zip(cut[::2], cut[1::2]):
                if e - s >= nfft:
                    us.append(u[s:e])
                    ys.append(y[s:e])
                    vs.append(v[s:e])
        n = sum(len(y) for y in ys)
        if n < min_samples or (frf := _bin_frf(us, ys, nfft, fl.fs)) is None:
            continue
        f, H, coh = frf
        m = (f >= f_band[0]) & (f <= min(f_band[1], 0.25 * fl.fs))
        g, tau, td = _fit_lag(f[m], H[m], coh[m])
        gain = g * float(np.mean(np.concatenate(vs)))  # per unit duty at this speed's voltage
        centers.append(np.sqrt(edges[k] * edges[k + 1]))
        taus.append(tau)
        gains.append(gain)
        ns.append(n)
        r2s.append(float(np.mean(coh[m])))
        delays.append(td)
    # physically the lag shortens with speed: a bin above the best-sampled one with a clearly longer lag comes from
    # large-signal data (punch-outs: current limit, sag) or disturbance-dominated airflow, not the small-signal lag
    if centers:
        ref = taus[int(np.argmax(ns))]
        keep = [i for i in range(len(centers)) if centers[i] <= centers[int(np.argmax(ns))] or taus[i] <= max_rise * ref]
        centers, taus, gains, ns, r2s, delays = ([v[i] for i in keep] for v in (centers, taus, gains, ns, r2s, delays))
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
        delay=float(np.median(delays)),
        n=np.array(ns),
        c0=float(c0),
        c1=float(c1),
        r2=np.array(r2s),
    )
