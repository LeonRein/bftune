"""Blackbox gaps: frames the logger dropped (device too slow for the log rate, corrupt sections the decoder skipped).

Data across a gap is not continuous: no FFT window or filter may span one. The frame just before a gap
can itself be corrupt (decoded from a damaged stream), so both frames around a gap are treated as unusable.
"""

from __future__ import annotations

import numpy as np

GAP_FACTOR = 3.0  # dt > 3 x median frame interval = frames missing


def gaps(t: np.ndarray) -> np.ndarray:
    """Indices k where frames are missing between frame k and k+1."""
    dt = np.diff(t)
    return np.flatnonzero(dt > GAP_FACTOR * np.median(dt)) if dt.size else np.zeros(0, int)


def continuous(fl) -> np.ndarray:
    """(N,) False on the frame either side of every gap: AND it into a mask so that its runs never span a gap."""
    ok = np.ones(fl.n, bool)
    g = gaps(fl.t)
    ok[g] = ok[g + 1] = False
    return ok


def segments(fl, mask: np.ndarray, min_len: int = 1) -> list[tuple[int, int]]:
    """[start, end) runs of `mask` that contain no gap and are at least `min_len` frames long."""
    idx = np.flatnonzero(np.diff(np.concatenate([[0], (mask & continuous(fl)).astype(int), [0]])))
    return [(int(a), int(b)) for a, b in zip(idx[::2], idx[1::2]) if b - a >= min_len]
