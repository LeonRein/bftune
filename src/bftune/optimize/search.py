"""Robust tune optimization on the identified model.

Structure
---------
* Operating cases per axis: throttle points (idle, identification/hover, mid, full) x
  battery (+/-8 %) x extra plant delay (+0.3 ms) x D boost (base D, D at d_max).
  Nominal cases carry the performance objective; all cases carry robustness constraints.
* Inner problem (per axis, per filter configuration): choose P, I, D, d_max/D by
  differential evolution with penalty constraints. The loop gain is linear in the gains,
  L = G*(kp*bP + ki*bI + kd*bD), so each case is precomputed once per filter config.
* Outer problem: coordinate descent over global settings (gyro/D-term lowpasses, dyn notch,
  RPM Q, TPA). Each candidate is scored by the sum of the optimized axis objectives.
* Noise at the motors (P + D path, high frequency) is predicted with the calibrated
  alias-aware noise model and constrained relative to the current tune.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import differential_evolution

from ..flight import AXES, FlightSummary
from ..model import filters as flt
from ..model.controller import OperatingPoint, controller_fr
from ..model.params import DTERM_SCALE, ITERM_SCALE, PTERM_SCALE, Tune, thrust_linear_slope
from ..noise.model import NoiseModel
from ..sysid.identify import Identification


@dataclass
class Goals:
    style: str = "freestyle"  # freestyle | race | cinematic
    ms_max: float = 2.0  # nominal peak sensitivity
    ms_max_robust: float = 2.4
    pm_min: float = 45.0
    pm_min_robust: float = 35.0
    gm_min_db: float = 6.0
    gm_min_robust_db: float = 4.0
    dm_min_ms: float = 1.0  # delay margin (nominal)
    noise_budget: float = 1.0  # motor noise vs the proven-safe level; the agent sets it from motor temperature
    perf_band: tuple[float, float] = (3.0, 60.0)
    idle_weight: float = 0.6  # propwash: weight of the idle case in the objective
    dmax_ratio_max: float = 1.6
    # I is set from P (Betaflight units) — attitude hold through flips/wind is a judgement call
    # the linear model cannot score well; defaults follow proven freestyle ratios.
    i_over_p: tuple[float, float, float] = (1.5, 1.5, 1.6)
    hf_extrapolation: float = 1.25  # max 1-3 kHz filter gain relative to the least-filtered safe tune
    tracking_weight: float = 0.5  # weight of setpoint-tracking error (1-15 Hz, without FF) in the objective
    tracking_band: tuple[float, float] = (1.0, 15.0)
    d_over_p: tuple[float, float] = (0.4, 1.2)  # allowed D/P ratio (Betaflight units) on roll/pitch
    gain_range: tuple[float, float] = (0.5, 2.5)  # P and D relative to the logged (anchor) tune
    ff_overshoot_flick: float = 10.0  # feedforward targets (% overshoot of a 300 deg/s flick / a fast snap)
    ff_overshoot_snap: float = 15.0
    peak_max: tuple | None = None  # as-flown step peak per axis [%]; None = the flown tune's own (workbench)
    idle_f_min: float = 0.0  # idle margins count above this [Hz]; the workbench derives it from the log
    mid_throttle: float = 0.5  # the "mid" design case; the workbench sets it from the throttle the pilot uses
    gain_uncertainty: float = 0.10  # robust variants: plant gain x(1+u) and /(1+1.25u), delay +y ms (identification)
    delay_uncertainty_ms: float = 0.3

    @classmethod
    def for_style(cls, style: str, noise_budget: float | None = None) -> Goals:
        """Conventions for a style (see optimize/targets.py; the agent overrides them from the data)."""
        from .targets import build_goals

        return build_goals(style, None, None, noise_budget)[0]


@dataclass
class Case:
    label: str
    op: OperatingPoint
    plant_params: dict
    plant_structure: str
    weight: float  # objective weight (0 = constraint only)
    robust: bool  # True: relaxed limits (uncertainty variant)
    limits: dict | None = None  # per-case override {"pm", "gm", "ms"} (idle cases relative to the flown tune)
    f_min: float = 0.0  # margins count only above this frequency (idle: 1 / how long idle stretches last)


@dataclass
class AxisResult:
    axis: int
    p: int
    i: int
    d: int
    d_max: int
    objective: float
    feasible: bool
    worst: dict
    noise_ratio: float
    per_case: list[dict] = field(default_factory=list)


@dataclass
class SearchResult:
    tune: Tune
    axes: dict[int, AxisResult]
    score: float
    history: list[tuple[str, float]]


def idle_motor_hz(src: FlightSummary, tune: Tune) -> float:
    """Idle motor speed: 20th percentile of natural idle, or the dyn_idle floor if higher."""
    return max(src.idle_hz(20), tune.i("dyn_idle_min_rpm") * 100 / 60.0)


def output_limit_ratio(tune: Tune, idn: Identification) -> float:
    """motor_output_limit scales the motor output range (mixer_init.c), so actuator authority and the
    noise reaching the motors scale with it, relative to the limit the plant was identified with."""
    return tune.i("motor_output_limit") / max(getattr(idn, "motor_output_limit", 100), 1)


def build_cases(fl: FlightSummary, idn: Identification, tune: Tune, axis: int, goals: Goals,
                search: bool = False) -> list[Case]:
    """Design cases for one axis. `search=True` (optimizers) adds the D-max cases whenever a D-max driver is enabled,
    so a search that raises d_max above d is constrained by them; the verdict adds them when d_max > d."""
    ai = idn.axes[axis]
    cases: list[Case] = []
    thr_id = ai.op.throttle
    points = [("idle", 0.02, goals.idle_weight), ("hover", thr_id, 1.0), ("mid", goals.mid_throttle, 0.6), ("full", 1.0, 0.0)]
    for label, thr, w in points:
        if label == "idle":
            mhz = np.full(len(ai.op.motor_hz), idle_motor_hz(fl, tune))
        elif label == "hover":
            mhz = np.array(ai.op.motor_hz)
        else:
            mhz = fl.motor_hz_at(thr)
        base = ai.plant.scaled(float(np.mean(mhz)), ai.op.vbat, idn.motor)
        dn = ai.op.dyn_notch_hz
        # D only rises above base D through d_max_gain (gyro) or d_max_advance (setpoint): pid.c
        drivers = tune.i("d_max_gain") > 0 or tune.i("d_max_advance") > 0
        dmax_active = drivers and (search or tune.i(f"d_max_{AXES[axis]}") > tune.i(f"d_{AXES[axis]}"))
        for boost in ((0.0, 1.0) if dmax_active else (0.0,)):
            op = OperatingPoint(throttle=thr, motor_hz=list(mhz), vbat=ai.op.vbat, d_boost=boost, dyn_notch_hz=dn,
                                label=f"{label}/d{'max' if boost else ''}")
            fmin = goals.idle_f_min if label == "idle" else 0.0
            cases.append(Case(op.label, op, dict(base.params), base.structure, w if boost == 0 else 0.0, False, None, fmin))
            # uncertainty variants (constraint only)
            u, dT_ = goals.gain_uncertainty, goals.delay_uncertainty_ms / 1000.0
            for vf, dT, tag in ((1.0 + u, dT_, "hiK+delay"), (1.0 / (1.0 + 1.25 * u), 0.0, "loK")):
                p = dict(base.params)
                p["K"] *= vf
                p["T"] += dT
                cases.append(Case(f"{op.label}/{tag}", op, p, base.structure, 0.0, True, None, fmin))
            # dyn notch at its minimum (it wanders): constraint only
            if tune.dyn_notch_enabled(fl.loop_hz) and dn is not None:
                opw = copy.copy(op)
                opw.dyn_notch_hz = None
                cases.append(Case(f"{op.label}/dn@min", opw, dict(base.params), base.structure, 0.0, True, None, fmin))
    return cases


class AxisProblem:
    """Precomputed basis responses for fast gain evaluation on one axis."""

    def __init__(self, tune: Tune, axis: int, cases: list[Case], idn: Identification, loop_hz: float,
                 nm: NoiseModel | None, goals: Goals, noise_ref: np.ndarray | None,
                 f: np.ndarray | None = None):
        from ..model.plant import plant_fr

        self.axis, self.goals, self.cases = axis, goals, cases
        self.tune = tune
        self.f = f if f is not None else np.geomspace(1.0, 0.45 * loop_hz, 360)
        fr = self.f
        perf_m = (fr >= goals.perf_band[0]) & (fr <= goals.perf_band[1])
        self.perf_m = perf_m
        self.B = []  # per case: loop-gain bases (G*Fg*P, G*Fg*I, G*Fg*Fd*D) per unit gain
        self.R = []  # per case: reference-path bases (G*P, G*I) for setpoint tracking
        self.trk_m = (fr >= goals.tracking_band[0]) & (fr <= goals.tracking_band[1])
        self.anchor = None  # (P0, D0) of the logged tune: bounds are relative to it
        for c in cases:
            C = controller_fr(tune, axis, c.op, fr, idn.dt, loop_hz, time_scale=idn.time_scale)
            G = plant_fr(c.plant_structure, c.plant_params, fr)
            # thrust_linear changes actuator gain relative to the identification tune
            y = min(max(c.op.throttle, 0.0), 1.0)
            G = G * (thrust_linear_slope(tune.i("thrust_linear"), y)
                     / thrust_linear_slope(getattr(idn, "thrust_linear", 0), y)) * output_limit_ratio(tune, idn)
            kp, ki = tune.kp(axis), tune.ki(axis)
            bP = C.Fg * (C.P / kp if kp > 0 else PTERM_SCALE)  # per unit Kp (includes yaw LPF, TPA if PD)
            bI = C.Fg * (C.I / ki if ki > 0 else ITERM_SCALE * idn.dt / (1 - flt.zinv(fr * idn.time_scale, idn.dt)))
            tpa = tune.tpa_factor(c.op.throttle, c.op.tpa_low_active)
            z1 = flt.zinv(fr * idn.time_scale, idn.dt)
            bD = C.Fg * C.Fd * tpa * (1 - z1) / idn.dt  # per unit Kd (boost handled in eval)
            rP = C.P / kp if kp > 0 else PTERM_SCALE * np.ones_like(fr, dtype=complex)
            rI = C.I / ki if ki > 0 else ITERM_SCALE * idn.dt / (1 - z1)
            self.B.append((G * bP, G * bI, G * bD))
            self.R.append((G * rP, G * rI))
        # noise quadratic forms per band: ∫S|kp bP + kd bD|^2 (HF, base D)
        self.noise_Q = None
        self.noise_ref = noise_ref
        if nm is not None and nm.bands:
            fn = np.linspace(getattr(nm, "motion_hz", 70.0), 0.5 * loop_hz / idn.time_scale, 3000)
            dfn = fn[1] - fn[0]
            Q = []
            for b in nm.bands:
                # average power quadratic form over motor-speed samples
                samples = b.motor_hz_samples if b.motor_hz_samples is not None else np.array([b.motor_hz])
                App = Add = Apd = 0.0
                S = nm.true_psd(b, axis, fn)
                for mh in samples[:4]:
                    op = OperatingPoint(throttle=b.throttle, motor_hz=list(mh))
                    C = controller_fr(tune, axis, op, fn, nm.dt, loop_hz, time_scale=nm.time_scale)
                    kp = tune.kp(axis) or PTERM_SCALE
                    bp = C.Fg * C.P / kp if tune.kp(axis) else C.Fg * PTERM_SCALE
                    tpa = tune.tpa_factor(b.throttle)
                    z1 = flt.zinv(fn * nm.time_scale, nm.dt)
                    bd = C.Fg * C.Fd * tpa * (1 - z1) / nm.dt
                    App += np.sum(S * np.abs(bp) ** 2) * dfn
                    Add += np.sum(S * np.abs(bd) ** 2) * dfn
                    Apd += np.sum(S * np.real(bp * np.conj(bd))) * dfn
                k = min(4, len(samples))
                Q.append((App / k, Add / k, Apd / k))
            self.noise_Q = np.array(Q)
            self.noise_scale = np.array([thrust_linear_slope(tune.i("thrust_linear"), b.throttle) for b in nm.bands]) \
                * output_limit_ratio(tune, idn)

    def noise(self, kp: float, kd: float) -> np.ndarray:
        """HF noise at the motors per noise band (pidSum units x thrust_linear slope)."""
        if self.noise_Q is None:
            return np.zeros(1)
        App, Add, Apd = self.noise_Q.T
        return np.sqrt(np.maximum(kp * kp * App + kd * kd * Add + 2 * kp * kd * Apd, 0)) * self.noise_scale

    def evaluate(self, P: float, I: float, D: float, dmax_ratio: float, detail: bool = False):
        from ..analysis.loop import metrics

        g = self.goals
        kp, ki, kd = PTERM_SCALE * P, ITERM_SCALE * I * (2.5 if self.axis == 2 and self.tune.s("use_integrated_yaw") != "ON" else 1.0), DTERM_SCALE * D
        penalty = 0.0
        obj = 0.0
        wsum = 0.0
        rows = []
        worst = {"pm": 999.0, "gm": 999.0, "ms": 0.0, "dm": 999.0}
        worst_case = {"pm": "", "gm": "", "ms": ""}
        fc_nom = []
        for c, (gp, gi, gd), (rp, ri) in zip(self.cases, self.B, self.R):
            kd_eff = kd * (1 + c.op.d_boost * (dmax_ratio - 1))
            L = kp * gp + ki * gi + kd_eff * gd
            M = metrics(self.f, L, c.f_min)
            # small internal safety margin so integer rounding cannot push the result over a limit
            lim = case_limits(c, g)
            pm_min, gm_min, ms_max = lim["pm"] + 1.5, lim["gm"] + 0.3, lim["ms"] - 0.04
            if not np.isfinite(M.pm):
                # no gain crossover: loop gain < 1 everywhere (no authority) or > 1 everywhere
                pm = 90.0 if np.max(np.abs(L[self.f >= c.f_min])) < 1 else -90.0
            else:
                pm = M.pm
            penalty += max(0.0, pm_min - pm) ** 2 / 25.0
            penalty += max(0.0, gm_min - M.gm_db) ** 2
            penalty += 100 * max(0.0, M.ms - ms_max) ** 2
            if not c.robust and np.isfinite(M.fc):
                penalty += max(0.0, g.dm_min_ms - M.dm_ms) ** 2 * 10
            if c.weight > 0:
                S = 1 / (1 + L[self.perf_m])
                obj += c.weight * float(np.mean(20 * np.log10(np.abs(S))))
                # setpoint tracking by the feedback loop alone (P+I; FF comes on top): penalizes
                # D-only loops that reject disturbances but neither hold attitude nor track
                m = self.trk_m
                E = 1 - (kp * rp[m] + ki * ri[m]) / (1 + L[m])
                obj += c.weight * g.tracking_weight * float(np.mean(20 * np.log10(np.abs(E) + 1e-6)))
                wsum += c.weight
                if np.isfinite(M.fc):
                    fc_nom.append(M.fc)
            if pm < worst["pm"]:
                worst["pm"], worst_case["pm"] = pm, c.label
            if M.gm_db < worst["gm"]:
                worst["gm"], worst_case["gm"] = M.gm_db, c.label
            if M.ms > worst["ms"]:
                worst["ms"], worst_case["ms"] = M.ms, c.label
            if not c.robust:
                worst["dm"] = min(worst["dm"], M.dm_ms)
            if detail:
                rows.append({"case": c.label, "robust": c.robust, **M.as_dict(), "pm_eff": pm, "limits": lim,
                             "relaxed": c.limits is not None})
        obj /= max(wsum, 1e-9)
        if self.axis < 2 and P > 0 and D > 0:
            r = D / P
            lo, hi = g.d_over_p
            penalty += 20 * (max(0.0, lo - r) ** 2 + max(0.0, r - hi) ** 2)
        nr = 0.0
        if self.noise_Q is not None and self.noise_ref is not None:
            nr = float(np.max(self.noise(kp, kd) / np.maximum(self.noise_ref, 1e-9)))
            limit = getattr(self, "noise_limit", None) or g.noise_budget
            penalty += 2000 * max(0.0, nr - 0.97 * limit) ** 2
        total = obj + penalty
        if detail:
            self.worst_case = worst_case
            return total, obj, penalty, worst, nr, rows
        return total


def optimize_axis(prob: AxisProblem, tune: Tune, seed: int = 0, maxiter: int = 50) -> AxisResult:
    """Optimize P, D and d_max/D for one axis; I follows P via goals.i_over_p."""
    ax = AXES[prob.axis]
    P0, D0 = tune.i(f"p_{ax}"), tune.i(f"d_{ax}")
    dm0 = tune.i(f"d_max_{ax}")
    ip = prob.goals.i_over_p[prob.axis]
    yaw_no_d = prob.axis == 2 and D0 == 0
    # bounds relative to the *logged* tune (anchor), never to the evolving search state
    Pa, Da = prob.anchor if prob.anchor else (P0, D0)
    lo, hi = prob.goals.gain_range
    bP = (max(10.0, lo * Pa), min(250.0 / max(ip, 1), hi * Pa))
    bD = (0.0, 1e-9) if yaw_no_d else (max(8.0, lo * Da), min(200.0, hi * Da))
    bR = (1.0, 1.0 + 1e-9) if yaw_no_d else (1.0, prob.goals.dmax_ratio_max)

    def fun(x):
        return prob.evaluate(x[0], ip * x[0], x[1], x[2])

    bounds = [bP, bD, bR]
    init = np.clip([P0, D0, max(1.0, dm0 / D0) if D0 > 0 else 1.0], [b[0] for b in bounds], [b[1] for b in bounds])
    # keep x0 strictly inside: scipy rescales x0 to [0, 1] and rejects float round-off at the edges
    eps = [max((b[1] - b[0]) * 1e-6, 1e-12) for b in bounds]
    init = np.minimum(np.maximum(init, [b[0] + e for b, e in zip(bounds, eps)]), [b[1] - e for b, e in zip(bounds, eps)])
    res = differential_evolution(fun, bounds, seed=seed, maxiter=maxiter, popsize=12, tol=1e-6, polish=True,
                                 x0=init, updating="deferred")
    P, D, R = res.x
    Pi, Di = int(round(P)), int(round(D))
    Ii = int(round(ip * P))
    Dmi = max(int(round(D * R)), Di) if Di > 0 else 0
    total, obj, pen, worst, nr, rows = prob.evaluate(Pi, Ii, Di, (Dmi / Di) if Di > 0 else 1.0, detail=True)
    feasible = not violations(rows, prob.goals, nr if prob.noise_Q is not None and prob.noise_ref is not None else None)
    return AxisResult(prob.axis, Pi, Ii, Di, Dmi, total, feasible, worst, nr, rows)


def apply_axis(tune: Tune, r: AxisResult) -> Tune:
    ax = AXES[r.axis]
    tune.set(f"p_{ax}", r.p).set(f"i_{ax}", r.i).set(f"d_{ax}", r.d).set(f"d_max_{ax}", r.d_max)
    return tune


def hf_filtering(tune: Tune, idn: Identification, loop_hz: float) -> tuple[float, float]:
    """RMS gain of the gyro chain and of the gyro+D-term chain over 1-3 kHz (hover op).

    Noise above the log Nyquist is inferred, not observed; this measures how far a candidate
    relies on that inference compared with tunes that have actually flown.
    """
    f = np.linspace(1000.0, min(3000.0, 0.45 * loop_hz), 200)
    ai = next(iter(idn.axes.values()))
    op = OperatingPoint(throttle=ai.op.throttle, motor_hz=ai.op.motor_hz, dyn_notch_hz=ai.op.dyn_notch_hz)
    C = controller_fr(tune, 0, op, f, idn.dt, loop_hz, time_scale=idn.time_scale)
    return float(np.sqrt(np.mean(np.abs(C.Fg) ** 2))), float(np.sqrt(np.mean(np.abs(C.Fg * C.Fd) ** 2)))


def score_tune(fl, idn, nm, tune, goals, noise_ref, axes=(0, 1, 2), maxiter=40, seed=0, hf_ref=None, anchor=None):
    results = {}
    total = 0.0
    if hf_ref is not None:
        g, d = hf_filtering(tune, idn, fl.loop_hz)
        excess = max(g / hf_ref[0], d / hf_ref[1]) - goals.hf_extrapolation
        if excess > 0:  # weaker HF filtering than any proven tune: do not trust the noise model there
            total += 100.0 * excess
    for axis in axes:
        cases = build_cases(fl, idn, tune, axis, goals, search=True)
        prob = AxisProblem(tune, axis, cases, idn, fl.loop_hz, nm, goals, noise_ref.get(axis) if noise_ref else None)
        if anchor is not None:
            prob.anchor = (anchor.i(f"p_{AXES[axis]}"), anchor.i(f"d_{AXES[axis]}"))
        r = optimize_axis(prob, tune, maxiter=maxiter, seed=seed)
        results[axis] = r
        total += r.objective * (0.5 if axis == 2 else 1.0)
    return total, results


def reference_noise(nm: NoiseModel, tune: Tune | list[Tune], loop_hz: float, idn: Identification) -> dict[int, np.ndarray]:
    """Motor-noise RMS per band and axis of known-safe tune(s) (budget reference).

    With several tunes (e.g. the logged tune plus another tune the pilot flew with cool
    motors) the per-band maximum is used: that noise level is proven acceptable.
    """
    tunes = tune if isinstance(tune, list) else [tune]
    out: dict[int, np.ndarray] = {}
    for axis in range(3):
        vals = []
        for t in tunes:
            prob = AxisProblem(t, axis, [], idn, loop_hz, nm, Goals(), None)
            vals.append(prob.noise(t.kp(axis), t.kd(axis)))
        out[axis] = np.max(np.array(vals), axis=0)
    return out


# ------------------------------------------------------------------------ global search
def candidate_moves(t: Tune) -> list[tuple[str, dict]]:
    """Discrete neighbourhood of global settings (coordinate descent). TPA first: it fixes
    high-throttle margins that otherwise dominate every other comparison."""
    moves: list[tuple[str, dict]] = []
    for rate, bp in ((0, 1350), (20, 1500), (35, 1450), (45, 1400), (65, 1350), (30, 1600)):
        if (rate, bp) != (t.i("tpa_rate"), t.i("tpa_breakpoint")):
            moves.append((f"tpa {rate}@{bp}", {"tpa_rate": rate, "tpa_breakpoint": bp}))
    if t.s("tpa_mode") == "D":
        moves.append(("tpa_mode PD", {"tpa_mode": "PD"}))
    else:
        moves.append(("tpa_mode D", {"tpa_mode": "D"}))
    for tl in (0, 20, 40, 60):
        if tl != t.i("thrust_linear"):
            moves.append((f"thrust_linear {tl}", {"thrust_linear": tl}))
    g1min = t.i("gyro_lpf1_dyn_min_hz")
    g1on = g1min > 0 or t.i("gyro_lpf1_static_hz") > 0
    if g1on:
        moves.append(("gyro_lpf1 off", {"gyro_lpf1_dyn_min_hz": 0, "gyro_lpf1_static_hz": 0}))
        for fac in (0.8, 1.25, 1.5):
            mn = int(round(max(g1min, t.i("gyro_lpf1_static_hz")) * fac / 5) * 5)
            moves.append((f"gyro_lpf1 x{fac}", {"gyro_lpf1_dyn_min_hz": mn, "gyro_lpf1_dyn_max_hz": 2 * mn, "gyro_lpf1_static_hz": mn}))
    else:
        for mn in (250, 350, 450):
            moves.append((f"gyro_lpf1 dyn {mn}", {"gyro_lpf1_dyn_min_hz": mn, "gyro_lpf1_dyn_max_hz": 2 * mn, "gyro_lpf1_static_hz": mn, "gyro_lpf1_type": "PT1"}))
    g2 = t.i("gyro_lpf2_static_hz")
    for hz in (0, int(g2 * 0.8), int(g2 * 1.3), int(g2 * 1.6)):
        if hz != g2 and (hz == 0 or 200 <= hz <= 1000):
            moves.append((f"gyro_lpf2 {hz}", {"gyro_lpf2_static_hz": hz}))
    d1 = t.i("dterm_lpf1_dyn_min_hz") or t.i("dterm_lpf1_static_hz")
    for fac in (0.8, 0.9, 1.15, 1.3):
        mn = int(round(d1 * fac / 5) * 5)
        moves.append((f"dterm_lpf1 x{fac}", {"dterm_lpf1_dyn_min_hz": mn, "dterm_lpf1_dyn_max_hz": 2 * mn, "dterm_lpf1_static_hz": mn}))
    for typ in ("PT1", "PT2", "PT3"):
        if typ != t.s("dterm_lpf1_type"):
            moves.append((f"dterm_lpf1 {typ}", {"dterm_lpf1_type": typ}))
    d2 = t.i("dterm_lpf2_static_hz")
    for hz in (0, int(d2 * 0.8), int(d2 * 1.25), int(d2 * 1.6)):
        if hz != d2 and (hz == 0 or 80 <= hz <= 600):
            moves.append((f"dterm_lpf2 {hz}", {"dterm_lpf2_static_hz": hz}))
    if t.s("dterm_lpf2_type") != "PT2":
        moves.append(("dterm_lpf2 PT2", {"dterm_lpf2_type": "PT2"}))
    n = t.i("dyn_notch_count")
    if n > 0:
        moves.append(("dyn_notch off", {"dyn_notch_count": 0}))
        for mn in (150, 200, 250):
            if mn != t.i("dyn_notch_min_hz"):
                moves.append((f"dyn_notch_min {mn}", {"dyn_notch_min_hz": mn}))
        for q in (250, 400, 500):
            if q != t.i("dyn_notch_q"):
                moves.append((f"dyn_notch_q {q}", {"dyn_notch_q": q}))
    else:
        moves.append(("dyn_notch 1", {"dyn_notch_count": 1, "dyn_notch_min_hz": 150, "dyn_notch_q": 350}))
    for q in (300, 400, 650, 800):
        if q != t.i("rpm_filter_q"):
            moves.append((f"rpm_q {q}", {"rpm_filter_q": q}))
    # compound D-term archetypes (a single lpf1-off move is always worse on its own)
    for typ, hz in (("PT3", 100), ("PT3", 120), ("PT3", 150), ("PT2", 110), ("PT2", 140)):
        moves.append((f"dterm lpf1 off + lpf2 {typ} {hz}", {"dterm_lpf1_dyn_min_hz": 0, "dterm_lpf1_static_hz": 0,
                                                            "dterm_lpf2_type": typ, "dterm_lpf2_static_hz": hz}))
    for typ, mn in (("PT1", 100), ("PT2", 90)):
        moves.append((f"dterm lpf1 {typ} dyn {mn} + lpf2 PT1 {2*mn+50}", {"dterm_lpf1_type": typ, "dterm_lpf1_dyn_min_hz": mn,
                      "dterm_lpf1_dyn_max_hz": 2 * mn, "dterm_lpf1_static_hz": mn, "dterm_lpf2_type": "PT1",
                      "dterm_lpf2_static_hz": 2 * mn + 50}))
    # compound gyro archetypes: RPM-filter-first (lpf1 off, single lpf2)
    for hz in (300, 400, 500, 750):
        moves.append((f"gyro lpf1 off + lpf2 {hz}", {"gyro_lpf1_dyn_min_hz": 0, "gyro_lpf1_static_hz": 0, "gyro_lpf2_static_hz": hz,
                                                    "gyro_lpf2_type": "PT1"}))
    return moves


def global_search(fl, idn, nm, tune0: Tune, goals: Goals, passes: int = 2, maxiter: int = 30,
                  log=print, fixed: dict | None = None, safe_tunes: list[Tune] | None = None,
                  noise_ref: dict | None = None, hf_ref: tuple[float, float] | None = None,
                  verbose: bool = False, anchor: Tune | None = None) -> SearchResult:
    if noise_ref is None and nm is not None:
        noise_ref = reference_noise(nm, [tune0] + list(safe_tunes or []), fl.loop_hz, idn)
    if hf_ref is None:
        hf = [hf_filtering(t, idn, fl.loop_hz) for t in [tune0] + list(safe_tunes or [])]
        hf_ref = (max(h[0] for h in hf), max(h[1] for h in hf))
    tune = tune0.copy()
    if fixed:
        tune.update(**fixed)
    anchor = anchor or tune0
    best_score, best_axes = score_tune(fl, idn, nm, tune, goals, noise_ref, maxiter=maxiter, hf_ref=hf_ref, anchor=anchor)
    for r in best_axes.values():
        apply_axis(tune, r)
    history = [("start", best_score)]
    log(f"start score {best_score:.3f}")
    for p in range(passes):
        improved = False
        for name, mv in candidate_moves(tune):
            cand = tune.copy().update(**mv)
            if all(str(cand.values.get(k)) == str(tune.values.get(k)) for k in mv):
                continue  # no-op move
            sc, axes = score_tune(fl, idn, nm, cand, goals, noise_ref, maxiter=maxiter, hf_ref=hf_ref, anchor=anchor)
            feas = all(a.feasible for a in axes.values())
            tag = "" if feas else " (constraints violated)"
            # the score already contains constraint penalties, so infeasible candidates can
            # still be accepted when they reduce violations (e.g. starting from an unsafe tune)
            if sc < best_score - 1e-3:
                best_score, tune, best_axes = sc, cand, axes
                for r in axes.values():
                    apply_axis(tune, r)
                history.append((name, sc))
                improved = True
                log(f"  pass {p+1}: accept {name:22s} -> score {sc:.3f}{tag}")
            elif verbose:
                log(f"  pass {p+1}: reject {name:22s}    score {sc:.3f}{tag}")
        if not improved:
            break
    return SearchResult(tune, best_axes, best_score, history)


FILTER_KEYS = (
    "gyro_lpf1_type", "gyro_lpf1_static_hz", "gyro_lpf1_dyn_min_hz", "gyro_lpf1_dyn_max_hz", "gyro_lpf1_dyn_expo",
    "gyro_lpf2_type", "gyro_lpf2_static_hz", "dterm_lpf1_type", "dterm_lpf1_static_hz", "dterm_lpf1_dyn_min_hz",
    "dterm_lpf1_dyn_max_hz", "dterm_lpf1_dyn_expo", "dterm_lpf2_type", "dterm_lpf2_static_hz", "dyn_notch_count",
    "dyn_notch_q", "dyn_notch_min_hz", "dyn_notch_max_hz", "rpm_filter_q", "tpa_mode",
    "tpa_rate", "tpa_breakpoint", "yaw_lowpass_hz",
)


def multi_start_search(fl, idn, nm, tune0: Tune, goals: Goals, seeds: dict[str, dict], passes: int = 2,
                       maxiter: int = 25, log=print, safe_tunes: list[Tune] | None = None) -> tuple[SearchResult, dict]:
    """Run the coordinate-descent search from several filter/TPA seeds; keep the best."""
    noise_ref = reference_noise(nm, [tune0] + list(safe_tunes or []), fl.loop_hz, idn) if nm is not None else None
    hf = [hf_filtering(t, idn, fl.loop_hz) for t in [tune0] + list(safe_tunes or [])]
    hf_ref = (max(h[0] for h in hf), max(h[1] for h in hf))
    log(f"HF filtering reference (1-3 kHz gain): gyro {hf_ref[0]:.3f}, gyro+dterm {hf_ref[1]:.4f}")
    results = {}
    best = None
    for name, seed in seeds.items():
        log(f"=== seed: {name}")
        start = tune0.copy().update(**seed)
        r = global_search(fl, idn, nm, start, goals, passes=passes, maxiter=maxiter, log=log, noise_ref=noise_ref,
                          hf_ref=hf_ref, anchor=tune0)
        results[name] = r
        log(f"=== seed {name}: final score {r.score:.3f}")
        if best is None or r.score < best.score:
            best = r
    return best, results


def case_limits(c, g: Goals) -> dict:
    """PM/GM/Ms limits of a case: the design goals, or the case's own override."""
    base = {"pm": g.pm_min_robust if c.robust else g.pm_min, "gm": g.gm_min_robust_db if c.robust else g.gm_min_db,
            "ms": g.ms_max_robust if c.robust else g.ms_max}
    return {**base, **(c.limits or {})}


def _below_floor(r: dict, pm: float, robust: bool) -> bool:
    from .targets import SAFETY_FLOOR as F

    sfx = "_robust" if robust else ""
    return (pm < F["pm_min" + sfx] - 0.5 or r["gm_db"] < F[("gm_min_robust_db" if robust else "gm_min_db")] - 0.2
            or r["ms"] > F["ms_max" + sfx] + 0.02)


def violations(rows: list[dict], goals: Goals, noise_ratio: float | None = None) -> list[str]:
    """Human-readable list of violated constraints for one axis (empty = all met)."""
    out = []
    for r in rows:
        rob = r.get("robust", False)
        lim = r.get("limits") or {}
        pm_min = lim.get("pm", goals.pm_min_robust if rob else goals.pm_min)
        gm_min = lim.get("gm", goals.gm_min_robust_db if rob else goals.gm_min_db)
        ms_max = lim.get("ms", goals.ms_max_robust if rob else goals.ms_max)
        pm = r.get("pm_eff", r["pm"])
        bad = []
        if pm < pm_min - 0.5:
            bad.append(f"PM {pm:.1f}°<{round(pm_min, 1):g}°")
        if r["gm_db"] < gm_min - 0.2:
            bad.append(f"GM {r['gm_db']:.1f}<{round(gm_min, 1):g} dB")
        if r["ms"] > ms_max + 0.02:
            bad.append(f"Ms {r['ms']:.2f}>{round(ms_max, 2):g}")
        if bad:
            tag = " (limit = flown tune)" if r.get("relaxed") else ""
            if not r["case"].startswith("idle") and _below_floor(r, pm, rob):
                tag += " - BELOW THE SAFETY FLOOR"
            out.append(f"{r['case']}: " + ", ".join(bad) + tag)
    if noise_ratio is not None and noise_ratio > goals.noise_budget + 1e-6:
        out.append(f"motor noise {noise_ratio:.2f}x proven-safe level (limit {goals.noise_budget:.2f}x)")
    return out
