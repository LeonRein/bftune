"""Discrete-time models of the Betaflight 2026.x filters.

Every function mirrors betaflight/src/main/common/filter.c. Frequency responses are
evaluated exactly on the unit circle, z = exp(j*2*pi*f*dt), at the rate the firmware
runs the filter (normally the PID loop rate).

Filter-type names follow the CLI: PT1, BIQUAD (a TPT state-variable filter with
Butterworth Q since 2026.6), PT2, PT3.
"""

from __future__ import annotations

import math

import numpy as np

CUTOFF_CORRECTION = {1: 1.0, 2: 1.553773974, 3: 1.961459177}
BUTTERWORTH_INV_Q = 1.41421356237
PT_ORDER = {"PT1": 1, "PT2": 2, "PT3": 3}


def zinv(f: np.ndarray, dt: float) -> np.ndarray:
    return np.exp(-2j * np.pi * np.asarray(f, dtype=float) * dt)


# --------------------------------------------------------------------------- gains
def pt1_gain(fc: float, dt: float) -> float:
    """pt1FilterGain(): k = w/(w+1), w = 2*pi*fc*dt."""
    w = 2.0 * math.pi * fc * dt
    return w / (w + 1.0)


def ptn_gain(fc: float, dt: float, order: int) -> float:
    return pt1_gain(fc * CUTOFF_CORRECTION[order], dt)


def svf_g(fc: float, dt: float) -> float:
    return math.tan(math.pi * fc * dt)


# --------------------------------------------------------------------------- responses
def fr_ptn(fc: float, f: np.ndarray, dt: float, order: int) -> np.ndarray:
    if fc <= 0:
        return np.ones_like(np.asarray(f, dtype=float), dtype=complex)
    k = ptn_gain(fc, dt, order)
    h1 = k / (1.0 - (1.0 - k) * zinv(f, dt))
    return h1**order


def fr_svf_lpf(fc: float, f: np.ndarray, dt: float) -> np.ndarray:
    """TPT SVF lowpass = bilinear (prewarped at fc) of a Butterworth 2nd-order LPF."""
    if fc <= 0:
        return np.ones_like(np.asarray(f, dtype=float), dtype=complex)
    g = svf_g(fc, dt)
    k = BUTTERWORTH_INV_Q
    z1 = zinv(f, dt)
    num = g * g * (1 + z1) ** 2
    den = (1 + g * k + g * g) + 2 * (g * g - 1) * z1 + (1 - g * k + g * g) * z1 * z1
    return num / den


def fr_svf_bandpass_norm(f0: float, q: float, f: np.ndarray, dt: float) -> np.ndarray:
    """k*BP(z) of the TPT SVF (unity gain at f0)."""
    g = svf_g(f0, dt)
    k = 1.0 / q
    z1 = zinv(f, dt)
    num = k * g * (1 - z1 * z1)
    den = (1 + g * k + g * g) + 2 * (g * g - 1) * z1 + (1 - g * k + g * g) * z1 * z1
    return num / den


def fr_svf_notch(f0: float, q: float, f: np.ndarray, dt: float, weight: float = 1.0) -> np.ndarray:
    """svfNotchApply / rpmNotchApply: y = x - w*k*BP."""
    if f0 <= 0 or weight <= 0:
        return np.ones_like(np.asarray(f, dtype=float), dtype=complex)
    return 1.0 - weight * fr_svf_bandpass_norm(f0, q, f, dt)


def notch_q_from_cutoff(center: float, cutoff: float) -> float:
    """filterGetNotchQ()."""
    return center * cutoff / (center * center - cutoff * cutoff)


def fr_lowpass(ftype: str, fc: float, f: np.ndarray, dt: float) -> np.ndarray:
    """Lowpass of the given CLI type. fc<=0 disables. SVF above 0.95*Nyquist is disabled."""
    ftype = ftype.upper()
    if fc <= 0:
        return np.ones_like(np.asarray(f, dtype=float), dtype=complex)
    if ftype in PT_ORDER:
        return fr_ptn(fc, f, dt, PT_ORDER[ftype])
    if ftype in ("BIQUAD", "SVF"):
        if fc > 0.95 * 0.5 / dt:
            return np.ones_like(np.asarray(f, dtype=float), dtype=complex)
        return fr_svf_lpf(fc, f, dt)
    raise ValueError(f"unknown filter type {ftype}")


def dyn_lpf_cutoff(fmin: float, fmax: float, expo: int, throttle: float) -> float:
    """dynLpfCutoffFreq() in pid.c (used by gyro and D-term dynamic lowpass)."""
    t = min(max(round(throttle * 100) / 100.0, 0.0), 1.0)
    if expo > 0:
        e = expo / 10.0
        curve = t * (1 - t) * e + t
        return (fmax - fmin) * curve + fmin
    # expo == 0 legacy curve
    dyn_thr = 1.5 * t * (1 - t * t / 3.0)
    return max(dyn_thr * fmax, fmin)


def phase_comp_coeffs(center_hz: float, center_phase_deg: float, dt: float) -> tuple[float, float, float]:
    """phaseCompUpdate(): returns (b0, b1, a1) of y = b0 x + b1 x1 - a1 y1."""
    omega = 2 * math.pi * center_hz * dt
    sn = math.sin(math.radians(center_phase_deg))
    gain = (1 + sn) / (1 - sn)
    alpha = (12 - omega * omega) / (6 * omega * math.sqrt(gain))
    b0 = 1 + alpha * gain
    b1 = 2 - b0
    a1 = 1 - alpha
    a0 = 1 / (1 + alpha)
    return b0 * a0, b1 * a0, a1 * a0


def chirp_leadlag(lag_hz: float, lead_hz: float, dt: float) -> tuple[float, float, float]:
    """Excitation shaping filter from pid_init.c (chirp)."""
    alpha = lead_hz / lag_hz
    center = lag_hz * math.sqrt(alpha)
    phase = math.degrees(math.asin((1 - alpha) / (1 + alpha)))
    return phase_comp_coeffs(center, phase, dt)


def fr_first_order(b0: float, b1: float, a1: float, f: np.ndarray, dt: float) -> np.ndarray:
    z1 = zinv(f, dt)
    return (b0 + b1 * z1) / (1 + a1 * z1)


# --------------------------------------------------------------------------- time domain
class PTn:
    __slots__ = ("k", "s")

    def __init__(self, fc: float, dt: float, order: int):
        self.k = ptn_gain(fc, dt, order) if fc > 0 else 1.0
        self.s = [0.0] * order

    def set_cutoff(self, fc: float, dt: float) -> None:
        self.k = ptn_gain(fc, dt, len(self.s))

    def __call__(self, x: float) -> float:
        k = self.k
        s = self.s
        for i in range(len(s)):
            s[i] += k * (x - s[i])
            x = s[i]
        return x


class SvfLpf:
    __slots__ = ("a1", "a2", "g", "ic1", "ic2")

    def __init__(self, fc: float, dt: float):
        self.ic1 = self.ic2 = 0.0
        self.set_cutoff(fc, dt)

    def set_cutoff(self, fc: float, dt: float) -> None:
        g = svf_g(fc, dt)
        inv = 1.0 / (1.0 + g * (g + BUTTERWORTH_INV_Q))
        self.a1, self.a2, self.g = inv, g * inv, g

    def __call__(self, x: float) -> float:
        v3 = x - self.ic2
        v1 = self.a1 * self.ic1 + self.a2 * v3
        v2 = self.ic2 + self.g * v1
        self.ic1 = 2 * v1 - self.ic1
        self.ic2 = 2 * v2 - self.ic2
        return v2


class SvfNotch:
    """Notch with optional weight (weight=1 → svfNotch, else rpmNotch)."""

    __slots__ = ("a1", "a2", "g", "ic1", "ic2", "wq")

    def __init__(self, f0: float, q: float, dt: float, weight: float = 1.0):
        self.ic1 = self.ic2 = 0.0
        self.update(f0, q, dt, weight)

    def update(self, f0: float, q: float, dt: float, weight: float = 1.0) -> None:
        g = svf_g(max(f0, 1e-3), dt)
        k = 1.0 / q
        a1 = 1.0 / (1.0 + g * (g + k))
        self.a1, self.a2, self.g, self.wq = a1, g * a1, g, k * weight

    def __call__(self, x: float) -> float:
        v3 = x - self.ic2
        v1 = self.a1 * self.ic1 + self.a2 * v3
        v2 = self.ic2 + self.g * v1
        self.ic1 = 2 * v1 - self.ic1
        self.ic2 = 2 * v2 - self.ic2
        return x - self.wq * v1


def make_lowpass(ftype: str, fc: float, dt: float):
    """Time-domain lowpass matching fr_lowpass (returns identity if disabled)."""
    ftype = ftype.upper()
    if fc <= 0:
        return lambda x: x
    if ftype in PT_ORDER:
        return PTn(fc, dt, PT_ORDER[ftype])
    if ftype in ("BIQUAD", "SVF"):
        if fc > 0.95 * 0.5 / dt:
            return lambda x: x
        return SvfLpf(fc, dt)
    raise ValueError(ftype)
