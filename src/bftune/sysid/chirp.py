"""Locate Betaflight CHIRP excitation runs in a flight (debug_mode = CHIRP, 2026.x).

DEBUG_CHIRP (pid.c): debug[0] = wrapped phase*5000, debug[1] = axis (-1 when the
chirp switch is off), debug[2] = instantaneous frequency*10, debug[3] = cos(phase)*1000
(the raw excitation *before* the lead-lag shaping filter and amplitude).
The excitation is added to the rate setpoint of `axis`, i.e. closed-loop excitation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..flight import BOX_ANGLE, BOX_HORIZON, Flight

DEBUG_CHIRP = 96  # debug_mode index in 2026.6 (see settings DB)


@dataclass
class ChirpRun:
    axis: int
    start: int  # sample index (inclusive)
    end: int  # sample index (exclusive)
    f_start: float
    f_end: float
    throttle: float  # median mixer throttle
    throttle_spread: float  # IQR
    vbat: float
    level_mode: bool  # ANGLE/HORIZON active (outer attitude loop present)
    complete: bool  # swept to the configured end frequency
    z: np.ndarray | None = None  # reconstructed excitation (when debug_mode != CHIRP)
    f_inst: np.ndarray | None = None  # reconstructed instantaneous frequency
    reconstructed: bool = False

    @property
    def n(self) -> int:
        return self.end - self.start


def find_chirps(fl: Flight, min_duration_s: float = 3.0) -> list[ChirpRun]:
    if fl.debug_mode != DEBUG_CHIRP:
        return reconstruct_chirps(fl, min_duration_s)
    ax = fl.debug[:, 1].astype(int)
    fr = fl.debug[:, 2] / 10.0
    active = (ax >= 0) & (fr > 0)
    runs: list[ChirpRun] = []
    idx = np.flatnonzero(np.diff(np.concatenate([[0], active.astype(int), [0]])))
    f_end_cfg = fl.log.header_int("chirp_frequency_end_deci_hz", 6000) / 10.0
    for a, b in zip(idx[::2], idx[1::2]):
        # split on axis changes inside an active block
        cuts = [a, *(a + 1 + np.flatnonzero(np.diff(ax[a:b]) != 0)), b]
        for s, e in zip(cuts[:-1], cuts[1:]):
            if (fl.t[e - 1] - fl.t[s]) < min_duration_s:
                continue
            thr = fl.throttle[s:e]
            q1, q3 = np.percentile(thr, [25, 75])
            runs.append(
                ChirpRun(
                    axis=int(ax[s]),
                    start=int(s),
                    end=int(e),
                    f_start=float(fr[s]),
                    f_end=float(fr[e - 1]),
                    throttle=float(np.median(thr)),
                    throttle_spread=float(q3 - q1),
                    vbat=float(np.median(fl.vbat[s:e])),
                    level_mode=bool(np.any(fl.mode(BOX_ANGLE)[s:e] | fl.mode(BOX_HORIZON)[s:e])),
                    complete=bool(fr[e - 1] >= 0.97 * f_end_cfg),
                )
            )
    return runs


def excitation(fl: Flight, run: ChirpRun) -> np.ndarray:
    """Raw excitation cos(phase) (the instrument variable): logged, or reconstructed."""
    if run.z is not None:
        return run.z
    return fl.debug[run.start : run.end, 3] / 1000.0


def chirp_frequency(fl: Flight, run: ChirpRun) -> np.ndarray:
    if run.f_inst is not None:
        return run.f_inst
    return fl.debug[run.start : run.end, 2] / 10.0


def _chirp_signal(count: np.ndarray, f0: float, f1: float, T: float, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """chirp.c: f = f0*beta^(count*dt); phase = k0*f - k1; exc = cos(phase) (x f below 1 Hz)."""
    beta = (f1 / f0) ** (1.0 / T)
    k0 = 2 * np.pi / np.log(beta)
    k1 = k0 * f0
    n_max = int(T / dt)
    valid = (count >= 0) & (count < n_max)
    f = np.where(valid, f0 * beta ** (np.clip(count, 0, n_max) * dt), 0.0)
    exc = np.where(valid, np.cos(k0 * f - k1), 0.0)
    exc = np.where(f < 1.0, f * exc, exc)
    return exc, f


def reconstruct_chirps(fl: Flight, min_duration_s: float = 3.0) -> list[ChirpRun]:
    """Recover chirp runs from the CHIRP mode switch when the debug fields are not logged.

    The start loop of each sweep is found by maximizing the correlation between the
    regenerated excitation and the setpoint of each axis (which contains amp*leadlag(exc)).
    The axis is taken from the best correlation (firmware cycles roll->pitch->yaw).
    Small phase errors of the reconstruction (fast-math approximations in firmware) do not
    bias the instrument-variable estimate: they cancel in S_zy/S_zu.
    """
    h = fl.log.headers
    if "chirp_frequency_end_deci_hz" not in h:
        return []
    f0 = int(h.get("chirp_frequency_start_deci_hz", "2")) / 10.0
    f1 = int(h.get("chirp_frequency_end_deci_hz", "6000")) / 10.0
    T = float(h.get("chirp_time_seconds", "20"))
    on = fl.mode(6)
    if not on.any():
        return []
    dt_nom = 1.0 / fl.loop_hz
    iters = fl.log.col("loopIteration").astype(np.int64) if fl.log.has("loopIteration") else np.arange(fl.n) * fl.log_ratio
    idx = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
    runs: list[ChirpRun] = []
    for a, b in zip(idx[::2], idx[1::2]):
        if (fl.t[b - 1] - fl.t[a]) < min_duration_s:
            continue
        best = None
        it_seg = iters[a:b]
        # the mode flag is applied in the RX task; search +-60 ms around it, coarse then fine
        base = int(iters[a])
        for step, span in ((fl.log_ratio, int(0.06 * fl.loop_hz)), (1, 2 * fl.log_ratio)):
            centre = base if best is None else best[1]
            for off in range(-span, span + 1, step):
                cnt = it_seg - (centre + off)
                exc, _ = _chirp_signal(cnt, f0, f1, T, dt_nom)
                m = (cnt >= 0) & (cnt < 2.0 / dt_nom)  # first 2 s (low frequencies, robust)
                if m.sum() < 50:
                    continue
                for ax in range(3):
                    sp = fl.setpoint[a:b, ax][m]
                    c = abs(np.corrcoef(exc[m], sp - sp.mean())[0, 1]) if np.std(sp) > 0 else 0.0
                    if best is None or c > best[0]:
                        best = (c, centre + off, ax)
            base = best[1] if best else base
        if best is None or best[0] < 0.3:
            continue
        cnt = it_seg - best[1]
        exc, f = _chirp_signal(cnt, f0, f1, T, dt_nom)
        act = f > 0
        if act.sum() < min_duration_s * fl.fs:
            continue
        s0 = a + int(np.argmax(act))
        e0 = a + len(act) - int(np.argmax(act[::-1]))
        thr = fl.throttle[s0:e0]
        q1, q3 = np.percentile(thr, [25, 75])
        runs.append(ChirpRun(
            axis=int(best[2]), start=int(s0), end=int(e0), f_start=float(f[act][0]), f_end=float(f[act][-1]),
            throttle=float(np.median(thr)), throttle_spread=float(q3 - q1), vbat=float(np.median(fl.vbat[s0:e0])),
            level_mode=bool(np.any(fl.mode(BOX_ANGLE)[s0:e0] | fl.mode(BOX_HORIZON)[s0:e0])),
            complete=bool(f[act][-1] >= 0.97 * f1), z=exc[s0 - a : e0 - a], f_inst=f[s0 - a : e0 - a],
            reconstructed=True,
        ))
    return runs
