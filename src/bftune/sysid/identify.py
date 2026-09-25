"""End-to-end plant identification from a flight with CHIRP runs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from ..flight import AXES, Flight
from ..model.controller import OperatingPoint, controller_fr
from ..model.params import Tune
from ..model.plant import Plant
from .chirp import ChirpRun, find_chirps
from .fit import FitResult, fit_best, weights_from_coherence
from .frf import BandFRF, plant_frf
from .motor import MotorModel, identify_motors

DEFAULT_STRUCTURES = {
    0: ["integ_lag", "integ_lag2", "pole_lag"],
    1: ["integ_lag", "integ_lag2", "pole_lag"],
    2: ["integ_lag_zero", "integ_lag_zero2", "integ_lag"],
}


@dataclass
class ChainCheck:
    fg_rms_db: float
    fg_rms_deg: float
    d_rms_db: float
    d_rms_deg: float
    band_hz: tuple[float, float]
    passed: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class AxisIdent:
    axis: int
    frf: BandFRF
    op: OperatingPoint
    fit: FitResult
    alternatives: dict[str, FitResult]
    fit_band: tuple[float, float]
    chain: ChainCheck
    G_meas: np.ndarray  # plant estimate (unfiltered-gyro path)
    G_meas_f: np.ndarray  # plant estimate via filtered gyro / modelled Fg (dyn notch unknown!)
    coherent_to_hz: float

    @property
    def plant(self) -> Plant:
        return self.fit.plant


@dataclass
class Identification:
    axes: dict[int, AxisIdent]
    motor: MotorModel | None
    runs: list[ChirpRun]
    dt: float  # nominal loop period
    time_scale: float  # actual/nominal loop period
    notes: list[str] = field(default_factory=list)
    thrust_linear: int = 0  # thrust_linear of the identification tune (plant includes its curve)

    def summary(self) -> str:
        lines = []
        for a, ai in self.axes.items():
            p = ai.plant.params
            ps = ", ".join(f"{k}={v:.4g}" for k, v in p.items())
            lines.append(
                f"{AXES[a]:5s}: {ai.plant.structure:16s} {ps}  | band {ai.fit_band[0]:.1f}-{ai.fit_band[1]:.1f} Hz"
                f" | chain check {'OK' if ai.chain.passed else 'FAIL'} (Fg {ai.chain.fg_rms_db:.2f} dB/{ai.chain.fg_rms_deg:.1f}°,"
                f" D {ai.chain.d_rms_db:.2f} dB/{ai.chain.d_rms_deg:.1f}°)"
            )
        if self.motor:
            lines.append(
                f"motor: tau(hz) = 1/({self.motor.c0:.2f} + {self.motor.c1:.4f}*hz); "
                f"authority full/hover ≈ {self.motor.authority_ratio(self.motor.bins_hz[-1], self.motor.bins_hz[len(self.motor.bins_hz)//2]):.2f}"
            )
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)


def operating_point_for(fl: Flight, runs: list[ChirpRun]) -> OperatingPoint:
    idx = np.concatenate([np.arange(r.start, r.end) for r in runs])
    mhz = np.median(fl.motor_hz[idx], axis=0) if fl.motor_hz is not None else np.array([200.0] * 4)
    return OperatingPoint(
        throttle=float(np.median(fl.throttle[idx])),
        motor_hz=[float(x) for x in mhz],
        vbat=float(np.median(fl.vbat[idx])),
        label="identification",
    )


def _rms_err(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    r = np.log(a / b)
    if w.sum() <= 0:
        return float("nan"), float("nan")
    db = np.sqrt(np.average((20 / np.log(10) * r.real) ** 2, weights=w))
    deg = np.sqrt(np.average(np.degrees(r.imag) ** 2, weights=w))
    return float(db), float(deg)


def identify(
    fl: Flight,
    tune: Tune,
    structures: dict[int, list[str]] | None = None,
    min_fit_hz: float = 3.0,
    coh_min: float = 0.5,
) -> Identification:
    runs = find_chirps(fl)
    if not runs:
        raise ValueError(
            "No CHIRP runs found. Record the log with debug_mode = CHIRP and fly the chirp mode "
            "(see the bf-flight-protocol skill)."
        )
    structures = structures or DEFAULT_STRUCTURES
    dt = 1.0 / fl.loop_hz
    time_scale = fl.loop_hz / (fl.fs * fl.log_ratio)
    motor = identify_motors(fl)
    notes: list[str] = []
    if abs(time_scale - 1) > 0.005:
        notes.append(f"loop runs at {1/(dt*time_scale):.0f} Hz real time (gyro clock skew {100*(1/time_scale-1):+.1f}%)")
    axes: dict[int, AxisIdent] = {}
    for axis in range(3):
        sel = [r for r in runs if r.axis == axis]
        if not sel:
            notes.append(f"no chirp for {AXES[axis]}")
            continue
        if any(r.level_mode for r in sel):
            notes.append(f"{AXES[axis]} chirp flown in ANGLE/HORIZON: fit starts at {min_fit_hz} Hz")
        frf = plant_frf(fl, runs, axis)
        op = operating_point_for(fl, sel)
        C = controller_fr(tune, axis, op, frf.f, dt, fl.loop_hz, time_scale=time_scale)
        G = frf.H["y"]
        Gf = frf.H["yf"] / C.Fg
        f_lo = max(min_fit_hz, 3.0 * min(r.f_start for r in sel))
        use = (frf.f >= f_lo) & (frf.coh["y"] >= coh_min) & (frf.coh["u"] >= 0.8) & (frf.valid_frac >= 0.8)
        # coherent band: contiguous from f_lo
        coh_to = frf.f[use].max() if use.any() else f_lo
        w = weights_from_coherence(frf.coh["y"], frf.coh["u"], frf.valid_frac) * use
        best, fits = fit_best(frf.f, G, w, structures[axis])
        # chain check where both gyro signals are coherent. The dyn-notch centre is not
        # logged, so scan it and keep the best match (also gives an estimate of where it sits).
        mfg = use & (frf.coh["yf"] >= 0.8)
        fg_meas = frf.H["yf"] / G
        dn_hz = None
        fg_db, fg_deg = _rms_err(fg_meas, C.Fg, mfg.astype(float))
        if tune.dyn_notch_enabled(fl.loop_hz) and mfg.any():
            best_err = fg_db / 0.5 + fg_deg / 5.0
            for c in np.linspace(tune.i("dyn_notch_min_hz"), tune.i("dyn_notch_max_hz"), 26):
                op_c = replace(op, dyn_notch_hz=[float(c)] * tune.i("dyn_notch_count"))
                Cc = controller_fr(tune, axis, op_c, frf.f, dt, fl.loop_hz, time_scale=time_scale)
                e_db, e_deg = _rms_err(fg_meas, Cc.Fg, mfg.astype(float))
                if e_db / 0.5 + e_deg / 5.0 < best_err:
                    best_err, fg_db, fg_deg, dn_hz, C = e_db / 0.5 + e_deg / 5.0, e_db, e_deg, float(c), Cc
            if dn_hz is not None:
                op = op_c = replace(op, dyn_notch_hz=[dn_hz] * tune.i("dyn_notch_count"))
                Gf = frf.H["yf"] / C.Fg
        md = (frf.f >= f_lo) & (frf.coh.get("D", np.zeros_like(frf.f)) >= 0.8)
        if tune.kd(axis) > 0 and md.any():
            d_db, d_deg = _rms_err(frf.H["D"] / frf.H["yf"], -C.D, md.astype(float))
        else:
            d_db, d_deg = 0.0, 0.0
        passed = (fg_db < 1.0 and fg_deg < 8.0) and (d_db < 1.5 and d_deg < 10.0)
        chain = ChainCheck(fg_db, fg_deg, d_db, d_deg, (f_lo, float(coh_to)), passed)
        if not passed:
            chain.notes.append("modelled filter chain disagrees with the log: check firmware version/config source")
        plant = best.plant
        plant.ref_motor_hz = float(np.mean(op.motor_hz))
        plant.ref_vbat = op.vbat
        plant.ref_throttle = op.throttle
        axes[axis] = AxisIdent(
            axis=axis,
            frf=frf,
            op=op,
            fit=best,
            alternatives=fits,
            fit_band=(f_lo, float(coh_to)),
            chain=chain,
            G_meas=G,
            G_meas_f=Gf,
            coherent_to_hz=float(coh_to),
        )
    return Identification(axes=axes, motor=motor, runs=runs, dt=dt, time_scale=time_scale, notes=notes,
                          thrust_linear=tune.i("thrust_linear"))
