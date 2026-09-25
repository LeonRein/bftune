"""Closed-loop frequency-response estimation with the chirp excitation as instrument.

Under feedback, H1 = S_uy/S_uu is biased wherever noise dominates (it tends to -1/C).
With an external instrument z (the injected chirp, uncorrelated with all noise),

    G_hat(f) = S_zy(f) / S_zu(f)

is consistent for the u->y path regardless of the controller. We accumulate the raw
cross-spectra of each run over log-spaced frequency bands (a chirp visits each
frequency only briefly, so band-summing is the natural smoother) and over repeated
runs of the same axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..flight import Flight
from .chirp import ChirpRun, chirp_frequency, excitation


@dataclass
class BandFRF:
    f: np.ndarray  # band centre frequencies [Hz]
    H: dict[str, np.ndarray]  # name -> complex response relative to the instrument-referenced input
    coh: dict[str, np.ndarray]  # name -> coherence of instrument with that signal
    valid_frac: np.ndarray  # fraction of the band's chirp time free of saturation
    n_runs: int
    fs: float
    extras: dict = field(default_factory=dict)


def saturation_mask(fl: Flight, sl: slice) -> np.ndarray:
    """True where the actuator path is linear (no motor at its limit, pidsum not clipped)."""
    mr = fl.motor_raw[sl]
    lo, hi = (fl.log.header_ints("motorOutput") or [0, 2047])[:2]
    ok = np.all((mr > lo + 1) & (mr < hi - 1), axis=1) if mr.size else np.ones(sl.stop - sl.start, bool)
    lim = np.array([fl.cfg.int("pidsum_limit", 500), fl.cfg.int("pidsum_limit", 500), fl.cfg.int("pidsum_limit_yaw", 400)])
    ok &= np.all(np.abs(fl.pidsum[sl]) < 0.98 * lim, axis=1)
    return ok


def band_edges(f_lo: float, f_hi: float, per_octave: int = 8) -> np.ndarray:
    n = int(np.ceil(np.log2(f_hi / f_lo) * per_octave))
    return np.geomspace(f_lo, f_hi, n + 1)


def estimate(
    fl: Flight,
    runs: list[ChirpRun],
    signals: dict[str, np.ndarray],
    reference: str,
    edges: np.ndarray | None = None,
    taper: float = 0.02,
) -> BandFRF:
    """Estimate responses of every signal relative to `reference` using the chirp instrument.

    signals: name -> (N,) array over the full flight (already selected for the axis).
    Response H[name] = S_z,name / S_z,reference.  Coherence is |sum S_zx|^2 / (sum S_zz sum S_xx).
    """
    if edges is None:
        f_hi = min(max(r.f_end for r in runs), 0.45 * fl.fs)
        edges = band_edges(max(min(r.f_start for r in runs), 0.5), f_hi)
    nb = len(edges) - 1
    Szx = {k: np.zeros(nb, complex) for k in signals}
    Sxx = {k: np.zeros(nb) for k in signals}
    Szz = np.zeros(nb)
    sat_t = np.zeros(nb)
    tot_t = np.zeros(nb)
    for run in runs:
        sl = slice(run.start, run.end)
        z = excitation(fl, run)
        n = len(z)
        w = _tukey(n, taper)
        Z = np.fft.rfft((z - z.mean()) * w)
        fr = np.fft.rfftfreq(n, 1.0 / fl.fs)
        band = np.digitize(fr, edges) - 1
        inb = (band >= 0) & (band < nb)
        Zb = Z[inb]
        bi = band[inb]
        Szz += np.bincount(bi, weights=np.abs(Zb) ** 2, minlength=nb)
        for k, sig in signals.items():
            x = sig[sl].astype(float)
            X = np.fft.rfft((x - x.mean()) * w)[inb]
            c = X * np.conj(Zb)
            Szx[k] += np.bincount(bi, weights=c.real, minlength=nb) + 1j * np.bincount(bi, weights=c.imag, minlength=nb)
            Sxx[k] += np.bincount(bi, weights=np.abs(X) ** 2, minlength=nb)
        # saturation bookkeeping in the chirp's time-frequency map (use log-rate time -> frequency)
        finst = chirp_frequency(fl, run) * (fl.fs * fl.log_ratio / fl.loop_hz)
        ok = saturation_mask(fl, sl)
        b2 = np.digitize(finst, edges) - 1
        m2 = (b2 >= 0) & (b2 < nb)
        tot_t += np.bincount(b2[m2], minlength=nb)
        sat_t += np.bincount(b2[m2], weights=(~ok[m2]).astype(float), minlength=nb)
    ref = Szx[reference]
    H = {k: v / np.where(np.abs(ref) > 0, ref, np.nan) for k, v in Szx.items()}
    coh = {k: np.abs(Szx[k]) ** 2 / np.maximum(Szz * Sxx[k], 1e-30) for k in signals}
    fc = np.sqrt(edges[:-1] * edges[1:])
    valid = 1.0 - sat_t / np.maximum(tot_t, 1)
    keep = Szz > 0
    return BandFRF(
        f=fc[keep],
        H={k: v[keep] for k, v in H.items()},
        coh={k: v[keep] for k, v in coh.items()},
        valid_frac=valid[keep],
        n_runs=len(runs),
        fs=fl.fs,
        extras={"Szz": Szz[keep], "edges": edges},
    )


def _tukey(n: int, alpha: float) -> np.ndarray:
    if alpha <= 0:
        return np.ones(n)
    w = np.ones(n)
    k = int(alpha * (n - 1) / 2)
    if k > 0:
        x = np.arange(k) / k
        w[:k] = 0.5 * (1 - np.cos(np.pi * x))
        w[-k:] = w[:k][::-1]
    return w


def plant_frf(fl: Flight, runs: list[ChirpRun], axis: int, edges: np.ndarray | None = None) -> BandFRF:
    """Plant G = gyroUnfilt / pidSum and companion responses for chain checks.

    Responses returned (all relative to pidSum 'u'):
      y   : gyroUnfilt           -> G
      yf  : gyroADC (filtered)   -> G * Fg
      P, D: logged P and D terms -> G * Fg * (Kp) and G * Fg * Fd * Kd*TPA*diff
      r   : setpoint             -> G / T_ry
    """
    sel = [r for r in runs if r.axis == axis]
    sig = {
        "u": fl.pidsum[:, axis],
        "y": fl.gyro_unfilt[:, axis],
        "yf": fl.gyro[:, axis],
        "P": fl.P[:, axis],
        "D": fl.D[:, axis],
        "I": fl.I[:, axis],
        "r": fl.setpoint[:, axis],
    }
    return estimate(fl, sel, sig, reference="u", edges=edges)
