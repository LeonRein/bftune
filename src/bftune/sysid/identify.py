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
    round_gains: list = field(default_factory=list)  # gain of each chirp round / combined fit (repeatability)

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
    motor_output_limit: int = 100  # at identification (scales actuator authority)
    source: str = "chirp"  # "chirp" (precise) or "freestyle" (stick inputs only, low confidence)
    # robustness variants used by the optimizer/verdict: gain range and extra delay
    uncertainty: dict = field(default_factory=lambda: {"k_hi": 1.10, "k_lo": 0.88, "dT": 0.0003})

    def summary(self) -> str:
        lines = []
        for a, ai in self.axes.items():
            p = ai.plant.params
            ps = ", ".join(f"{k}={v:.4g}" for k, v in p.items())
            lines.append(
                f"{AXES[a]:5s}: {ai.plant.structure:16s} {ps}  | band {ai.fit_band[0]:.1f}-{ai.fit_band[1]:.1f} Hz"
                + (f" | chain check {'OK' if ai.chain.passed else 'FAIL'} (Fg {ai.chain.fg_rms_db:.2f} dB/{ai.chain.fg_rms_deg:.1f}°,"
                   f" D {ai.chain.d_rms_db:.2f} dB/{ai.chain.d_rms_deg:.1f}°)" if getattr(self, "source", "chirp") == "chirp"
                   else (f" | gyro-filter check {ai.chain.fg_rms_db:.2f} dB/{ai.chain.fg_rms_deg:.1f}° (stick band)"
                         if np.isfinite(ai.chain.fg_rms_db) else " | gyro-filter check n/a (stick band too narrow)"))
            )
        if self.motor:
            lines.append(
                f"motor: tau(hz) = 1/({self.motor.c0:.2f} + {self.motor.c1:.4f}*hz); "
                f"authority full/hover ≈ {self.motor.authority_ratio(self.motor.bins_hz[-1], float(np.median(self.motor.bins_hz))):.2f}"
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
        from .freestyle import identify_freestyle

        return identify_freestyle(fl, tune)
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
        # coherent band: contiguous from f_lo (gaps of one band tolerated; isolated coherent bins further up don't count)
        idx = np.flatnonzero(use)
        if idx.size:
            br = np.flatnonzero(np.diff(idx) > 2)
            coh_to = float(frf.f[idx[br[0]] if br.size else idx[-1]])
        else:
            coh_to = f_lo
        w = weights_from_coherence(frf.coh["y"], frf.coh["u"], frf.valid_frac) * use
        try:
            best, fits = fit_best(frf.f, G, w, structures[axis])
        except ValueError as e:
            notes.append(f"{AXES[axis]}: identification failed: {e}")
            continue
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
        round_gains = _round_gains(fl, sel, axis, plant, frf.f, use) if len(sel) >= 2 else []
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
            round_gains=round_gains,
        )
    if motor is not None:
        for a, ai in axes.items():
            t_ax = ai.plant.params["tau"]
            t_mm = float(motor.tau_at(np.mean(ai.op.motor_hz)))
            if abs(t_mm / t_ax - 1) > 0.5:
                notes.append(
                    f"{AXES[a]}: motor-model tau {t_mm*1000:.0f} ms vs axis fit {t_ax*1000:.0f} ms (>50% apart); only the "
                    "motor model's *trend* with speed is used for scheduling"
                )
    idn = Identification(axes=axes, motor=motor, runs=runs, dt=dt, time_scale=time_scale, notes=notes,
                         thrust_linear=tune.i("thrust_linear"), motor_output_limit=tune.i("motor_output_limit"))
    idn.uncertainty = chirp_uncertainty(axes)
    return idn


CONVENTION_GAIN_U = 0.10  # robust variants never narrower than +-10 % gain / +0.3 ms delay (convention)


def _round_gains(fl, runs, axis, plant, f_all, use) -> list[float]:
    """Plant gain of each chirp round relative to the combined fit (geometric mean of |G_round / G_model| over the
    fit band): how repeatable the identification is on this quad."""
    out = []
    for r in runs:
        try:
            fr = plant_frf(fl, [r], axis)
        except Exception:  # noqa: BLE001 - a bad round must not break identification
            continue
        m = (fr.coh["y"] >= 0.5) & (fr.f >= f_all[use].min()) & (fr.f <= f_all[use].max()) if use.any() else None
        if m is None or m.sum() < 4:
            continue
        out.append(float(np.exp(np.median(np.log(np.abs(fr.H["y"][m] / plant.fr(fr.f[m])))))))
    return out


def chirp_uncertainty(axes: dict) -> dict:
    """Robustness variants from the data: the spread of the plant gain between chirp rounds, never below the
    convention. (Rounds show repeatability, not every model error, hence the floor.)"""
    spread = [abs(np.log(g)) for ai in axes.values() for g in (getattr(ai, "round_gains", None) or [])]
    measured = float(np.expm1(max(spread))) if spread else None
    u = max(CONVENTION_GAIN_U, measured or 0.0)
    src = (f"chirp rounds: gain spread ±{100 * measured:.0f} % ({sum(len(getattr(ai, 'round_gains', []) or []) for ai in axes.values())} "
           f"rounds){'; convention floor ±10 %' if measured < CONVENTION_GAIN_U else ''}" if measured is not None
           else "convention (fewer than 2 chirp rounds per axis)")
    return {"k_hi": 1.0 + u, "k_lo": 1.0 / (1.0 + 1.25 * u), "dT": 0.0003, "source": src,
            "round_gain_spread": None if measured is None else round(measured, 3)}
