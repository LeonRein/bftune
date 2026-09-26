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
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .flight import AXES
from .model.controller import OperatingPoint
from .model.params import DEFAULTS, Tune, thrust_linear_slope
from .model.plant import Plant
from .optimize.search import (
    AxisProblem,
    Goals,
    build_cases,
    hf_filtering,
    optimize_axis,
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
    """Apply `set` lines on top of `base`; returns (tune, {key: inline reason})."""
    t = base.copy()
    reasons: dict[str, str] = {}
    for line in text.splitlines():
        m = _SET_LINE.match(line)
        if not m:
            continue
        k, v, why = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
        apply_setting(t, k, v)
        if why:
            reasons[k] = why
    return t, reasons


@dataclass
class Workbench:
    out: Path
    style: str = "freestyle"
    noise_budget: float = 0.9
    an: Analysis = field(init=False)
    goals: Goals = field(init=False)

    def __post_init__(self):
        self.out = Path(self.out)
        self.an = load_analysis(self.out)
        if getattr(self.an, "summary", None) is None:
            raise SystemExit("analysis.pkl is from an older bftune version: re-run `bftune analyze`")
        self.goals = Goals.for_style(self.style, noise_budget=self.noise_budget)
        self.src = self.an.summary
        self.idn = self.an.idn
        safe = [t for _, t in self.an.safe]
        self.noise_ref = reference_noise(self.an.nm, [self.an.tune] + safe, self.src.loop_hz, self.idn) if self.an.nm.bands else None
        hf = [hf_filtering(t, self.idn, self.src.loop_hz) for t in [self.an.tune] + safe]
        self.hf_ref = (max(h[0] for h in hf), max(h[1] for h in hf))

    # ----------------------------------------------------------------- tunes
    @property
    def logged(self) -> Tune:
        return self.an.tune

    def load(self, path: str | Path | None) -> tuple[Tune, dict[str, str]]:
        if path is None:
            return self.logged.copy(), {}
        return parse_candidate(self.logged, Path(path).read_text())

    def write_candidate(self, path: Path, tune: Tune | None = None, reasons: dict | None = None) -> Path:
        t = tune or self.logged
        lines = [
            f"# bftune candidate for {self.an.craft or 'craft'} (Betaflight {self.an.firmware or '?'})",
            "# Edit values; keep a short reason after '#' for every change (it goes into the report).",
            "# Evaluate:  bftune assess -o OUT this_file   |   emit:  bftune emit -o OUT this_file",
        ]
        for g, keys in GROUPS.items():
            lines.append(f"\n# --- {g}")
            for k in keys:
                if k in t.values:
                    why = (reasons or {}).get(k)
                    lines.append(f"set {k} = {t.values[k]}" + (f"   # {why}" if why else ""))
        path.write_text("\n".join(lines) + "\n")
        return path

    # ----------------------------------------------------------------- assessment
    def _plant_for(self, axis: int, tune: Tune) -> Plant:
        ai = self.idn.axes[axis]
        y = ai.op.throttle
        p = dict(ai.plant.params)
        p["K"] *= thrust_linear_slope(tune.i("thrust_linear"), y) / thrust_linear_slope(self.idn.thrust_linear, y)
        return Plant(ai.plant.structure, p)

    def step_metrics(self, tune: Tune, axis: int) -> dict:
        from .analysis.loop import step_metrics, step_response
        from .optimize.rules import max_rate, rx_rate_hz

        ai = self.idn.axes[axis]
        op = OperatingPoint(throttle=ai.op.throttle, motor_hz=ai.op.motor_hz, vbat=ai.op.vbat, d_boost=1.0,
                            dyn_notch_hz=ai.op.dyn_notch_hz)
        pl = self._plant_for(axis, tune)
        rx = rx_rate_hz(self.src)
        out = {}
        for name, amp, ramp in (("flick", 300.0, 0.05), ("snap", min(600.0, 0.6 * max_rate(self.src, axis)), 0.03)):
            m = step_metrics(step_response(tune, axis, pl, op, self.idn.dt, self.src.loop_hz, rx, amplitude=amp,
                                           ramp_s=ramp, time_scale=self.idn.time_scale))
            out[name] = {k: round(float(m[k]), 2) for k in ("tracking_lag_ms", "stick_lag_ms", "overshoot_pct", "settle_5pct_ms")}
        return out

    def axis_problem(self, tune: Tune, axis: int) -> AxisProblem:
        cases = build_cases(self.src, self.idn, tune, axis, self.goals)
        prob = AxisProblem(tune, axis, cases, self.idn, self.src.loop_hz, self.an.nm, self.goals,
                           self.noise_ref.get(axis) if self.noise_ref else None)
        prob.anchor = (self.logged.i(f"p_{AXES[axis]}"), self.logged.i(f"d_{AXES[axis]}"))
        return prob

    def assess_axis(self, tune: Tune, axis: int, steps: bool = True) -> dict:
        ax = AXES[axis]
        prob = self.axis_problem(tune, axis)
        d = tune.i(f"d_{ax}")
        dm = tune.i(f"d_max_{ax}") / d if d > 0 else 1.0
        total, obj, pen, worst, nr, rows = prob.evaluate(tune.i(f"p_{ax}"), tune.i(f"i_{ax}"), d, dm, detail=True)
        byc = {r["case"]: r for r in rows}
        pick = lambda c, keys: {k: (None if byc.get(c, {}).get(k) is None else round(float(byc[c][k]), 2)) for k in keys}  # noqa: E731
        viol = violations(rows, self.goals, nr if prob.noise_Q is not None and self.noise_ref else None)
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
        }
        if steps:
            res["step"] = self.step_metrics(tune, axis)
        return res

    @property
    def relative_gate(self) -> bool:
        """No chirp -> the plant gain is only known to about ±40 %: absolute margins are not trustworthy.
        The gate then becomes 'no worse than the tune that flew' (case by case, same model)."""
        return getattr(self.idn, "source", "chirp") != "chirp"

    def _relative_violations(self, axis: int, rows: list[dict], absolute: list[str]) -> list[str]:
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
            if c not in failing or c not in ref:
                continue
            q = ref[c]
            bad = []
            pm, pm0 = r.get("pm_eff", r["pm"]), q.get("pm_eff", q["pm"])
            if pm < pm0 - 2.0:
                bad.append(f"PM {pm:.0f}° < flown {pm0:.0f}°")
            if r["gm_db"] < q["gm_db"] - 0.5:
                bad.append(f"GM {r['gm_db']:.1f} < flown {q['gm_db']:.1f} dB")
            if r["ms"] > q["ms"] * 1.05 + 0.02:
                bad.append(f"Ms {r['ms']:.2f} > flown {q['ms']:.2f}")
            if bad:
                out.append(f"{c}: " + ", ".join(bad) + " (relative gate: no chirp)")
        return out

    def assess(self, tune: Tune, steps: bool = True, axes=None) -> dict:
        from .emit.cli import changed_keys
        from .model.params import validate

        axes = list(self.idn.axes) if axes is None else axes
        per = {AXES[a]: self.assess_axis(tune, a, steps) for a in axes}
        g, dchain = hf_filtering(tune, self.idn, self.src.loop_hz)
        notes = []
        if g > self.goals.hf_extrapolation * self.hf_ref[0] or dchain > self.goals.hf_extrapolation * self.hf_ref[1]:
            notes.append("high-frequency filtering (1-3 kHz) is weaker than any proven tune: noise model extrapolates there")
        for ax, e in per.items():
            fc = e["hover"]["fc"]
            msf = e["hover"].get("ms_hz")
            if msf and msf > e["coherent_to_hz"]:
                notes.append(f"{ax}: hover sensitivity peak at {msf:.0f} Hz lies beyond the identified band "
                             f"({e['coherent_to_hz']:.0f} Hz): its height relies on the plant model")
            if fc and fc > 0.8 * e["coherent_to_hz"]:
                notes.append(f"{ax}: hover crossover {fc:.0f} Hz is close to the end of the identified band "
                             f"({e['coherent_to_hz']:.0f} Hz): margins rely on the model extrapolation")
        problems = validate(tune, [k for k in changed_keys(self.logged, tune) if k in DEFAULTS or True])
        verdict = "PASS" if not any(e["violations"] for e in per.values()) and not problems else "FAIL"
        if self.relative_gate:
            notes.insert(0, "NO CHIRP: plant gain uncertain (±40 %). Verdict = no worse than the flown tune in every "
                            "failing case (relative gate), not the absolute design margins. Keep changes small; fly a chirp.")
        return {"verdict": verdict, "gate": "relative" if self.relative_gate else "absolute", "axes": per, "notes": notes, "range_problems": problems,
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
            rows.append({"value": v, "verdict": r["verdict"], **{ax: e for ax, e in r["axes"].items()}})
        return rows

    def suggest(self, base: Tune, axes: list[int], maxiter: int = 30) -> dict:
        """Per-axis P/I/D/d_max helper with all other settings fixed (a proposal, not a decision)."""
        out = {}
        for a in axes:
            prob = self.axis_problem(base, a)
            r = optimize_axis(prob, base, maxiter=maxiter)
            out[AXES[a]] = {"p": r.p, "i": r.i, "d": r.d, "d_max": r.d_max, "feasible": r.feasible,
                            "noise_vs_safe": round(float(r.noise_ratio), 3), "worst": {k: round(float(v), 2) for k, v in r.worst.items()}}
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
                m = lf & (f > 70)
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
             dest: Path | None = None) -> dict:
        from .emit.cli import cli_block, diff_table
        from .noise.model import predict as predict_noise
        from .optimize.rules import rx_rate_hz
        from .report import plots as P
        from .report.markdown import write_report

        dest = Path(dest) if dest else self.out
        dest.mkdir(parents=True, exist_ok=True)
        old = self.logged
        new = drop_inert_changes(old, tune)
        for k in ("simplified_pids_mode", "simplified_dterm_filter", "simplified_gyro_filter"):
            new.values[k] = "OFF"
        reasons = dict(reasons or {})
        for k in ("simplified_pids_mode", "simplified_dterm_filter", "simplified_gyro_filter"):
            reasons.setdefault(k, "keep explicit values (Configurator sliders would overwrite them)")
        ass = {"current": self.assess(old), "new": self.assess(new)}
        for name, t in self.an.safe:
            ass[name] = self.assess(t)
        verdict = ass["new"]["verdict"]
        viol = {ax: e["violations"] for ax, e in ass["new"]["axes"].items()}
        comment = [f"style: {self.style}; model-predicted margins and noise in report.md"]
        if verdict == "FAIL":
            comment.append("WARNING: the model predicts violated constraints for this tune:")
            comment += [f"  {ax}: {v}" for ax, vs in viol.items() for v in vs] + [f"  {p}" for p in ass["new"]["range_problems"]]
            comment.append("Do NOT fly without reviewing report.md (see 'Verdict').")
        if not self.an.nm.bands:
            comment.append("WARNING: no noise model - motor noise unchecked")
        profile = self.src.cfg.active_profile if "dump" in self.src.cfg.source else None
        apply_txt, revert_txt, problems = cli_block(old, new, profile=profile, craft=self.an.craft,
                                                    firmware=self.an.firmware, extra_comment=comment)
        (dest / "tune_cli.txt").write_text(apply_txt)
        (dest / "revert_cli.txt").write_text(revert_txt)
        missing = [k for k, _, _, r in diff_table(old, new, reasons) if not r]
        rows = diff_table(old, new, reasons)
        plots = {"loop": P.loop_compare(self.idn, self.src.loop_hz, old, new, dest / "loop_compare.png").name}
        sp, _ = P.step_compare(self.idn, self.src.loop_hz, rx_rate_hz(self.src), old, new, dest / "step_compare.png")
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
            **(extra or {}),
        }
        (dest / "tune.json").write_text(json.dumps(result, indent=1, default=float))
        write_report(dest, self.an, result, apply_txt, revert_txt)
        log(format_assessment({"new": ass["new"]}))
        if missing:
            log("changes without a reason: " + ", ".join(missing))
        log(f"wrote {dest/'tune_cli.txt'}, {dest/'revert_cli.txt'}, {dest/'report.md'}")
        return result


# --------------------------------------------------------------------- formatting
def _f(v, fmt="{:.1f}"):
    return "—" if v is None else fmt.format(v)


def format_assessment(ass: dict) -> str:
    lines = []
    for name, a in ass.items():
        lines.append(f"{name}: {a['verdict']}")
        for ax, e in a["axes"].items():
            h, i, fu, w = e["hover"], e["idle"], e["full"], e["worst"]
            nz = "n/a" if e["noise_vs_safe"] is None else f"{e['noise_vs_safe']:.2f}x safe"
            lines.append(
                f"  {ax:5s} hover fc {_f(h['fc'])} Hz PM {_f(h['pm'], '{:.0f}')}° Ms {_f(h['ms'], '{:.2f}')}"
                f" | idle fc {_f(i['fc'])} Ms {_f(i['ms'], '{:.2f}')} | full PM {_f(fu['pm'], '{:.0f}')}°"
                f" | worst PM {w['pm']:.0f}° ({e.get('worst_case', {}).get('pm', '')}) Ms {w['ms']:.2f}"
                f" ({e.get('worst_case', {}).get('ms', '')}) | noise {nz} | obj {e['objective_db']:.2f} dB")
            if "step" in e:
                s = e["step"]
                lines.append(f"        stick→gyro lag: flick {s['flick']['stick_lag_ms']:.1f} ms, snap {s['snap']['stick_lag_ms']:.1f} ms"
                             f" | overshoot flick {s['flick']['overshoot_pct']:.0f}%, snap {s['snap']['overshoot_pct']:.0f}%"
                             f" | settle {s['flick']['settle_5pct_ms']:.0f} ms | (lag vs smoothed setpoint {s['flick']['tracking_lag_ms']:.1f} ms)")
            vs = e["violations"]
            for v in vs[:5]:
                lines.append(f"        violated: {v}")
            if len(vs) > 5:
                lines.append(f"        ... and {len(vs) - 5} more violated cases (use --json for all)")
        for n in a.get("notes", []):
            lines.append(f"  note: {n}")
        for p in a.get("range_problems", []):
            lines.append(f"  RANGE: {p}")
    return "\n".join(lines)


def format_sweep(key: str, rows: list[dict]) -> str:
    lines = [f"sweep {key}:"]
    hdr = f"{'value':>8s} {'verdict':7s} " + " | ".join(
        f"{ax}: fc/PM/Ms hover, idle fc, worstPM/Ms, noise, obj" for ax in rows[0] if ax in AXES)
    lines.append(hdr)
    for r in rows:
        parts = []
        for ax in AXES:
            if ax not in r:
                continue
            e = r[ax]
            h, i, w = e["hover"], e["idle"], e["worst"]
            nz = "n/a" if e["noise_vs_safe"] is None else f"{e['noise_vs_safe']:.2f}"
            st = ""
            if "step" in e:
                st = (f" stick-lag {e['step']['flick']['stick_lag_ms']:.1f} ov {e['step']['flick']['overshoot_pct']:.0f}%"
                      f"/{e['step']['snap']['overshoot_pct']:.0f}%")
            wc = e.get("worst_case", {}).get("pm", "")
            parts.append(f"{_f(h['fc'])}/{_f(h['pm'], '{:.0f}')}/{_f(h['ms'], '{:.2f}')}, {_f(i['fc'])}, "
                         f"{w['pm']:.0f}/{w['ms']:.2f} [{wc}], {nz}, {e['objective_db']:.2f}{st}")
        lines.append(f"{str(r['value']):>8s} {r['verdict']:7s} " + " | ".join(parts))
    return "\n".join(lines)


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


def _compact_axis(e: dict) -> dict:
    out = {k: e[k] for k in ("hover", "idle", "full", "worst", "worst_case", "noise_vs_safe") if k in e}
    v = e.get("violations", [])
    out["violations"] = v[:6] + ([f"... {len(v) - 6} more (run assess)"] if len(v) > 6 else [])
    if "step" in e:
        out["step"] = e["step"]
    return out


def brief(wb: Workbench) -> dict:
    """Everything the agent needs to start reasoning, in one compact JSON (see skills/toolbox)."""
    from dataclasses import asdict

    from . import __version__
    from .emit.cli import changed_keys

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
                           "chain_check_passed": bool(ai.chain.passed),
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
    for name, t in an.safe:
        ass = wb.assess(t, steps=True)
        safe.append({"name": name,
                     "differs": {k: [an.tune.values.get(k), t.values[k]] for k in changed_keys(an.tune, t) if k in keys},
                     "verdict": ass["verdict"], "axes": {ax: _compact_axis(e) for ax, e in ass["axes"].items()}})
    return {
        "bftune": __version__,
        "craft": an.craft, "firmware": an.firmware, "loop_hz": src.loop_hz, "log_rate_hz": src.fs,
        "style": wb.style,
        "goals": {k: v for k, v in asdict(wb.goals).items() if k in (
            "ms_max", "ms_max_robust", "pm_min", "pm_min_robust", "gm_min_db", "gm_min_robust_db", "noise_budget",
            "d_over_p", "i_over_p")},
        "identification": ident,
        "flight": {
            "natural_idle_rpm_p20": round(src.idle_hz(20) * 60),
            "hover_motor_hz": round(float(np.mean([np.mean(ai.op.motor_hz) for ai in idn.axes.values()])), 1)
            if idn.axes else None,
            "rx_rate_hz": src.header_int("rc_smoothing_rx_smoothed", 0),
        },
        "current_tune": {k: an.tune.values.get(k) for k in keys if k in an.tune.values},
        "current_assessment": {"verdict": cur["verdict"], "notes": cur["notes"],
                               "axes": {ax: _compact_axis(e) for ax, e in cur["axes"].items()}},
        "proven_safe_tunes": safe,
        "noise": None if noise is None else {
            "persistent_non_rpm_peaks_hz": noise["persistent_peaks_hz"],
            "bands": [{k: b[k] for k in ("throttle", "motor_hz", "measured_dterm_rms", "fit_error") if k in b}
                      for b in noise["bands"]]},
        "diagnosis": getattr(an, "diagnosis", []),
        "warnings": an.warnings,
    }
