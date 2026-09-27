"""Fast, agent-facing tuning tools on a cached analysis (the `bftune` workbench).

The numerics (decoding, plant identification, noise model, margins, step simulation) live in
scripts; the *decisions* are made by the tuning engineer (a person or an agent) who iterates

    hypothesis -> edit candidate file -> assess / sweep -> adjust

Every call loads `analysis.pkl` (identified plant, noise model, flight summary, proven-safe tunes)
and answers in about a second. Whatever is finally emitted must pass the same deterministic
verdict gate.

Candidate files are plain Betaflight CLI text (`set key = value`), applied on top of the logged
tune. An inline comment after a value is kept as the reason for that change:

    set d_roll = 34   # D limited by the noise budget at 35 % throttle
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .flight import AXES
from .model.controller import OperatingPoint
from .model.params import Tune, _load_settings_db, thrust_linear_slope
from .model.plant import Plant
from .optimize.search import (
    AxisProblem,
    Goals,
    build_cases,
    hf_filtering,
    optimize_axis,
    output_limit_ratio,
    reference_noise,
    violations,
)
from .pipeline import Analysis, drop_inert_changes, load_analysis

# Settings the workbench reasons about, grouped for the candidate file.
GROUPS = {
    "PIDs (profile)": [f"{t}_{a}" for a in AXES for t in ("p", "i", "d", "d_max", "f")],
    "D-max / TPA / thrust (profile)": ["d_max_gain", "d_max_advance", "tpa_mode", "tpa_rate", "tpa_breakpoint",
                                        "thrust_linear", "dyn_idle_min_rpm"],
    "I-term / anti-gravity (profile)": ["iterm_relax", "iterm_relax_type", "iterm_relax_cutoff", "anti_gravity_gain"],
    "Feedforward / RC (profile + master)": ["feedforward_boost", "feedforward_smooth_factor", "feedforward_jitter_factor",
                                            "feedforward_averaging", "feedforward_max_rate_limit",
                                            "feedforward_yaw_hold_gain", "feedforward_yaw_hold_time",
                                            "rc_smoothing_auto_factor"],
    "Gyro filters (master)": ["gyro_lpf1_type", "gyro_lpf1_static_hz", "gyro_lpf1_dyn_min_hz", "gyro_lpf1_dyn_max_hz",
                              "gyro_lpf2_type", "gyro_lpf2_static_hz", "dyn_notch_count", "dyn_notch_q",
                              "dyn_notch_min_hz", "dyn_notch_max_hz", "rpm_filter_harmonics", "rpm_filter_weights",
                              "rpm_filter_q", "rpm_filter_min_hz"],
    "D-term filters (profile)": ["dterm_lpf1_type", "dterm_lpf1_static_hz", "dterm_lpf1_dyn_min_hz",
                                 "dterm_lpf1_dyn_max_hz", "dterm_lpf2_type", "dterm_lpf2_static_hz", "yaw_lowpass_hz"],
}

_SET_LINE = re.compile(r"^\s*set\s+(\S+)\s*=\s*([^#]*?)\s*(?:#\s*(.*))?$")


_LPF_OFF = {
    "gyro_lpf1_type": ("gyro_lpf1_static_hz", "gyro_lpf1_dyn_min_hz"),
    "gyro_lpf2_type": ("gyro_lpf2_static_hz",),
    "dterm_lpf1_type": ("dterm_lpf1_static_hz", "dterm_lpf1_dyn_min_hz"),
    "dterm_lpf2_type": ("dterm_lpf2_static_hz",),
}


def apply_setting(t: Tune, key: str, value) -> Tune:
    """Set a value; `<lpf>_type = OFF` is a convenience that zeroes that lowpass's cutoffs
    (Betaflight has no OFF type: a lowpass is disabled by cutoff 0)."""
    if key in _LPF_OFF and str(value).upper() == "OFF":
        for k in _LPF_OFF[key]:
            t.values[k] = "0"
        return t
    t.values[key] = str(value)
    return t


def parse_candidate(base: Tune, text: str) -> tuple[Tune, dict[str, str]]:
    """Apply `set` lines on top of `base`; returns (tune, {key: inline reason}).
    `text` may also be the path of a candidate file (API convenience; see also Workbench.load)."""
    if isinstance(base, (str, Path)) and isinstance(text, Tune):  # tolerate swapped arguments
        base, text = text, base
    if "\n" not in str(text) and "set " not in str(text) and Path(str(text)).is_file():
        text = Path(str(text)).read_text(encoding="utf-8")
    t = base.copy()
    reasons: dict[str, str] = {}
    for line in text.splitlines():
        m = _SET_LINE.match(line)
        if not m:
            continue
        k, v, why = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
        apply_setting(t, k, v)
        if why:
            if k in _LPF_OFF and v.upper() == "OFF":  # the shortcut changes the cutoffs: the reason goes there
                for kk in _LPF_OFF[k]:
                    reasons[kk] = why
            else:
                reasons[k] = why
    return t, reasons


@dataclass
class Workbench:
    out: Path
    style: str | None = None  # None: from targets.json, else freestyle
    noise_budget: float | None = None  # None: from targets.json / style default
    an: Analysis = field(init=False)
    goals: Goals = field(init=False)

    def __post_init__(self):
        from .optimize.targets import build_goals, load_targets

        self.out = Path(self.out)
        self.an = load_analysis(self.out)
        if getattr(self.an, "summary", None) is None:
            raise SystemExit("analysis.pkl is from an older bftune version: re-run `bftune analyze`")
        self.src = self.an.summary
        self.idn = self.an.idn
        self.targets = load_targets(self.out)
        self.profile = (getattr(self.an, "extra", None) or {}).get("profile") or {}
        unc = dict(getattr(self.idn, "uncertainty", None) or {"k_hi": 1.10, "k_lo": 0.88, "dT": 0.0003})
        unc.setdefault("source", "convention")
        self._unc = unc
        self.goals, self.target_sources = build_goals(self.style, None, self.targets.get("overrides"), self.noise_budget,
                                                      self.profile, unc, self.an.tune)
        self.style = self.goals.style
        safe = [t for _, t in self.an.safe]
        self.noise_ref = reference_noise(self.an.nm, [self.an.tune] + safe, self.src.loop_hz, self.idn) if self.an.nm.bands else None
        hf = [hf_filtering(t, self.idn, self.src.loop_hz) for t in [self.an.tune] + safe]
        self.hf_ref = (max(h[0] for h in hf), max(h[1] for h in hf))
        # frequency bands scale with the quad: derive them from the flown tune's hover crossover
        fcs = [self._flown_rows(a).get("hover/d", {}).get("fc") for a in self.idn.axes if a < 2]
        fcs = [x for x in fcs if x and x == x]
        self.flown_hover_fc = float(np.mean(fcs)) if fcs else None
        if self.flown_hover_fc:
            self.goals, self.target_sources = build_goals(self.style, self.flown_hover_fc,
                                                          self.targets.get("overrides"), self.noise_budget,
                                                          self.profile, self._unc, self.an.tune)

    @property
    def stale(self) -> str | None:
        """A warning when the analysis was built by an older bftune (missing measurements, other defaults)."""
        from . import __version__

        built = (getattr(self.an, "extra", None) or {}).get("bftune")
        if built and built.split(".")[:2] == __version__.split(".")[:2]:
            return None
        return (f"this analysis was built by bftune {built or 'older than 0.8'} (now {__version__}): re-run `bftune analyze` "
                "for the flight profile, the as-flown step and the data-derived targets; defaults may have changed")

    # ----------------------------------------------------------------- the pilot's inputs
    @property
    def rx_hz(self) -> float:
        """RC link rate: log header, else estimated from the log (profile), else the 250 Hz convention."""
        from .optimize.rules import rx_rate_hz

        v = self.src.header_int("rc_smoothing_rx_smoothed", 0)
        return float(v) if v > 0 else float(self.profile.get("rx_rate_hz") or rx_rate_hz(self.src))

    def stimuli(self, axis: int) -> dict:
        """The stick moves the step tests use: this pilot's own typical ("flick") and fast ("snap") moves measured in
        the log, else conventions (300 deg/s in 50 ms; 0.6 x max rate in 30 ms)."""
        from .analysis.profile import CONVENTION_FLICK, CONVENTION_SNAP_RAMP_MS
        from .optimize.rules import max_rate

        m = (self.profile.get("stick_moves") or {}).get(AXES[axis])
        if m:
            return {"flick": (float(m["flick"]["deg_s"]), m["flick"]["ramp_ms"] / 1000.0),
                    "snap": (float(m["snap"]["deg_s"]), m["snap"]["ramp_ms"] / 1000.0),
                    "source": f"this pilot's stick moves in the log ({m['moves']} moves)"}
        return {"flick": (CONVENTION_FLICK["deg_s"], CONVENTION_FLICK["ramp_ms"] / 1000.0),
                "snap": (min(600.0, 0.6 * max_rate(self.src, axis)), CONVENTION_SNAP_RAMP_MS / 1000.0),
                "source": "convention (no stick moves measured)"}

    @property
    def peak_max(self) -> dict:
        """As-flown step peak target per axis [%]: the agent's override, else the flown tune's own peak (the model's
        replay of it, so model and target share the model's bias)."""
        if self.goals.peak_max is not None:
            return {AXES[a]: float(v) for a, v in enumerate(self.goals.peak_max)}
        if not hasattr(self, "_flown_peak"):
            self._flown_peak = {}
            for a in self.idn.axes:
                r = self.model_step(self.logged, a)
                if r is not None:
                    self._flown_peak[AXES[a]] = round(float(r["overshoot_pct"]), 1)
            if self._flown_peak and self.target_sources.get("peak_max") in (None, "convention"):
                self.target_sources["peak_max"] = "from the flown tune (as-flown peak)"
        return self._flown_peak

    # ----------------------------------------------------------------- tunes
    @property
    def logged(self) -> Tune:
        return self.an.tune

    @property
    def on_quad(self) -> Tune:
        """The tune on the quad now: the dump's, when it differs from the logged tune; else the logged tune.
        Candidates start from it and the delivered CLI/revert are relative to it. The model and the
        'no worse than flown' references stay with the logged tune (what the log measured)."""
        return getattr(self.an, "quad_tune", None) or self.an.tune

    def load(self, path: str | Path | None) -> tuple[Tune, dict[str, str]]:
        if path is None:
            return self.on_quad.copy(), {}
        return parse_candidate(self.on_quad, Path(path).read_text(encoding="utf-8"))

    def write_candidate(self, path: Path, tune: Tune | None = None, reasons: dict | None = None) -> Path:
        from .coverage import FEATURES

        t = tune or self.on_quad
        lines = [
            f"# bftune candidate for {self.an.craft or 'craft'} (Betaflight {self.an.firmware or '?'})",
            "# Edit values; keep a short reason after '#' for every change (it goes into the report).",
            "# Evaluate:  bftune assess -o OUT this_file   |   emit:  bftune emit -o OUT this_file",
        ]
        if getattr(self.an, "quad_tune", None) is not None:
            lines.append("# NOTE: starts from the tune on the quad now (the dump), which differs from the logged tune.")
        groups = dict(GROUPS)
        listed = {k for g in groups.values() for k in g}
        groups["Other flight-behaviour settings"] = [k for f in FEATURES for k in f.keys if k not in listed]
        listed |= set(groups["Other flight-behaviour settings"])
        extra = [k for k in t.values if k not in listed and str(t.values[k]) != str(self.on_quad.values.get(k))]
        if extra:
            groups["Other changed settings"] = extra
        for g, keys in groups.items():
            lines.append(f"\n# --- {g}")
            for k in keys:
                if k in t.values:
                    why = (reasons or {}).get(k)
                    lines.append(f"set {k} = {t.values[k]}" + (f"   # {why}" if why else ""))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    # ----------------------------------------------------------------- assessment
    def _plant_for(self, axis: int, tune: Tune) -> Plant:
        ai = self.idn.axes[axis]
        y = ai.op.throttle
        p = dict(ai.plant.params)
        p["K"] *= thrust_linear_slope(tune.i("thrust_linear"), y) / thrust_linear_slope(self.idn.thrust_linear, y)
        p["K"] *= output_limit_ratio(tune, self.idn)
        return Plant(ai.plant.structure, p)

    def step_metrics(self, tune: Tune, axis: int) -> dict:
        from .analysis.loop import step_metrics, step_response

        ai = self.idn.axes[axis]
        # during a stick move D rises toward d_max only if a D-max driver is enabled (pid.c)
        boost = 1.0 if (tune.i("d_max_gain") > 0 or tune.i("d_max_advance") > 0) else 0.0
        op = OperatingPoint(throttle=ai.op.throttle, motor_hz=ai.op.motor_hz, vbat=ai.op.vbat, d_boost=boost,
                            dyn_notch_hz=ai.op.dyn_notch_hz)
        pl = self._plant_for(axis, tune)
        rx = self.rx_hz
        st = self.stimuli(axis)
        out = {}
        for name in ("flick", "snap"):
            amp, ramp = st[name]
            m = step_metrics(step_response(tune, axis, pl, op, self.idn.dt, self.src.loop_hz, rx, amplitude=amp,
                                           ramp_s=ramp, duration=max(0.5, ramp + 0.3), time_scale=self.idn.time_scale))
            out[name] = {k: round(float(m[k]), 2) for k in ("tracking_lag_ms", "stick_lag_ms", "overshoot_pct", "undershoot_pct",
                                                            "settle_5pct_ms")}
        r = self.model_step(tune, axis)
        if r is not None:
            out["as_flown"] = {"delay_50_ms": round(r["delay_50_ms"], 2), "peak_pct": round(r["overshoot_pct"], 1),
                               "dip_pct": round(r["undershoot_pct"], 1)}
        return out

    def model_step(self, tune: Tune, axis: int, relax_i: bool = False) -> dict | None:
        """The step response "as flown" for any tune: the model (at the hover operating point) replays the pilot's
        logged setpoint - recorded after RC smoothing (blackbox.c: pidGetPreviousSetpoint) - through FF and the loop,
        and the same windows and Wiener estimator as the measurement turn it into a step. For the flown tune it
        is directly comparable with the measured step (estimator bias cancels); for a candidate it predicts what
        the next log will show, with the pilot's real stick moves (analysis.logstep). The I-term stays in: iterm_relax
        only holds it during fast stick changes, and over whole flight windows it acts (on synthetic twins with known
        truth, dropping it predicted 0 % overshoot where the twin showed 25-40 %)."""
        from .analysis.logstep import step_estimate
        from .analysis.loop import jitter_attenuator, rc_path_fr
        from .model.controller import controller_fr

        rp = (getattr(self.an, "extra", None) or {}).get("replay")
        if not rp or not rp["starts"].get(axis):
            return None
        ai = self.idn.axes[axis]
        boost = 1.0 if (tune.i("d_max_gain") > 0 or tune.i("d_max_advance") > 0) else 0.0
        op = OperatingPoint(throttle=ai.op.throttle, motor_hz=ai.op.motor_hz, vbat=ai.op.vbat, d_boost=boost,
                            dyn_notch_hz=ai.op.dyn_notch_hz)
        sp = np.asarray(rp["setpoint"][:, axis], dtype=float)
        n = len(sp)
        f = np.fft.rfftfreq(n, 1.0 / rp["fs"])
        f[0] = 1e-6
        relaxed = relax_i and (tune.s("iterm_relax").startswith("RPY")
                               or (axis < 2 and tune.s("iterm_relax").startswith("RP")))
        C = controller_fr(tune, axis, op, f, self.idn.dt, self.src.loop_hz, time_scale=self.idn.time_scale,
                          include_i=not relaxed)
        G = self._plant_for(axis, tune).fr(f)
        H_sp0, _ = rc_path_fr(self.logged, f, self.rx_hz, self.idn.dt, axis)  # what smoothed the logged setpoint
        H_sp, H_ff = rc_path_fr(tune, f, self.rx_hz, self.idn.dt, axis)
        STICK = np.fft.rfft(sp - sp.mean()) / H_sp0
        ff = np.fft.irfft(tune.kf(axis) * H_ff * STICK, n)
        if "rc" in rp:  # jitter reduction replayed on the pilot's own stick changes (the log's FF is ~0.5-0.8x without)
            ff = ff * jitter_attenuator(np.asarray(rp["rc"][:, axis], float), rp["fs"], self.rx_hz,
                                        tune.i("feedforward_jitter_factor"))
        y = np.fft.irfft(G / (1 + G * C.Cy) * (C.Cr * H_sp * STICK + np.fft.rfft(ff)), n) + sp.mean()
        return step_estimate(sp, y, rp["starts"][axis], rp["fs"], fixed=True)

    def axis_problem(self, tune: Tune, axis: int, relax_idle: bool = True, search: bool = False) -> AxisProblem:
        cases = build_cases(self.src, self.idn, tune, axis, self.goals, search=search)
        if relax_idle:
            self._relax_idle_cases(axis, cases)
        prob = AxisProblem(tune, axis, cases, self.idn, self.src.loop_hz, self.an.nm, self.goals,
                           self.noise_ref.get(axis) if self.noise_ref else None)
        prob.anchor = (self.logged.i(f"p_{AXES[axis]}"), self.logged.i(f"d_{AXES[axis]}"))
        return prob

    def _flown_rows(self, axis: int) -> dict:
        """Per-case metrics of the tune that flew (the log's tune), with the plain design limits."""
        cache = self.__dict__.setdefault("_flown_rows_cache", {})
        if axis not in cache:
            t, ax = self.logged, AXES[axis]
            d = t.i(f"d_{ax}")
            prob = self.axis_problem(t, axis, relax_idle=False)
            rows = prob.evaluate(t.i(f"p_{ax}"), t.i(f"i_{ax}"), d, t.i(f"d_max_{ax}") / d if d > 0 else 1.0, detail=True)[5]
            cache[axis] = {r["case"]: r for r in rows}
        return cache[axis]

    def _relax_idle_cases(self, axis: int, cases: list) -> None:
        """Idle is where the model is least certain (motor lag extrapolated at very low rpm) and where some
        quads cannot reach the design limits at all. If the tune that flew already misses them at idle, the
        idle limit becomes 'no worse than the flown tune' (it flew, so it is acceptable)."""
        from .optimize.search import case_limits

        flown = self._flown_rows(axis)
        for c in cases:
            if not c.label.startswith("idle"):
                continue
            ref = flown.get(c.label) or flown.get(c.label.replace("/dmax", "/d"))
            if ref is None:
                continue
            lim = case_limits(c, self.goals)
            pm0 = ref.get("pm_eff", ref["pm"])
            if pm0 != pm0:  # no gain crossover at idle in the flown tune (almost no authority): PM can't be compared
                pm0 = -180.0
            if pm0 >= lim["pm"] and ref["gm_db"] >= lim["gm"] and ref["ms"] <= lim["ms"]:
                continue  # the flown tune meets the design limits here: keep them
            c.limits = {"pm": min(lim["pm"], pm0 - 2.0), "gm": min(lim["gm"], ref["gm_db"] - 0.5),
                        "ms": max(lim["ms"], ref["ms"] * 1.05 + 0.02)}

    def assess_axis(self, tune: Tune, axis: int, steps: bool = True) -> dict:
        ax = AXES[axis]
        prob = self.axis_problem(tune, axis)
        d = tune.i(f"d_{ax}")
        dm = tune.i(f"d_max_{ax}") / d if d > 0 else 1.0
        total, obj, pen, worst, nr, rows = prob.evaluate(tune.i(f"p_{ax}"), tune.i(f"i_{ax}"), d, dm, detail=True)
        byc = {r["case"]: r for r in rows}
        pick = lambda c, keys: {k: (None if byc.get(c, {}).get(k) is None else round(float(byc[c][k]), 2)) for k in keys}  # noqa: E731
        has_noise = prob.noise_Q is not None and bool(self.noise_ref)
        # the flown tune's own noise has flown: never force a cut below it (unless the budget was lowered
        # explicitly below 0.9, i.e. hot motors). The 0.9 margin guards going *above* what flew.
        viol = violations(rows, self.goals, None)
        noise_bands = None
        if has_noise:
            from .model.params import DTERM_SCALE, PTERM_SCALE

            noise_bands = (prob.noise(PTERM_SCALE * tune.i(f"p_{ax}"), DTERM_SCALE * d), np.asarray(self.noise_ref[axis]))
        if self.relative_gate:
            viol = self._relative_violations(axis, rows, viol)
        res = {
            "violations": viol,
            "objective_db": round(float(obj), 3),
            "hover": pick("hover/d", ("fc", "pm", "gm_db", "ms", "dm_ms", "bw_s", "ms_hz")),
            "idle": pick("idle/d", ("fc", "pm", "gm_db", "ms", "bw_s", "ms_hz")),
            "mid": pick("mid/d", ("fc", "pm", "gm_db", "ms")),
            "full": pick("full/d", ("fc", "pm", "gm_db", "ms")),
            "worst": {k: round(float(v), 2) for k, v in worst.items()},
            "worst_case": dict(getattr(prob, "worst_case", {})),
            "noise_vs_safe": round(float(nr), 3) if prob.noise_Q is not None and self.noise_ref else None,
            "coherent_to_hz": round(self.idn.axes[axis].coherent_to_hz, 1),
            "idle_limits_from_flown": any(r.get("relaxed") for r in rows),
            "_noise_bands": noise_bands,  # (candidate, reference) per throttle band: the verdict sums the axes
        }
        if steps:
            res["step"] = self.step_metrics(tune, axis)
        return res

    @staticmethod
    def _motor_noise(per: dict) -> float | None:
        """Noise at the motors, all axes together (each motor's command carries roll +- pitch +- yaw through the
        mixer; uncorrelated axes add in power), relative to the reference, worst throttle band."""
        nb = [e.get("_noise_bands") for e in per.values()]
        if len(nb) < 3 or any(x is None for x in nb):
            return None
        cand = np.sqrt(sum(c ** 2 for c, _ in nb))
        ref = np.sqrt(sum(r ** 2 for _, r in nb))
        return float(np.max(cand / np.maximum(ref, 1e-12)))

    def motor_noise_limit(self) -> float:
        """Allowed total motor noise (x reference): the budget, or the flown tune's own level if higher (it flew),
        unless the budget was lowered below 0.9 on purpose (warm/hot motors)."""
        b = self.goals.noise_budget
        if b < 0.9:
            return b
        if "_logged_total" not in self.__dict__:
            per = {AXES[a]: self.assess_axis(self.logged, a, steps=False) for a in self.idn.axes}
            self._logged_total = self._motor_noise(per) or 0.0
        return max(b, self._logged_total)

    def noise_limit(self, axis: int) -> float:
        """Allowed motor noise (x proven-safe level): the budget, or the flown tune's own level if higher
        (it flew, so no forced cut) - unless the budget was lowered below 0.9 on purpose (warm/hot motors)."""
        b = self.goals.noise_budget
        return max(b, self._logged_noise(axis)) if b >= 0.9 else b

    def _logged_noise(self, axis: int) -> float:
        cache = self.__dict__.setdefault("_logged_nr", {})
        if axis not in cache:
            t = self.logged
            ax = AXES[axis]
            d = t.i(f"d_{ax}")
            prob = self.axis_problem(t, axis)
            cache[axis] = float(prob.evaluate(t.i(f"p_{ax}"), t.i(f"i_{ax}"), d,
                                              t.i(f"d_max_{ax}") / d if d > 0 else 1.0, detail=True)[4])
        return cache[axis]

    @property
    def relative_gate(self) -> bool:
        """No chirp -> the plant gain is only known to about ±40 %: absolute margins are not trustworthy.
        The gate then becomes 'no worse than the tune that flew' (case by case, same model)."""
        return getattr(self.idn, "source", "chirp") != "chirp"

    def _relative_violations(self, axis: int, rows: list[dict], absolute: list[str]) -> list[str]:
        from .optimize.targets import SAFETY_FLOOR

        cache = self.__dict__.setdefault("_logged_rows", {})
        if axis not in cache:
            t = self.logged
            ax = AXES[axis]
            d = t.i(f"d_{ax}")
            prob = self.axis_problem(t, axis)
            cache[axis] = {r["case"]: r for r in prob.evaluate(t.i(f"p_{ax}"), t.i(f"i_{ax}"), d,
                                                               t.i(f"d_max_{ax}") / d if d > 0 else 1.0, detail=True)[5]}
        ref = cache[axis]
        failing = {v.split(":")[0] for v in absolute}
        out = [v for v in absolute if v.startswith("motor noise")]
        for r in rows:
            c = r["case"]
            if c not in failing:
                continue
            # a case the flown tune doesn't have (D-max or the dynamic notch newly enabled) is compared with the
            # flown tune's matching case without it: that is how the flown tune behaved there
            q = next((ref[k] for k in (c, c.replace("/dmax", "/d"), c.replace("/dn@min", ""),
                                        c.replace("/dmax", "/d").replace("/dn@min", "")) if k in ref), None)
            if q is None:  # nothing flown to compare with: the absolute limits stand
                out += [v + " (relative gate: no flown reference)" for v in absolute if v.split(":")[0] == c]
                continue
            bad = []
            pm, pm0 = r.get("pm_eff", r["pm"]), q.get("pm_eff", q["pm"])
            if pm < pm0 - 2.0:
                bad.append(f"PM {pm:.0f}° < flown {pm0:.0f}°")
            if r["gm_db"] < q["gm_db"] - 0.5:
                bad.append(f"GM {r['gm_db']:.1f} < flown {q['gm_db']:.1f} dB")
            if r["ms"] > q["ms"] * 1.05 + 0.02:
                bad.append(f"Ms {r['ms']:.2f} > flown {q['ms']:.2f}")
            dm, dm0 = r.get("dm_ms"), q.get("dm_ms")
            if dm is not None and dm0 is not None and dm == dm and dm0 == dm0 and dm < min(0.9 * dm0, SAFETY_FLOOR["dm_min_ms"]):
                bad.append(f"DM {dm:.2f} < flown {dm0:.2f} ms")
            if bad:
                out.append(f"{c}: " + ", ".join(bad) + " (relative gate: no chirp)")
        return out

    def lint(self, tune: Tune) -> list[str]:
        """Setting combinations the linear model can't score but real quads show (from flown logs)."""
        out = []
        floor = tune.i("dyn_idle_min_rpm") * 100 / 60.0
        low = self.src.idle_q.get(5, self.src.idle_hz(10))  # the lowest idle speeds (dives) must be notched too
        idle = max(low, floor)
        rmin = tune.i("rpm_filter_min_hz")
        if tune.i("rpm_filter_harmonics") > 0 and tune.s("dshot_bidir") != "OFF" and rmin > 0.95 * idle:
            out.append(f"rpm_filter_min_hz {rmin} is not below the lowest idle motor frequency with margin (idle p5 "
                       f"~{idle:.0f} Hz" + (" = the dyn idle floor" if floor > low else "")
                       + f"; the check is min_hz > 0.95 x idle = {0.95 * idle:.0f} Hz): the motor fundamental at the lowest "
                       "speeds is not notched. It can show up as a low-throttle "
                       f"error peak near {idle:.0f} Hz. If `diagnose` shows a motor line there, lower rpm_filter_min_hz to "
                       f"~{max(50, int(idle * 0.9 / 5) * 5)}; rpm_filter_fade_range_hz decides how deep the notch is just "
                       "above that floor. A notch near the idle crossover costs idle phase: `sweep rpm_filter_min_hz` and "
                       "`rpm_filter_fade_range_hz` show how much on this quad. If `diagnose` shows body motion, leave it.")
        bp = tune.i("tpa_breakpoint")
        hover_thr = float(np.mean([ai.op.throttle for ai in self.idn.axes.values()])) if self.idn.axes else 0.3
        if 1000 + 1000 * hover_thr > bp and tune.i("tpa_rate") > 0:
            out.append(f"tpa_breakpoint {bp} is below hover (~{1000 + 1000 * hover_thr:.0f}): TPA already cuts gains at hover. "
                       "Raising it puts full P/D back at hover (check hover Ms and noise with `sweep tpa_breakpoint`).")
        slider = {"simplified_pids_mode": ("p_", "i_", "d_", "d_max_", "f_"),
                  "simplified_dterm_filter": ("dterm_lpf",), "simplified_gyro_filter": ("gyro_lpf",)}
        for sk, prefixes in slider.items():
            if str(tune.values.get(sk, "OFF")).upper() not in ("OFF", "0"):
                touched = [k for k in tune.values if k.startswith(prefixes)
                           and str(tune.values[k]) != str(self.on_quad.values.get(k))]
                if touched:
                    out.append(f"{sk} is {tune.values[sk]} while {', '.join(sorted(touched)[:4])} are set explicitly: "
                               "the Configurator recomputes them from the slider when it saves (the model uses the "
                               f"explicit values). Set {sk} = OFF with explicit values (emit does).")
        if tune.i("d_max_roll") > tune.i("d_roll") and tune.i("d_max_gain") == 0 and tune.i("d_max_advance") == 0:
            out.append("d_max > d but d_max_gain and d_max_advance are 0: D-max never engages (set d_max = d or enable a driver).")
        return out

    def assess(self, tune: Tune, steps: bool = True, axes=None) -> dict:
        from .emit.cli import changed_keys
        from .model.params import validate

        full = axes is None
        axes = list(self.idn.axes) if full else axes
        per = {AXES[a]: self.assess_axis(tune, a, steps) for a in axes}
        motor_noise, noise_viol = self._motor_noise(per), []
        if motor_noise is not None:
            lim = self.motor_noise_limit()
            if motor_noise > lim + 1e-3:
                why = "the flown tune's level" if lim > self.goals.noise_budget else "budget"
                noise_viol.append(f"motor noise (all axes, at the motors) {motor_noise:.2f}x reference "
                                  f"(limit {lim:.2f}x: {why})")
        else:  # a subset of axes: judge each axis against the limit on its own (conservative)
            for ax, e in per.items():
                a = AXES.index(ax)
                if e["noise_vs_safe"] is not None and e["noise_vs_safe"] > self.noise_limit(a) + 1e-3:
                    e["violations"].append(f"motor noise {e['noise_vs_safe']:.2f}x reference (limit "
                                           f"{self.noise_limit(a):.2f}x, this axis alone)")
        for e in per.values():
            e.pop("_noise_bands", None)
        g, dchain = hf_filtering(tune, self.idn, self.src.loop_hz)
        notes = []
        if g > self.goals.hf_extrapolation * self.hf_ref[0] or dchain > self.goals.hf_extrapolation * self.hf_ref[1]:
            which = []
            if g > self.goals.hf_extrapolation * self.hf_ref[0]:
                which.append(f"gyro path {g / self.hf_ref[0]:.2f}x")
            if dchain > self.goals.hf_extrapolation * self.hf_ref[1]:
                which.append(f"gyro+D-term path {dchain / self.hf_ref[1]:.2f}x")
            notes.append("high-frequency filtering (1-3 kHz) is weaker than the least-filtered flown tune (logged + "
                         f"proven-safe) on the {', '.join(which)} (limit {self.goals.hf_extrapolation:.2f}x): the noise "
                         "model extrapolates there")
        for ax, e in per.items():
            fc = e["hover"]["fc"]
            msf = e["hover"].get("ms_hz")
            if msf and msf > e["coherent_to_hz"]:
                notes.append(f"{ax}: hover sensitivity peak at {msf:.0f} Hz lies beyond the identified band "
                             f"({e['coherent_to_hz']:.0f} Hz): its height relies on the plant model")
            if fc and fc > 0.8 * e["coherent_to_hz"]:
                notes.append(f"{ax}: hover crossover {fc:.0f} Hz is close to the end of the identified band "
                             f"({e['coherent_to_hz']:.0f} Hz): margins rely on the model extrapolation")
        notes += self.lint(tune)
        if steps:
            pk = self.peak_max
            for ax, e in per.items():
                af = e.get("step", {}).get("as_flown")
                lim = pk.get(ax)
                if af and lim is not None and af["peak_pct"] > lim + 1.0:
                    notes.append(f"{ax}: as-flown step peak +{af['peak_pct']:.0f} % is above the target +{lim:.0f} % "
                                 f"({self.target_sources.get('peak_max')}): more overshoot than the pilot flew with")
        relaxed = [ax for ax, e in per.items() if e.get("idle_limits_from_flown")]
        if relaxed:
            notes.append(f"idle limits on {', '.join(relaxed)} = the flown tune's idle margins (it flies, but misses the design "
                         "limits there; at very low rpm the model is least certain). Judge idle by improvement.")
        changed = changed_keys(self.logged, tune)
        problems = validate(tune, changed)  # every changed key: a typo or out-of-range value must never PASS
        db = _load_settings_db()
        problems += [f"{k}: a {db[k]['scope']}-scope setting, which bftune's CLI block can't write" for k in changed
                     if k in db and db[k].get("scope") not in ("master", "profile", "rateprofile")]
        unchecked = []
        if full:  # an axis without a model (no chirp on it, or its fit failed) can't be judged: don't change it
            for a in sorted(set(range(3)) - set(self.idn.axes)):
                keys = [k for k in changed if AXES[a] in k.split("_") and db.get(k, {}).get("scope") != "rateprofile"]
                if keys:
                    unchecked.append(f"{AXES[a]}: no model of this axis (no usable chirp or fit), so changes to "
                                     f"{', '.join(keys)} can't be checked: keep them as flown")
                else:
                    notes.append(f"{AXES[a]}: no model of this axis; shared settings (filters, TPA, idle) are not "
                                 "checked on it")
        verdict = ("PASS" if not any(e["violations"] for e in per.values()) and not problems and not noise_viol
                   and not unchecked else "FAIL")
        if self.stale:
            notes.insert(0, self.stale)
        if self.relative_gate:
            notes.insert(0, "NO CHIRP: plant gain uncertain (±40 %). Verdict = no worse than the flown tune in every "
                            "failing case (relative gate), not the absolute design margins. Keep changes small; fly a chirp.")
        overridden = {k: getattr(self.goals, k) for k, v in self.target_sources.items() if v in ("override", "command line")}
        return {"verdict": verdict, "gate": "relative" if self.relative_gate else "absolute",
                "targets": {"style": self.goals.style, "overrides": overridden},
                "axes": per, "notes": notes, "range_problems": problems, "unchecked": unchecked,
                "motor_noise": None if motor_noise is None else round(motor_noise, 3), "noise_violations": noise_viol,
                "hf_filtering": {"gyro": round(g, 4), "gyro_dterm": round(dchain, 5), "safe_max": [round(x, 5) for x in self.hf_ref]}}

    # ----------------------------------------------------------------- exploration
    def sweep(self, base: Tune, key: str, values: list, steps: bool = False) -> list[dict]:
        axes = None
        for a, ax in enumerate(AXES):
            if key.endswith(f"_{ax}"):
                axes = [a]
        rows = []
        for v in values:
            t = apply_setting(base.copy(), key, v)
            r = self.assess(t, steps=steps, axes=axes)
            rows.append({"value": v, "verdict": r["verdict"], "hf_beyond_flown": any("1-3 kHz" in n for n in r["notes"]),
                         "motor_noise": r.get("motor_noise"), "noise_violations": r.get("noise_violations", []),
                         **{ax: e for ax, e in r["axes"].items()}})
        return rows

    def grid(self, base: Tune, k1: str, v1: list, k2: str, v2: list, steps: bool = False) -> dict:
        axes = sorted({a for a, ax in enumerate(AXES) for k in (k1, k2) if k.endswith(f"_{ax}")}) or None
        cells = []
        for a in v1:
            for b in v2:
                t = apply_setting(apply_setting(base.copy(), k1, a), k2, b)
                r = self.assess(t, steps=steps, axes=axes)
                cells.append({k1: a, k2: b, "verdict": r["verdict"],
                              "hf_beyond_flown": any("1-3 kHz" in n for n in r["notes"]),
                              "axes": {ax: {"ok": not e["violations"], "hover_ms": e["hover"]["ms"],
                                            "hover_fc": e["hover"]["fc"], "worst_pm": e["worst"]["pm"],
                                            "noise": e["noise_vs_safe"], "obj": e["objective_db"],
                                            "as_flown": e.get("step", {}).get("as_flown"),
                                            "first_violation": (e["violations"] or [None])[0]}
                                       for ax, e in r["axes"].items()}})
        return {"keys": [k1, k2], "values": [v1, v2], "cells": cells}

    def suggest(self, base: Tune, axes: list[int], maxiter: int = 30) -> dict:
        """Per-axis P/I/D/d_max helper with all other settings fixed (a proposal, not a decision)."""
        from dataclasses import replace

        out = {}
        for a in axes:
            prob = self.axis_problem(base, a, search=True)
            if self.relative_gate:  # no chirp: stay close to the flown gains (skills: about ±15 %)
                prob.goals = replace(prob.goals, gain_range=(0.85, 1.15))
            if prob.noise_Q is not None and self.noise_ref:
                prob.noise_limit = self.noise_limit(a)
            r = optimize_axis(prob, base, maxiter=maxiter)
            # feasibility = the gate the delivery uses (every case incl. D-max and robust variants), not the search's own
            ax = AXES[a]
            t = base.copy().set(f"p_{ax}", r.p).set(f"i_{ax}", r.i).set(f"d_{ax}", r.d).set(f"d_max_{ax}", r.d_max)
            chk = self.assess_axis(t, a, steps=True)
            chk.pop("_noise_bands", None)
            if chk["noise_vs_safe"] is not None and chk["noise_vs_safe"] > self.noise_limit(a) + 1e-3:
                chk["violations"].append(f"motor noise {chk['noise_vs_safe']:.2f}x reference (limit "
                                         f"{self.noise_limit(a):.2f}x on this axis)")
            r.feasible = not chk["violations"]
            s0 = self.step_metrics(base, a)
            af0, af1 = s0.get("as_flown"), chk.get("step", {}).get("as_flown")
            out[AXES[a]] = {"p": r.p, "i": r.i, "d": r.d, "d_max": r.d_max, "feasible": r.feasible,
                            "noise_limited": bool(prob.noise_Q is not None and self.noise_ref
                                                  and r.noise_ratio >= 0.95 * self.noise_limit(a)),
                            "violations": chk["violations"][:3],
                            "as_flown_50_ms": [af0["delay_50_ms"], af1["delay_50_ms"]] if af0 and af1 else None,
                            "as_flown_peak_pct": [af0["peak_pct"], af1["peak_pct"]] if af0 and af1 else None,
                            "noise_vs_safe": round(float(r.noise_ratio), 3), "worst": {k: round(float(v), 2) for k, v in r.worst.items()},
                            **({} if r.feasible else {"note": "does not pass every case; least-violating point found"}),
                            **({"range": "±15 % of the flown P/D (no chirp)"} if self.relative_gate else {})}
        return out

    def ff_table(self, base: Tune, axis: int, values: list[int]) -> list[dict]:
        rows = []
        for f in values:
            t = base.copy().set(f"f_{AXES[axis]}", f)
            rows.append({"f": f, **self.step_metrics(t, axis)})
        return rows

    def noise_report(self, tune: Tune | None = None) -> dict:
        """Per throttle band: measured noise, fit quality, non-RPM peaks, candidate vs budget."""

        nm = self.an.nm
        rep = {"bands": [], "log_rate_hz": self.src.fs}
        cand = None
        if tune is not None and nm.bands:
            cand = {a: self.axis_problem(tune, a).noise(tune.kp(a), tune.kd(a)) for a in self.idn.axes}
        for i, b in enumerate(nm.bands):
            f = b.f_obs
            peaks = []
            lf = b.line_free if b.line_free is not None else np.ones_like(f, bool)
            for a in range(3):
                p = b.psd_unfilt[a]
                m = lf & (f > getattr(nm, "motion_hz", 70.0))
                if m.sum() < 5:
                    continue
                floor = np.median(p[m])
                for k in np.flatnonzero(m[1:-1]) + 1:
                    if p[k] > p[k - 1] and p[k] >= p[k + 1] and p[k] > 6.3 * floor:  # > 8 dB above floor
                        peaks.append({"axis": AXES[a], "apparent_hz": round(float(f[k]), 1),
                                      "db_above_floor": round(float(10 * np.log10(p[k] / floor)), 1)})
            entry = {
                "throttle": round(b.throttle, 3),
                "motor_hz": round(float(np.mean(b.motor_hz)), 1),
                "measured_dterm_rms": np.sqrt(b.var_d).round(2).tolist(),
                "measured_gyro_rms": np.sqrt(b.var_filt).round(3).tolist(),
                "fit_error": b.calib_err.round(2).tolist(),
                "non_rpm_peaks": sorted(peaks, key=lambda x: -x["db_above_floor"])[:6],
            }
            if self.noise_ref:
                entry["safe_level"] = {AXES[a]: round(float(self.noise_ref[a][i]), 3) for a in self.idn.axes}
            if cand:
                entry["candidate"] = {AXES[a]: round(float(cand[a][i]), 3) for a in cand}
            rep["bands"].append(entry)
        # persistence: the same apparent frequency (±12 Hz) in >= 2 throttle bands -> likely structural
        allp = [(bi, p) for bi, b in enumerate(rep["bands"]) for p in b["non_rpm_peaks"]]
        for bi, p in allp:
            p["persistent"] = any(bj != bi and abs(q["apparent_hz"] - p["apparent_hz"]) <= 12 for bj, q in allp)
        rep["persistent_peaks_hz"] = sorted({p["apparent_hz"] for _, p in allp if p["persistent"]})
        rep["note"] = ("Non-RPM peaks are in the (possibly aliased) log spectrum; 'apparent_hz' may be a fold of "
                       f"content above {self.src.fs/2:.0f} Hz. Peaks that persist across throttle bands at the same "
                       "frequency suggest a frame resonance (dyn notch); none -> RPM filter alone may suffice. Peaks that move with "
                       "throttle but are not RPM lines are usually aliased higher motor harmonics or motor-line sidebands.")
        return rep

    # ----------------------------------------------------------------- deliverable
    def emit(self, tune: Tune, reasons: dict[str, str] | None = None, extra: dict | None = None, log=print,
             dest: Path | None = None, profile: int | None = None, experiment: str | None = None,
             full: bool = False) -> dict:
        from .emit.cli import cli_block, diff_table
        from .noise.model import predict as predict_noise
        from .report import plots as P
        from .report.tune import write_report

        dest = Path(dest) if dest else self.out
        dest.mkdir(parents=True, exist_ok=True)
        old = self.on_quad
        new = drop_inert_changes(old, tune)
        for k in ("simplified_pids_mode", "simplified_dterm_filter", "simplified_gyro_filter"):
            new.values[k] = "OFF"
        reasons = dict(reasons or {})
        for k in ("simplified_pids_mode", "simplified_dterm_filter", "simplified_gyro_filter"):
            reasons.setdefault(k, "keep explicit values (Configurator sliders would overwrite them)")
        ass = {"logged": self.assess(self.logged), "new": self.assess(new)}
        if old is not self.logged:
            ass["on_quad"] = self.assess(old)
        for name, t in self.an.safe:
            ass[name] = self.assess(t)
        verdict = ass["new"]["verdict"]
        viol = {ax: e["violations"] for ax, e in ass["new"]["axes"].items()}
        if ass["new"].get("noise_violations"):
            viol["motors"] = ass["new"]["noise_violations"]
        comment = [f"style: {self.style}; model-predicted margins and noise in report.html"]
        if experiment:
            comment.append(f"EXPERIMENT: {experiment}")
            comment.append("Fly it with the checks in report.html; go back with revert_cli.txt if anything is off.")
        if verdict == "FAIL":
            comment.append("WARNING: the model predicts violated constraints for this tune:")
            comment += [f"  {ax}: {v}" for ax, vs in viol.items() for v in vs] + [f"  {p}" for p in ass["new"]["range_problems"]]
            comment.append("Do NOT fly without reviewing report.html (see 'Verdict').")
        if not self.an.nm.bands:
            comment.append("WARNING: no noise model - motor noise unchecked")
        if profile is None:  # known only from a real dump (or the agent: --profile from the project's older dumps)
            known = getattr(self.src.cfg, "profile_known", None)
            if known is None:  # analysis from before 0.8: a real dump is the only way the source says "dump"
                known = "dump" in self.src.cfg.source and "log header" not in self.src.cfg.source
            profile = self.src.cfg.active_profile if known else None
        full_keys = None
        if full:  # the tune on the quad is unknown: pin every tuning setting (never the pilot's rates)
            from .coverage import FEATURES

            rates = {k for ft in FEATURES if ft.group.startswith("Rates") for k in ft.keys}
            full_keys = [k for k in new.values if k in tuning_keys() and k not in rates and str(new.values[k]) != ""]
            comment.append("FULL tune: every tuning setting is written, because the tune on the quad was unknown "
                           "(rates untouched).")
        apply_txt, revert_txt, problems = cli_block(old, new, profile=profile, craft=self.an.craft,
                                                    firmware=self.an.firmware, extra_comment=comment, keys=full_keys)
        bad = [p for p in problems if not p.startswith("revert:")]
        if bad:  # the CLI would reject or drop these lines and apply the rest: never write such a block
            raise SystemExit("bftune emit: refused, the tune has invalid settings (nothing written):\n  "
                             + "\n  ".join(bad))
        (dest / "tune_cli.txt").write_text(apply_txt, encoding="utf-8")
        (dest / "revert_cli.txt").write_text(revert_txt, encoding="utf-8")
        missing = [k for k, _, _, r in diff_table(old, new, reasons) if not r]
        rows = diff_table(old, new, reasons)
        plots = {"loop": P.loop_compare(self.idn, self.src.loop_hz, old, new, dest / "loop_compare.png").name}
        sp, _ = P.step_compare(self.idn, self.src.loop_hz, self.rx_hz, old, new, dest / "step_compare.png",
                               {a: self.stimuli(a)["flick"] for a in self.idn.axes})
        plots["step"] = sp.name
        if self.an.nm.bands:
            sc = lambda t: np.array([[thrust_linear_slope(t.i("thrust_linear"), b.throttle)] for b in self.an.nm.bands])  # noqa: E731
            plots["noise"] = P.noise_compare(self.an.nm, predict_noise(self.an.nm, old).motor_rms * sc(old),
                                             predict_noise(self.an.nm, new).motor_rms * sc(new), self.noise_ref,
                                             dest / "noise_compare.png").name
        result = {
            "verdict": verdict, "violations": viol, "noise_model": bool(self.an.nm.bands), "style": self.style,
            "changes": [{"setting": k, "old": o, "new": n, "reason": r} for k, o, n, r in rows],
            "unexplained_changes": missing, "assessment": ass, "problems": problems, "plots": plots,
            "goals": {k: v for k, v in asdict(self.goals).items() if isinstance(v, (int, float, tuple, str))},
            "target_sources": self.target_sources,
            # a stored reason belongs to the stored value only (not to a --noise-budget/--style given for this call)
            "target_reasons": {k: v for k, v in self.targets.get("reasons", {}).items()
                               if self.target_sources.get(k) == "override"},
            "experiment": experiment,
            **(extra or {}),
        }
        (dest / "tune.json").write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
        import shutil

        for png in ("plant_bode.png", "motor_model.png"):
            if (self.out / png).exists() and dest.resolve() != self.out.resolve():
                shutil.copy(self.out / png, dest / png)
        write_report(dest, self.an, result, apply_txt, revert_txt)
        log(format_assessment({"new": ass["new"]}))
        if missing:
            log("changes without a reason: " + ", ".join(missing))
        if problems:
            log("RANGE PROBLEMS (tune.json.problems): " + "; ".join(problems))
        files = ["tune_cli.txt", "revert_cli.txt", "report.md", "report.html", "tune.json", *plots.values()]
        log(f"wrote into {dest}: " + ", ".join(f for f in files if (dest / f).exists()))
        return result


# --------------------------------------------------------------------- formatting
def _f(v, fmt="{:.1f}"):
    return "—" if v is None else fmt.format(v)


def format_assessment(ass: dict) -> str:
    lines = []
    common = set.intersection(*(set(a.get("notes", [])) for a in ass.values())) if len(ass) > 1 else set()
    t = next(iter(ass.values()), {}).get("targets")
    if t:
        ov = ", ".join(f"{k}={v}" for k, v in t["overrides"].items()) or "none"
        lines.append(f"[targets: style {t['style']}, overrides: {ov}] (`bftune targets` shows all values and their sources)")
    for name, a in ass.items():
        lines.append(f"{name}: {a['verdict']}")
        for ax, e in a["axes"].items():
            h, i, fu, w = e["hover"], e["idle"], e["full"], e["worst"]
            nz = "n/a" if e["noise_vs_safe"] is None else f"{e['noise_vs_safe']:.2f}x safe"
            lines.append(
                f"  {ax:5s} hover fc {_f(h['fc'])} Hz PM {_f(h['pm'], '{:.0f}')}° Ms {_f(h['ms'], '{:.2f}')}"
                f"@{_f(h.get('ms_hz'), '{:.0f}')}Hz"
                f" | idle fc {_f(i['fc'])} Ms {_f(i['ms'], '{:.2f}')}@{_f(i.get('ms_hz'), '{:.0f}')}Hz | full PM {_f(fu['pm'], '{:.0f}')}°"
                f" | worst PM {w['pm']:.0f}° ({e.get('worst_case', {}).get('pm', '')}) Ms {w['ms']:.2f}"
                f" ({e.get('worst_case', {}).get('ms', '')}) | noise {nz} | obj {e['objective_db']:.2f} dB")
            if "step" in e:
                s = e["step"]
                af = s.get("as_flown")
                if af:
                    lines.append(f"        as flown (model replays this pilot's stick inputs): 50 % at {af['delay_50_ms']:.1f} ms, "
                                 f"peak +{af['peak_pct']:.0f}%, dip -{af['dip_pct']:.0f}%")
                if not af:
                    lines.append(f"        ramp tests (typical/fast move of this pilot, a clean ramp): time shift stick→gyro "
                                 f"{s['flick']['stick_lag_ms']:.1f}/{s['snap']['stick_lag_ms']:.1f} ms, overshoot "
                                 f"{s['flick']['overshoot_pct']:.0f}/{s['snap']['overshoot_pct']:.0f}%, settle "
                                 f"{s['flick']['settle_5pct_ms']:.0f} ms (no as-flown step: this analysis has no replay data)")
            vs = e["violations"]
            for v in vs[:5]:
                lines.append(f"        violated: {v}")
            if len(vs) > 5:
                lines.append(f"        ... and {len(vs) - 5} more violated cases (use --json for all)")
        if a.get("motor_noise") is not None:
            lines.append(f"  motor noise, all axes at the motors: {a['motor_noise']:.2f}x reference"
                         + ("".join(f"  VIOLATED: {v}" for v in a.get("noise_violations", []))))
        for n in a.get("notes", []):
            if n not in common:
                lines.append(f"  note: {n}")
        for p in a.get("range_problems", []):
            lines.append(f"  RANGE: {p}")
    for n in sorted(common):
        lines.append(f"note (all): {n}")
    return "\n".join(lines)


def format_sweep(key: str, rows: list[dict]) -> str:
    """One line per value and axis: the axis's own verdict, the key margins and the first violation."""
    lines = [f"sweep {key}   (overall = all assessed axes; axis = that axis alone)",
             f"{'value':>8s} {'overall':7s} {'axis':5s} {'axis':4s}  hover fc/PM/Ms@Hz    idle fc  full PM  worst PM/Ms [case]"
             f"          noise  obj     first violation"]
    for r in rows:
        first = True
        for ax in AXES:
            if ax not in r:
                continue
            e = r[ax]
            h, i, fu, w = e["hover"], e["idle"], e["full"], e["worst"]
            nz = "n/a" if e["noise_vs_safe"] is None else f"{e['noise_vs_safe']:.2f}"
            v = e.get("violations", [])
            st = ""
            if "step" in e:
                st = (f" | stick-lag {e['step']['flick']['stick_lag_ms']:.1f} ov {e['step']['flick']['overshoot_pct']:.0f}%"
                      f"/{e['step']['snap']['overshoot_pct']:.0f}%")
                af = e["step"].get("as_flown")
                if af:
                    st += f" | as flown {af['delay_50_ms']:.1f} ms +{af['peak_pct']:.0f}%"
            wc = e.get("worst_case", {}).get("pm", "")
            lines.append(
                f"{str(r['value']) if first else '':>8s} {(r['verdict'] + ('!' if r.get('hf_beyond_flown') else '')) if first else '':7s} {ax:5s} {'ok' if not v else 'FAIL':4s}  "
                f"{_f(h['fc']):>5s}/{_f(h['pm'], '{:.0f}'):>3s}/{_f(h['ms'], '{:.2f}')}@{_f(h.get('ms_hz'), '{:.0f}'):<3s}  {_f(i['fc']):>6s}  "
                f"{_f(fu['pm'], '{:.0f}'):>6s}°  {w['pm']:4.0f}/{w['ms']:<5.2f} [{wc:18s}] {nz:>5s}  {e['objective_db']:6.2f}  "
                f"{(v[0] + (f' (+{len(v) - 1})' if len(v) > 1 else '')) if v else '-'}{st}")
            first = False
        for nv in r.get("noise_violations", []):
            lines.append(f"{'':>8s} {'':7s} all   FAIL  {nv}")
    sig = [tuple((r[ax]["hover"]["fc"], r[ax]["hover"]["ms"], r[ax]["objective_db"], r[ax]["noise_vs_safe"],
                  str(r[ax].get("worst")), str(r[ax].get("step")))
                 for ax in AXES if ax in r) for r in rows]
    has_steps = any("step" in r[ax] for r in rows for ax in AXES if ax in r)
    if len(rows) > 1 and len(set(sig)) == 1 and not has_steps:
        lines.append(f"note: margins (incl. worst case), noise and objective are identical for every value of {key}. If it acts on the stick "
                     "response (feedforward_*, rc_smoothing_*, iterm_relax*), rerun with --steps; otherwise it may be "
                     "inactive (a notch needs its *_cutoff > 0, a static lowpass is ignored while *_dyn_min_hz > 0, "
                     "tpa_low_* needs tpa_low_always = ON, dyn idle below the natural idle) or not modelled (`coverage`).")
    elif len(rows) > 1 and len(set(sig)) == 1:
        lines.append(f"note: every value gives identical results - {key} has no effect here. It may be inactive "
                     "(a notch needs its *_cutoff > 0, a static lowpass is ignored while *_dyn_min_hz > 0, tpa_low_* "
                     "needs tpa_low_always = ON, dyn idle below the natural idle), or not modelled (see `coverage`).")
    if any(r.get("hf_beyond_flown") for r in rows):
        lines.append("! = filtering at 1-3 kHz lighter than any flown tune: the noise prediction extrapolates there "
                     "(only for a supervised noise-headroom flight)")
    return "\n".join(lines)


def format_grid(res: dict) -> str:
    k1, k2 = res["keys"]
    v1, v2 = res["values"]
    axes = list(res["cells"][0]["axes"]) if res["cells"] else []
    steps = any(e.get("as_flown") for c in res["cells"] for e in c["axes"].values())
    w = 38 if steps else 24
    out = [f"grid {k1} (rows) x {k2} (columns): cell = verdict hoverMs/worstPM noise obj"
           + (" | as-flown 50% ms/peak%" if steps else "") + "  (per axis)"]
    for ax in axes:
        out.append(f"\n[{ax}]  " + "".join(f"{str(b):>{w}s}" for b in v2))
        for a in v1:
            row = f"{str(a):>6s} "
            for b in v2:
                c = next(c for c in res["cells"] if c[k1] == a and c[k2] == b)
                e = c["axes"][ax]
                nz = "-" if e["noise"] is None else f"{e['noise']:.2f}"
                af = e.get("as_flown")
                st = f" | {af['delay_50_ms']:.1f}/{af['peak_pct']:.0f}" if af else ""
                row += f"{'ok' if e['ok'] else 'NO'}{'!' if c.get('hf_beyond_flown') else ' '}{_f(e['hover_ms'], '{:.2f}')}/{e['worst_pm']:.0f} {nz} {e['obj']:.2f}{st}".rjust(w)
            out.append(row)
    if any(c.get("hf_beyond_flown") for c in res["cells"]):
        out.append("! = filtering at 1-3 kHz lighter than any flown tune (noise prediction extrapolates; headroom flights only)")
    why = [(c[k1], c[k2], ax, e["first_violation"]) for c in res["cells"] for ax, e in c["axes"].items() if not e["ok"]]
    if why:
        out.append("\nwhy NO (first violation per cell):")
        out += [f"  {k1}={a} {k2}={b} {ax}: {v}" for a, b, ax, v in why]
    return "\n".join(out)


def parse_values(spec: str) -> list:
    """'20:50:5' -> [20,25,...,50]; '100,150,200' -> list; strings kept (e.g. PT1,PT2)."""
    if ":" in spec:
        a, b, c = (float(x) for x in spec.split(":"))
        vals = np.arange(a, b + 0.5 * c, c)
        return [int(round(v)) for v in vals]
    out = []
    sep = ";" if ";" in spec else ","  # use ';' for array values, e.g. "100,100,100;100,50,100"
    for x in spec.split(sep):
        x = x.strip()
        out.append(int(x) if re.fullmatch(r"-?\d+", x) else x)
    return out


def tuning_keys() -> set[str]:
    """Settings the agent tunes (range-checked); other differences (debug_mode, resources...) are not its business."""
    from .coverage import FEATURES

    return {k for g in GROUPS.values() for k in g} | {k for f in FEATURES for k in f.keys} | {
        "simplified_pids_mode", "simplified_dterm_filter", "simplified_gyro_filter"}


def _compact_axis(e: dict) -> dict:
    out = {k: e[k] for k in ("hover", "idle", "full", "worst", "worst_case", "noise_vs_safe") if k in e}
    v = e.get("violations", [])
    out["violations"] = v[:6] + ([f"... {len(v) - 6} more (run assess)"] if len(v) > 6 else [])
    if "step" in e:
        out["step"] = e["step"]
    return out


def brief(wb: Workbench) -> dict:
    """Everything the agent needs to start reasoning, in one compact JSON (see skills/toolbox)."""

    from . import __version__
    from .emit.cli import changed_keys
    from .optimize.targets import SAFETY_FLOOR, goals_dict

    an, idn, src = wb.an, wb.idn, wb.src
    keys = [k for g in GROUPS.values() for k in g]
    cur = wb.assess(wb.logged, steps=True)
    ident = {
        "source": getattr(idn, "source", "chirp"),
        "uncertainty": getattr(idn, "uncertainty", None),
        "axes": {AXES[a]: {"structure": ai.plant.structure,
                           "params": {k: round(float(v), 5) for k, v in ai.plant.params.items()},
                           "fit_band_hz": [round(x, 1) for x in ai.fit_band],
                           "coherent_to_hz": round(ai.coherent_to_hz, 1),
                           "chain_check_passed": bool(ai.chain.passed) if np.isfinite(ai.chain.fg_rms_db) else None,
                           "op": {"throttle": round(ai.op.throttle, 3), "motor_hz": round(float(np.mean(ai.op.motor_hz)), 1),
                                  "vbat": round(ai.op.vbat, 2)}}
                 for a, ai in idn.axes.items()},
        "missing_axes": [AXES[a] for a in range(3) if a not in idn.axes],
        "motor_tau_ms": None if idn.motor is None else
        {str(int(h)): round(float(idn.motor.tau_at(h) * 1000), 1) for h in idn.motor.bins_hz},
        "validation": an.validation,
        "notes": idn.notes,
    }
    noise = wb.noise_report() if an.nm.bands else None
    safe = []
    seen: dict[str, str] = {}
    for name, t in an.safe:
        sig = json.dumps({k: t.values.get(k) for k in sorted(keys)})
        if sig in seen:  # the same tune logged twice: one entry is enough
            safe.append({"name": name, "same_tune_as": seen[sig]})
            continue
        seen[sig] = name
        ass = wb.assess(t, steps=True)
        safe.append({"name": name,
                     "differs": {k: [an.tune.values.get(k), t.values[k]] for k in changed_keys(an.tune, t) if k in keys},
                     "verdict": ass["verdict"], "axes": {ax: _compact_axis(e) for ax, e in ass["axes"].items()}})
    qa = wb.assess(an.quad_tune, steps=True) if getattr(an, "quad_tune", None) is not None else None
    summary = ([f"STALE ANALYSIS: {wb.stale}"] if wb.stale else []) + [f"model: {ident['source']} identification"
               + (f", gain uncertainty ±{(ident['uncertainty']['k_hi'] - 1) * 100:.0f} %" if ident.get("uncertainty") else "")
               + (f"; missing axes {ident['missing_axes']}" if ident["missing_axes"] else ""),
               f"logged tune: {cur['verdict']} ({'relative' if wb.relative_gate else 'absolute'} gate)"]
    if qa is not None:
        summary.append(f"DUMP DIFFERS FROM THE LOG: the quad now runs another tune ({len(changed_keys(an.tune, an.quad_tune))}"
                       f" settings differ), verdict {qa['verdict']} on this model; candidates/CLI are relative to it")
    for ax, e in cur["axes"].items():
        h, fu, w = e["hover"], e["full"], e["worst"]
        summary.append(f"{ax}: hover fc {h['fc']} Hz, Ms {h['ms']}@{h.get('ms_hz')} Hz; full PM {fu['pm']}°; "
                       f"worst PM {w['pm']}° ({e.get('worst_case', {}).get('pm', '')}); noise {e.get('noise_vs_safe')}x safe")
    for s_ in safe:
        if "same_tune_as" in s_:
            summary.append(f"proven-safe {s_['name']}: same tune as {s_['same_tune_as']}")
        else:
            summary.append(f"proven-safe {s_['name']}: {s_['verdict']}, differs in {len(s_['differs'])} settings")
    meas = (getattr(an, "extra", None) or {}).get("measured_step") or {}
    model_step = (getattr(an, "extra", None) or {}).get("model_step") or {}
    lag_pairs = [f"{ax} {m['delay_50_ms']:.1f} vs {model_step[ax]['delay_50_ms']:.1f} ms, peak "
                 f"+{m['overshoot_pct']:.0f} vs +{model_step[ax]['overshoot_pct']:.0f} %"
                 + ("" if m.get("confidence", "good") == "good" else f" ({m.get('confidence')} confidence)")
                 + (" GAP" if abs(m["delay_50_ms"] - model_step[ax]["delay_50_ms"])
                    > max(1.5, 0.2 * m["delay_50_ms"]) and m.get("confidence") != "low" else "")
                 for ax, m in meas.items() if ax in model_step]
    if lag_pairs:
        summary.append("step response of the flown tune, measured in the log vs model (50 % time, peak): " + "; ".join(lag_pairs)
                       + " (a clear timing gap = the model misses something: bftune:evidence; `peak_max` is on the MODEL's "
                       "scale, so set it from model peaks and quote predicted changes, not absolute model lags, to the pilot)")
    if wb.profile:
        st = wb.stimuli(0)
        summary.append(f"flight profile: hover throttle {wb.profile.get('hover_throttle')}, hover motor "
                       f"{wb.profile.get('hover_motor_hz')} Hz, mid design case at throttle {wb.goals.mid_throttle}, "
                       f"roll stick moves {st['flick'][0]:.0f} deg/s in {st['flick'][1] * 1000:.0f} ms typical / "
                       f"{st['snap'][0]:.0f} in {st['snap'][1] * 1000:.0f} ms fast ({st['source']}), link "
                       f"{wb.rx_hz:.0f} Hz ({wb.profile.get('rx_rate_source', 'header')})")
    summary += [f"finding [{f['severity']}] {f['summary']}" for f in getattr(an, "diagnosis", []) if f["severity"] != "info"]
    return {
        "summary": summary,
        "analysis_built_by": (getattr(an, "extra", None) or {}).get("bftune"),
        "bftune": __version__,
        "craft": an.craft, "firmware": an.firmware, "loop_hz": src.loop_hz, "log_rate_hz": src.fs,
        "style": wb.style,
        "targets": {"values": goals_dict(wb.goals), "sources": wb.target_sources,
                    "reasons": wb.targets.get("reasons", {}), "flown_hover_crossover_hz": wb.flown_hover_fc,
                    "safety_floor": SAFETY_FLOOR},
        "identification": ident,
        "flight_profile": wb.profile or None,  # how this quad is flown: the scales it is judged on
        "flight": {
            "natural_idle_rpm_p20": round(src.idle_hz(20) * 60),
            "hover_motor_hz": round(float(np.mean([np.mean(ai.op.motor_hz) for ai in idn.axes.values()])), 1)
            if idn.axes else None,
            "rx_rate_hz": round(wb.rx_hz),
        },
        "current_tune": {k: an.tune.values.get(k) for k in keys if k in an.tune.values},
        "tune_on_quad": None if getattr(an, "quad_tune", None) is None else {
            "note": "the dump differs from the logged tune; candidates and the delivered CLI are relative to this",
            "differs_from_logged": {k: [an.tune.values.get(k), an.quad_tune.values[k]]
                                    for k in changed_keys(an.tune, an.quad_tune) if k in keys},
            "assessment": {"verdict": qa["verdict"], "axes": {ax: _compact_axis(e) for ax, e in qa["axes"].items()}}},
        "current_assessment": {"verdict": cur["verdict"], "notes": cur["notes"],
                               "axes": {ax: _compact_axis(e) for ax, e in cur["axes"].items()}},
        "proven_safe_tunes": safe,
        "noise": None if noise is None else {
            "persistent_non_rpm_peaks_hz": noise["persistent_peaks_hz"],
            "bands": [{k: b[k] for k in ("throttle", "motor_hz", "measured_dterm_rms", "fit_error") if k in b}
                      for b in noise["bands"]]},
        "diagnosis": getattr(an, "diagnosis", []),
        "measured_step_as_flown": meas or None,
        "model_step_flown_tune": model_step or None,
        "warnings": an.warnings,
    }
