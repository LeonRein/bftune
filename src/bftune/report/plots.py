"""Matplotlib figures for the tuning report (PNG, light background, colour-blind safe)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ..analysis.loop import evaluate, step_metrics, step_response
from ..flight import AXES
from ..model.controller import OperatingPoint
from ..model.params import Tune, thrust_linear_slope
from ..model.plant import Plant

C_OLD, C_NEW, C_MEAS, C_ALT = "#6b7280", "#2563eb", "#111827", "#d97706"


def plant_bode(idn, path: Path) -> Path:
    fig, axs = plt.subplots(2, 3, figsize=(15, 7), sharex=True)
    for axis, ai in idn.axes.items():
        f = ai.frf.f
        m = ai.frf.coh["y"] >= 0.5
        ff = np.geomspace(1, 400, 400)
        H = ai.plant.fr(ff)
        a0, a1 = axs[0, axis], axs[1, axis]
        a0.semilogx(f[m], 20 * np.log10(np.abs(ai.G_meas[m])), "o", ms=3, color=C_MEAS, label="measured (chirp IV)")
        a0.semilogx(ff, 20 * np.log10(np.abs(H)), color=C_NEW, label=f"model: {ai.plant.structure}")
        a1.semilogx(f[m], np.degrees(np.unwrap(np.angle(ai.G_meas[m]))), "o", ms=3, color=C_MEAS)
        ph = np.degrees(np.unwrap(np.angle(H)))
        a1.semilogx(ff, ph, color=C_NEW)
        a0.axvspan(*ai.fit_band, color="#dbeafe", alpha=0.5, lw=0)
        a0.set_title(f"{AXES[axis]} plant  pidSum → gyro")
        a0.set_ylabel("dB (deg/s per pidSum)")
        a1.set_ylabel("phase [deg]")
        a1.set_xlabel("Hz")
        a0.grid(True, which="both", alpha=0.3)
        a1.grid(True, which="both", alpha=0.3)
        a0.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path


def _cases_for_plot(idn, axis, fl_loop, tune: Tune):
    ai = idn.axes[axis]
    return OperatingPoint(throttle=ai.op.throttle, motor_hz=ai.op.motor_hz, vbat=ai.op.vbat, dyn_notch_hz=ai.op.dyn_notch_hz)


def _plant_for(idn, axis, tune: Tune) -> Plant:
    ai = idn.axes[axis]
    y = ai.op.throttle
    p = dict(ai.plant.params)
    p["K"] *= thrust_linear_slope(tune.i("thrust_linear"), y) / thrust_linear_slope(idn.thrust_linear, y)
    return Plant(ai.plant.structure, p)


def loop_compare(idn, loop_hz: float, old: Tune, new: Tune, path: Path) -> Path:
    fig, axs = plt.subplots(2, 3, figsize=(15, 7), sharex=True)
    for axis in idn.axes:
        op = _cases_for_plot(idn, axis, loop_hz, old)
        for tune, col, lab in ((old, C_OLD, "current"), (new, C_NEW, "new")):
            M, r, _ = evaluate(tune, axis, _plant_for(idn, axis, tune), op, idn.dt, loop_hz, idn.time_scale)
            axs[0, axis].semilogx(r["f"], 20 * np.log10(np.abs(r["S"])), color=col,
                                  label=f"{lab}: Ms {M.ms:.2f}, PM {M.pm:.0f}°, fc {M.fc:.0f} Hz")
            axs[1, axis].semilogx(r["f"], 20 * np.log10(np.abs(r["L"])), color=col)
        axs[0, axis].axhline(0, color="k", lw=0.6)
        axs[0, axis].set_title(f"{AXES[axis]}: sensitivity |S| (disturbance → gyro), hover")
        axs[0, axis].set_ylim(-40, 10)
        axs[1, axis].set_ylim(-40, 40)
        axs[1, axis].axhline(0, color="k", lw=0.6)
        axs[1, axis].set_title("loop gain |L|")
        axs[1, axis].set_xlabel("Hz")
        for a in axs[:, axis]:
            a.grid(True, which="both", alpha=0.3)
        axs[0, axis].legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path


def step_compare(idn, loop_hz: float, rx_hz: float, old: Tune, new: Tune, path: Path) -> tuple[Path, dict]:
    fig, axs = plt.subplots(1, 3, figsize=(15, 4))
    metrics = {}
    for axis in idn.axes:
        ai = idn.axes[axis]
        op = OperatingPoint(throttle=ai.op.throttle, motor_hz=ai.op.motor_hz, vbat=ai.op.vbat, d_boost=1.0,
                            dyn_notch_hz=ai.op.dyn_notch_hz)
        for tune, col, lab in ((old, C_OLD, "current"), (new, C_NEW, "new")):
            r = step_response(tune, axis, _plant_for(idn, axis, tune), op, idn.dt, loop_hz, rx_hz, amplitude=300,
                              ramp_s=0.05, duration=0.25, time_scale=idn.time_scale)
            m = step_metrics(r)
            metrics[(axis, lab)] = m
            if lab == "current":
                axs[axis].plot(r["t"] * 1000, r["setpoint"], color=C_MEAS, lw=1, ls="--", label="setpoint")
            axs[axis].plot(r["t"] * 1000, r["gyro"], color=col,
                           label=f"{lab}: lag {m['tracking_lag_ms']:.1f} ms, overshoot {m['overshoot_pct']:.0f}%")
        axs[axis].set_title(f"{AXES[axis]}: stick flick 300°/s in 50 ms (model)")
        axs[axis].set_xlabel("ms")
        axs[axis].grid(True, alpha=0.3)
        axs[axis].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path, metrics


def noise_compare(nm, bands_old: np.ndarray, bands_new: np.ndarray, budget: dict, path: Path) -> Path:
    fig, axs = plt.subplots(1, 3, figsize=(15, 4))
    thr = [b.throttle for b in nm.bands]
    for axis in range(3):
        axs[axis].plot(thr, bands_old[:, axis], "o-", color=C_OLD, label="current")
        axs[axis].plot(thr, bands_new[:, axis], "o-", color=C_NEW, label="new")
        if budget is not None and axis in budget:
            axs[axis].plot(thr, budget[axis], "--", color=C_ALT, label="budget (proven-safe level)")
        axs[axis].set_title(f"{AXES[axis]}: predicted HF noise at motors")
        axs[axis].set_xlabel("throttle")
        axs[axis].set_ylabel("pidSum RMS (>70 Hz)")
        axs[axis].grid(True, alpha=0.3)
        axs[axis].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path


def motor_model_plot(motor, path: Path) -> Path | None:
    if motor is None:
        return None
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(motor.bins_hz, motor.tau * 1000, "o", color=C_MEAS, label="per speed bin (ARX)")
    hz = np.linspace(motor.bins_hz.min() * 0.8, motor.bins_hz.max() * 1.1, 100)
    ax.plot(hz, motor.tau_at(hz) * 1000, color=C_NEW, label="fit 1/τ = c0 + c1·f")
    ax.set_xlabel("motor speed [Hz]")
    ax.set_ylabel("motor time constant [ms]")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path
