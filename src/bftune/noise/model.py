"""Alias-aware gyro-noise model and noise-to-motor prediction.

Blackbox logs are plain every-Nth-sample snapshots of 8 kHz (or 4/3.2 kHz) signals with
no anti-alias filter. That destroys *where* high-frequency noise sits, but it preserves
*how much* there is: the variance of a decimated signal equals the variance of the
original. We exploit that:

1. For each throttle band, measure the in-flight noise spectra (above the flight-motion
   band) of three signals that see the same gyro noise through different, *known* filter
   chains: gyroUnfilt (none), gyroADC (gyro filters) and the D term (gyro + D filters +
   differentiator).
2. Model the true broadband gyro noise PSD at the loop rate as a smooth log-log spline
   on knots from 70 Hz to the loop Nyquist (motor lines excluded: the RPM filter tracks
   them exactly).
3. Fold the model through each chain and the decimation and fit the spline so the
   predicted aliased spectra match all three measured spectra at once (a regularized
   deconvolution: the three chains weight frequencies differently, which makes the
   true location of folded noise identifiable).
4. Predict noise at the motors (P+D path) for any candidate tune from the calibrated
   model. Tunes are compared by the *ratio* to the current tune, which cancels most
   modelling error.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from ..flight import Flight
from ..model.controller import OperatingPoint, controller_fr
from ..model.params import Tune
from ..sysid.chirp import find_chirps

MOTION_HZ = 70.0  # flight motion lives below this; noise estimated above it


@dataclass
class BandNoise:
    throttle: float  # centre (median) throttle
    motor_hz: list[float]
    n_windows: int
    f_obs: np.ndarray  # observed (aliased) frequency axis at log rate
    psd_unfilt: np.ndarray  # (3, F) per axis
    psd_filt: np.ndarray
    psd_d: np.ndarray
    var_unfilt: np.ndarray  # (3,) HF variance estimates (aliased band [MOTION_HZ, fN] rescaled)
    var_filt: np.ndarray
    var_d: np.ndarray
    line_free: np.ndarray | None = None  # bool mask over f_obs
    motor_hz_samples: np.ndarray | None = None  # (K, M) representative motor-speed sets in this band
    knots_hz: np.ndarray = field(default_factory=lambda: np.zeros(0))
    log_psd: np.ndarray = field(default_factory=lambda: np.zeros((3, 0)))  # (3, K) at knots
    calib_err: np.ndarray = field(default_factory=lambda: np.zeros(3))  # rms log error of the fit


@dataclass
class NoiseModel:
    bands: list[BandNoise]
    fs_log: float
    loop_hz: float
    dt: float
    time_scale: float
    notes: list[str] = field(default_factory=list)

    def true_psd(self, band: BandNoise, axis: int, f: np.ndarray) -> np.ndarray:
        """Modelled one-sided PSD of the broadband gyro noise at the loop rate, (deg/s)^2/Hz."""
        k = band.knots_hz
        lf = np.log(np.clip(f, k[0], k[-1]))
        return np.exp(np.interp(lf, np.log(k), band.log_psd[axis]))


def _welch_windows(x: np.ndarray, idx_starts: np.ndarray, n: int) -> np.ndarray:
    w = np.hanning(n)
    segs = np.stack([x[s : s + n] for s in idx_starts])
    segs = segs - segs.mean(axis=1, keepdims=True)
    X = np.fft.rfft(segs * w, axis=1)
    return (np.abs(X) ** 2).mean(axis=0) / (np.sum(w**2))  # PSD per bin (scaled below)


def _hf_var(f: np.ndarray, psd_bin: np.ndarray, fs: float) -> float:
    """Variance above MOTION_HZ, rescaled to the full [0, fN] band (aliased noise is ~white)."""
    fN = fs / 2
    m = f >= MOTION_HZ
    df = f[1] - f[0]
    v_band = np.sum(psd_bin[m]) * df
    return float(v_band * fN / (fN - MOTION_HZ))


def measure(fl: Flight, n: int = 256, n_bands: int = 5, min_windows: int = 12) -> list[BandNoise]:
    """Measure noise spectra per throttle band on armed, non-chirp data."""
    ok = fl.mode(0).copy()
    for r in find_chirps(fl):
        ok[r.start : r.end] = False
    starts = np.arange(0, fl.n - n, n // 2)
    starts = starts[[ok[s : s + n].all() for s in starts]]
    if len(starts) == 0:
        return []
    thr = np.array([fl.throttle[s : s + n].mean() for s in starts])
    thr_sd = np.array([fl.throttle[s : s + n].std() for s in starts])
    starts, thr = starts[thr_sd < 0.05], thr[thr_sd < 0.05]
    edges = np.unique(np.quantile(thr, np.linspace(0, 1, n_bands + 1)))
    f = np.fft.rfftfreq(n, 1 / fl.fs)
    bands = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = starts[(thr >= lo) & (thr <= hi)]
        if len(sel) < min_windows:
            continue
        # PSD scaling: |X|^2/(fs*sum w^2) *2 (one-sided)
        scale = 2.0 / fl.fs
        pu = np.stack([_welch_windows(fl.gyro_unfilt[:, a], sel, n) for a in range(3)]) * scale
        pf = np.stack([_welch_windows(fl.gyro[:, a], sel, n) for a in range(3)]) * scale
        pd = np.stack([_welch_windows(fl.D[:, a], sel, n) for a in range(3)]) * scale
        idx = np.concatenate([np.arange(s, s + n) for s in sel])
        mhz = np.median(fl.motor_hz[idx], axis=0).tolist() if fl.motor_hz is not None else [0.0] * 4
        line_free = _line_free_mask(f, fl.fs, fl.motor_hz[idx] if fl.motor_hz is not None else None)
        mh_samples = None
        if fl.motor_hz is not None:
            rng = np.random.default_rng(0)
            mh_samples = fl.motor_hz[rng.choice(idx, size=min(8, len(idx)), replace=False)]
        bands.append(
            BandNoise(
                throttle=float(np.median(fl.throttle[idx])),
                motor_hz=mhz,
                n_windows=len(sel),
                f_obs=f,
                psd_unfilt=pu,
                psd_filt=pf,
                psd_d=pd,
                var_unfilt=np.array([_hf_var(f, pu[a], fl.fs) for a in range(3)]),
                var_filt=np.array([_hf_var(f, pf[a], fl.fs) for a in range(3)]),
                var_d=np.array([_hf_var(f, pd[a], fl.fs) for a in range(3)]),
                line_free=line_free,
                motor_hz_samples=mh_samples,
            )
        )
    return bands


def _line_free_mask(f: np.ndarray, fs: float, motor_hz: np.ndarray | None, harmonics: int = 3,
                    guard_hz: float = 6.0, min_bins: int = 12) -> np.ndarray:
    """Bins of the aliased log spectrum not hit by motor harmonics (incl. aliases).

    Uses the 10-90 % motor-speed range; drops the highest harmonics first if the mask
    would leave fewer than `min_bins` usable bins.
    """
    ok_all = np.ones_like(f, dtype=bool)
    if motor_hz is None:
        return ok_all
    lo, hi = np.percentile(motor_hz, [10, 90])
    for nh in range(harmonics, 0, -1):
        ok = ok_all.copy()
        for h in range(1, nh + 1):
            for fm in np.linspace(h * lo, h * hi, 60):
                fa = abs(((fm + fs / 2) % fs) - fs / 2)
                ok &= np.abs(f - fa) > guard_hz
        if np.sum(ok & (f >= MOTION_HZ)) >= min_bins:
            return ok
    return ok_all


def _fold_to_bins(f_loop: np.ndarray, psd: np.ndarray, df_loop: float, fs_log: float, f_obs: np.ndarray) -> np.ndarray:
    """Aliased one-sided PSD at the log's bin centres from a loop-rate one-sided PSD."""
    fa = np.abs(((f_loop + fs_log / 2) % fs_log) - fs_log / 2)
    dfo = f_obs[1] - f_obs[0]
    idx = np.clip(np.rint(fa / dfo).astype(int), 0, len(f_obs) - 1)
    return np.bincount(idx, weights=psd * df_loop, minlength=len(f_obs)) / dfo


def chain_power(nm_or_dt, tune: Tune, axis: int, band: BandNoise, f: np.ndarray, loop_hz: float,
                time_scale: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """|Fg|^2, |Fg*D|^2, |Cy|^2 averaged over the band's motor-speed distribution.

    Averaging the power response over real motor speeds reproduces how the RPM notches
    act on a time-averaged spectrum (the notches move with each motor's speed).
    """
    dt = nm_or_dt.dt if hasattr(nm_or_dt, "dt") else nm_or_dt
    samples = band.motor_hz_samples if band.motor_hz_samples is not None else np.array([band.motor_hz])
    g2 = np.zeros_like(f)
    d2 = np.zeros_like(f)
    c2 = np.zeros_like(f)
    for mh in samples:
        op = OperatingPoint(throttle=band.throttle, motor_hz=list(mh), d_boost=0.0)
        C = controller_fr(tune, axis, op, f, dt, loop_hz, time_scale=time_scale)
        g2 += np.abs(C.Fg) ** 2
        d2 += np.abs(C.Fg * C.D) ** 2
        c2 += np.abs(C.Cy) ** 2
    k = len(samples)
    return g2 / k, d2 / k, c2 / k


def build(fl: Flight, tune: Tune, time_scale: float = 1.0, n_bands: int = 5, smooth: float = 0.3) -> NoiseModel:
    bands = measure(fl, n_bands=n_bands)
    nm = NoiseModel(bands=bands, fs_log=fl.fs, loop_hz=fl.loop_hz, dt=1 / fl.loop_hz, time_scale=time_scale)
    if not bands:
        nm.notes.append("not enough steady armed data to build a noise model")
        return nm
    f_nyq = 0.5 * fl.loop_hz / time_scale
    knots = np.geomspace(MOTION_HZ, f_nyq, 12)
    f = np.linspace(MOTION_HZ, f_nyq, 8000)
    df = f[1] - f[0]
    lk = np.log(knots)
    lf = np.log(f)
    for b in bands:
        b.knots_hz = knots
        b.log_psd = np.zeros((3, len(knots)))
        m = b.f_obs >= MOTION_HZ
        for axis in range(3):
            g2, d2, _ = chain_power(nm, tune, axis, b, f, fl.loop_hz, time_scale)
            # gyroUnfilt is dominated by motor lines that the RPM filter removes, so the fit uses
            # what passes the current filters: filtered gyro and (if D is active) the D term.
            chains = [(g2, b.psd_filt[axis])]
            if tune.kd(axis) > 0 and b.var_d[axis] > 0:
                chains.append((d2, b.psd_d[axis]))
            obs = [np.log(np.maximum(o[m], 1e-14)) for _, o in chains]
            # gyroUnfilt contains the motor lines on top of the broadband floor, so it is an
            # *upper bound* for the folded broadband model (one-sided penalty).
            ub = np.log(np.maximum(b.psd_unfilt[axis][m], 1e-14))
            # basis: S(f) = exp(interp(log f; knots, x))
            W = np.zeros((len(f), len(knots)))
            j = np.clip(np.searchsorted(lk, lf) - 1, 0, len(knots) - 2)
            t = (lf - lk[j]) / (lk[j + 1] - lk[j])
            W[np.arange(len(f)), j] = 1 - t
            W[np.arange(len(f)), j + 1] = t

            def loss(x, chains=chains, obs=obs, W=W, ub=ub):
                S = np.exp(W @ x)
                e = 0.0
                for (h2, _), o in zip(chains, obs):
                    pred = _fold_to_bins(f, S * h2, df, fl.fs, b.f_obs)[m]
                    e += np.mean((np.log(np.maximum(pred, 1e-14)) - o) ** 2)
                pu = np.log(np.maximum(_fold_to_bins(f, S, df, fl.fs, b.f_obs)[m], 1e-14))
                over = np.maximum(pu - ub, 0.0)
                return e / len(chains) + 4.0 * np.mean(over**2) + smooth * np.mean(np.diff(x, 2) ** 2)

            x0 = np.full(len(knots), float(np.median(obs[0])))
            r = minimize(loss, x0, method="L-BFGS-B", options={"maxiter": 3000})
            b.log_psd[axis] = r.x
            b.calib_err[axis] = float(np.sqrt(max(r.fun, 0)))
    return nm


@dataclass
class NoisePrediction:
    throttle: list[float]
    motor_rms: np.ndarray  # (bands, 3) predicted HF pidSum noise RMS (P + D path)
    d_rms: np.ndarray  # (bands, 3)
    gyro_rms: np.ndarray  # (bands, 3) filtered gyro HF noise RMS


def predict(nm: NoiseModel, tune: Tune, f_lo: float = MOTION_HZ) -> NoisePrediction:
    f = np.linspace(f_lo, 0.5 * nm.loop_hz / nm.time_scale, 6000)
    df = f[1] - f[0]
    mot = np.zeros((len(nm.bands), 3))
    d = np.zeros_like(mot)
    g = np.zeros_like(mot)
    for i, b in enumerate(nm.bands):
        for axis in range(3):
            g2, d2, c2 = chain_power(nm, tune, axis, b, f, nm.loop_hz, nm.time_scale)
            S = nm.true_psd(b, axis, f)
            mot[i, axis] = np.sqrt(np.sum(S * c2) * df)
            d[i, axis] = np.sqrt(np.sum(S * d2) * df)
            g[i, axis] = np.sqrt(np.sum(S * g2) * df)
    return NoisePrediction([b.throttle for b in nm.bands], mot, d, g)
