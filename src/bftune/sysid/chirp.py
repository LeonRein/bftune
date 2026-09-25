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

    @property
    def n(self) -> int:
        return self.end - self.start


def find_chirps(fl: Flight, min_duration_s: float = 3.0) -> list[ChirpRun]:
    if fl.debug_mode != DEBUG_CHIRP:
        return []
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
    """Raw excitation cos(phase) as logged (used as the instrument variable)."""
    return fl.debug[run.start : run.end, 3] / 1000.0


def chirp_frequency(fl: Flight, run: ChirpRun) -> np.ndarray:
    return fl.debug[run.start : run.end, 2] / 10.0
