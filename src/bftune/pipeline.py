"""High-level workflow used by the `bftune` CLI and the agent skills.

    analyze  : decode -> identify plant (chirp IV) -> validate -> noise model -> analysis.json/.pkl
    optimize : robust search + rules -> tune.json, tune_cli.txt, revert_cli.txt, report.md
    evaluate : score any tune (e.g. a hand-edited CLI diff) against the identified model
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import __version__
from .flight import AXES, Flight, load_flight
from .io.dump import parse_dump
from .model.params import Tune
from .noise.model import NoiseModel
from .noise.model import build as build_noise
from .noise.model import predict as predict_noise
from .sysid.identify import Identification, identify
from .sysid.validate import closed_loop_check, replay


@dataclass
class Analysis:
    log_path: str
    dump_path: str | None
    log_index: int | None
    tune: Tune
    idn: Identification
    nm: NoiseModel
    craft: str | None
    firmware: str | None
    validation: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def flight(self) -> Flight:
        return load_flight(self.log_path, self.dump_path, self.log_index)


def _plant_dict(ai) -> dict:
    return {
        "structure": ai.plant.structure,
        "params": {k: float(v) for k, v in ai.plant.params.items()},
        "fit_band_hz": [float(x) for x in ai.fit_band],
        "identified_at": {"throttle": ai.op.throttle, "motor_hz": float(np.mean(ai.op.motor_hz)), "vbat": ai.op.vbat,
                          "dyn_notch_hz_estimate": ai.op.dyn_notch_hz},
        "chain_check": {"passed": ai.chain.passed, "gyro_filters_rms_db": ai.chain.fg_rms_db,
                        "gyro_filters_rms_deg": ai.chain.fg_rms_deg, "dterm_rms_db": ai.chain.d_rms_db,
                        "dterm_rms_deg": ai.chain.d_rms_deg},
        "alternatives_aic": {k: float(v.aic) for k, v in ai.alternatives.items()},
    }


def sanity_warnings(fl: Flight, idn: Identification | None) -> list[str]:
    w = []
    hr = fl.log.headers.get("blackbox_high_resolution", "0").strip() in ("1", "ON")
    if not hr:
        w.append("blackbox_high_resolution is OFF: gyro/setpoint quantized to 1 deg/s (limits coherence above ~100 Hz)")
    if fl.fs < 1500:
        w.append(f"log rate {fl.fs:.0f} Hz: noise above {fl.fs/2:.0f} Hz is aliased (handled by the alias-aware noise model)")
    if fl.motor_hz is None:
        w.append("no eRPM in log (bidirectional DShot off?): RPM filter and motor model unavailable")
    sat = np.mean(np.any(fl.motor_raw >= 2046, axis=1)) if fl.motor_raw.size else 0
    if sat > 0.02:
        w.append(f"motors at 100% for {100*sat:.1f}% of the log (saturation): check props/weight/motor_output_limit")
    if idn is not None:
        for a, ai in idn.axes.items():
            if not ai.chain.passed:
                w.append(f"{AXES[a]}: filter-chain check failed — the firmware model may not match this firmware")
            if ai.coherent_to_hz < 25:
                w.append(f"{AXES[a]}: coherent band only up to {ai.coherent_to_hz:.0f} Hz — use more chirp amplitude")
        for a in range(3):
            if a not in idn.axes:
                w.append(f"no chirp for {AXES[a]}: that axis cannot be optimized")
    return w


def analyze(log_path: str, dump_path: str | None, out: Path, log_index: int | None = None, plots: bool = True,
            log=print) -> Analysis:
    out.mkdir(parents=True, exist_ok=True)
    log(f"decoding {log_path} ...")
    fl = load_flight(log_path, dump_path, log_index)
    tune = Tune.from_config(fl.cfg)
    log(f"{fl.n} frames, {fl.t[-1]:.0f} s, log rate {fl.fs:.0f} Hz, loop {fl.loop_hz:.0f} Hz, firmware {fl.cfg.firmware_version}")
    log("identifying plant from chirp runs ...")
    idn = identify(fl, tune)
    log(idn.summary())
    log("validating model ...")
    cl = closed_loop_check(fl, tune, idn)
    val = {"closed_loop": {AXES[a]: {"rms_db": c.rms_db, "rms_deg": c.rms_deg} for a, c in cl.items()}, "replay": {}}
    for a in idn.axes:
        rr = replay(fl, tune, idn, a)
        if rr is not None:
            val["replay"][AXES[a]] = {"fit_pct": rr.fit_pct, "windows": len(rr.windows)}
    log(json.dumps(val, indent=1))
    log("building alias-aware noise model ...")
    nm = build_noise(fl, tune, idn.time_scale)
    warns = sanity_warnings(fl, idn)
    bad = [b.throttle for b in nm.bands if np.max(b.calib_err) > 1.0]
    if bad:
        warns.append(f"noise-model fit error > 1 in throttle bands {', '.join(f'{t:.2f}' for t in bad)}: noise predictions there are less certain")
    if not nm.bands:
        warns.append("noise model empty: fly >=20 s of steady flight outside the chirps (hover/cruise) so motor noise can be checked")
    an = Analysis(str(Path(log_path).resolve()), str(Path(dump_path).resolve()) if dump_path else None, log_index, tune, idn, nm,
                  fl.cfg.craft_name,
                  fl.cfg.firmware_version, val, warns)
    for w in warns:
        log(f"WARNING: {w}")
    with open(out / "analysis.pkl", "wb") as fh:
        pickle.dump(an, fh)
    pred = predict_noise(nm, tune) if nm.bands else None
    summary = {
        "bftune": __version__,
        "log": str(log_path),
        "dump": str(dump_path) if dump_path else None,
        "craft": an.craft,
        "firmware": an.firmware,
        "loop_hz": fl.loop_hz,
        "log_rate_hz": fl.fs,
        "time_scale": idn.time_scale,
        "chirp_runs": [{"axis": AXES[r.axis], "t0": float(fl.t[r.start]), "f": [r.f_start, r.f_end], "throttle": r.throttle,
                        "level_mode": r.level_mode, "reconstructed": r.reconstructed} for r in idn.runs],
        "plants": {AXES[a]: _plant_dict(ai) for a, ai in idn.axes.items()},
        "motor_model": None if idn.motor is None else {
            "tau_ms_at_hz": {str(int(h)): float(idn.motor.tau_at(h) * 1000) for h in idn.motor.bins_hz},
            "authority_full_over_hover": float(idn.motor.authority_ratio(idn.motor.bins_hz[-1], np.median(idn.motor.bins_hz))),
        },
        "validation": val,
        "noise_bands": [{"throttle": b.throttle, "motor_hz": float(np.mean(b.motor_hz)),
                         "measured_dterm_rms": np.sqrt(b.var_d).round(3).tolist(),
                         "measured_gyro_rms": np.sqrt(b.var_filt).round(3).tolist(),
                         "fit_error": b.calib_err.round(3).tolist()} for b in nm.bands],
        "predicted_motor_noise_current": None if pred is None else pred.motor_rms.round(3).tolist(),
        "notes": idn.notes,
        "warnings": an.warnings,
    }
    (out / "analysis.json").write_text(json.dumps(summary, indent=1, default=float))
    if plots:
        from .report import plots as P

        P.plant_bode(idn, out / "plant_bode.png")
        P.motor_model_plot(idn.motor, out / "motor_model.png")
    log(f"wrote {out/'analysis.json'}")
    return an


def load_analysis(out: Path) -> Analysis:
    with open(Path(out) / "analysis.pkl", "rb") as fh:
        return pickle.load(fh)


def tune_from_cli_text(base: Tune, text: str) -> Tune:
    cfg = parse_dump(text)
    t = base.copy()
    for k, v in cfg.values.items():
        t.values[k] = v
    return t


def safe_tune_from_log(path: str) -> Tune:
    from .io.bbl import decode
    from .io.dump import config_from_headers

    logs = decode(path)
    lg = max(logs, key=lambda x: x.main.shape[0])
    return Tune.from_config(config_from_headers(lg))


def archetype_seed(an: Analysis) -> dict:
    """RPM-filter-first filter layout scaled to the craft's hover motor frequency."""
    fm = float(np.mean([np.mean(ai.op.motor_hz) for ai in an.idn.axes.values()]))
    clip = lambda v, lo, hi: int(round(min(max(v, lo), hi) / 5) * 5)
    d1 = clip(0.4 * fm, 60, 150)
    return {
        "gyro_lpf1_dyn_min_hz": 0, "gyro_lpf1_static_hz": 0, "gyro_lpf2_type": "PT1",
        "gyro_lpf2_static_hz": clip(2.2 * fm, 250, 1000),
        "dterm_lpf1_type": "PT1", "dterm_lpf1_dyn_min_hz": d1, "dterm_lpf1_dyn_max_hz": 2 * d1, "dterm_lpf1_static_hz": d1,
        "dterm_lpf2_type": "PT1", "dterm_lpf2_static_hz": clip(fm, 120, 300),
        "dyn_notch_count": 1, "dyn_notch_q": 350, "dyn_notch_min_hz": clip(0.6 * fm, 60, 250),
        "tpa_mode": "PD", "tpa_rate": 40, "tpa_breakpoint": 1400,
    }


def optimize(out: Path, style: str = "freestyle", safe_logs: list[str] | None = None, safe_cli: list[str] | None = None,
             passes: int = 2, maxiter: int = 25, noise_budget: float = 0.9, log=print, seeds: dict | None = None) -> dict:
    from .optimize import rules
    from .optimize.search import FILTER_KEYS, Goals, multi_start_search

    out = Path(out)
    an = load_analysis(out)
    fl = an.flight()
    old = an.tune
    safe = []
    for p in safe_logs or []:
        safe.append(safe_tune_from_log(p))
    for p in safe_cli or []:
        safe.append(tune_from_cli_text(old, Path(p).read_text()))
    goals = Goals.for_style(style, noise_budget=noise_budget)
    if seeds is None:
        seeds = {"current": {}, "rpm-first": archetype_seed(an)}
        for i, t in enumerate(safe):
            seeds[f"safe{i+1}-filters"] = {k: t.values[k] for k in FILTER_KEYS}
    best, allr = multi_start_search(fl, an.idn, an.nm, old, goals, seeds, passes=passes, maxiter=maxiter, log=log,
                                    safe_tunes=safe)
    new = best.tune.copy()
    decisions = {}
    for d in rules.tune_feedforward(fl, an.idn, new, style) + rules.judgement(fl, an.idn, new, style):
        new.values[d.key] = d.value
        decisions[d.key] = d.reason
    new = drop_inert_changes(old, new)
    # the Configurator must not re-apply slider formulas over explicit values
    for k in ("simplified_pids_mode", "simplified_dterm_filter", "simplified_gyro_filter"):
        new.values[k] = "OFF"
    return finalize(out, an, fl, old, new, best, allr, goals, safe, decisions, style, log)


def drop_inert_changes(old: Tune, new: Tune) -> Tune:
    """Revert settings that have no effect in `new` (keeps the CLI minimal and readable)."""
    t = new.copy()

    def keep_old(*keys):
        for k in keys:
            if k in old.values:
                t.values[k] = old.values[k]

    if t.i("dyn_notch_count") == 0:
        keep_old("dyn_notch_q", "dyn_notch_min_hz", "dyn_notch_max_hz")
    if t.i("gyro_lpf1_dyn_min_hz") == 0 and t.i("gyro_lpf1_static_hz") == 0:
        keep_old("gyro_lpf1_dyn_max_hz", "gyro_lpf1_type", "gyro_lpf1_dyn_expo")
    if t.i("dterm_lpf1_dyn_min_hz") == 0 and t.i("dterm_lpf1_static_hz") == 0:
        keep_old("dterm_lpf1_dyn_max_hz", "dterm_lpf1_type", "dterm_lpf1_dyn_expo")
    elif t.i("dterm_lpf1_dyn_min_hz") > 0:
        keep_old("dterm_lpf1_static_hz") if old.i("dterm_lpf1_static_hz") > 0 else None
    if t.i("gyro_lpf2_static_hz") == 0:
        keep_old("gyro_lpf2_type")
    if t.i("dterm_lpf2_static_hz") == 0:
        keep_old("dterm_lpf2_type")
    return t


def evaluate_tunes(an: Analysis, fl: Flight, tunes: dict[str, Tune], goals, noise_ref) -> dict:
    """Per-tune robustness/performance table on the optimizer's operating cases."""
    from .optimize.search import AxisProblem, build_cases

    res = {}
    for name, t in tunes.items():
        res[name] = {}
        for axis in an.idn.axes:
            cases = build_cases(fl, an.idn, t, axis, goals)
            prob = AxisProblem(t, axis, cases, an.idn, fl.loop_hz, an.nm, goals, noise_ref.get(axis) if noise_ref else None)
            ax = AXES[axis]
            dm = t.i(f"d_max_{ax}") / t.i(f"d_{ax}") if t.i(f"d_{ax}") > 0 else 1.0
            total, obj, pen, worst, nr, rows = prob.evaluate(t.i(f"p_{ax}"), t.i(f"i_{ax}"), t.i(f"d_{ax}"), dm, detail=True)
            nominal = {r["case"]: r for r in rows}
            from .optimize.search import violations

            res[name][ax] = {
                "violations": violations(rows, goals, nr if prob.noise_Q is not None and noise_ref else None),
                "objective_db": obj, "penalty": pen, "worst": {k: float(v) for k, v in worst.items()},
                "noise_vs_budget": nr,
                "hover": {k: nominal.get("hover/d", {}).get(k) for k in ("fc", "pm", "gm_db", "ms", "dm_ms", "bw_s")},
                "idle": {k: nominal.get("idle/d", {}).get(k) for k in ("fc", "pm", "gm_db", "ms", "bw_s")},
                "full": {k: nominal.get("full/d", {}).get(k) for k in ("fc", "pm", "gm_db", "ms")},
            }
    return res


def finalize(out, an, fl, old, new, best, allr, goals, safe, decisions, style, log) -> dict:
    from .emit.cli import cli_block, diff_table
    from .optimize.rules import rx_rate_hz
    from .optimize.search import reference_noise
    from .report import plots as P
    from .report.markdown import write_report

    noise_ref = reference_noise(an.nm, [old] + safe, fl.loop_hz, an.idn) if an.nm.bands else None
    tunes = {"current": old, "new": new}
    for i, t in enumerate(safe):
        tunes[f"safe{i+1}"] = t
    ev = evaluate_tunes(an, fl, tunes, goals, noise_ref)
    reasons = dict(decisions)
    for k in new.values:
        if k not in reasons and str(old.values.get(k)) != str(new.values[k]):
            if k.startswith(("p_", "i_", "d_", "d_max_")):
                reasons[k] = "robust loop optimization (margins across idle/hover/mid/full, battery, delay)"
            elif k.startswith("simplified_"):
                reasons[k] = "keep explicit values (Configurator sliders would overwrite them)"
            else:
                reasons[k] = "filter/TPA search: best disturbance rejection within the noise budget"
    viol = {ax: e["violations"] for ax, e in ev["new"].items()}
    verdict = "PASS" if not any(viol.values()) else "FAIL"
    comment = [f"style: {style}; model-predicted margins and noise in report.md"]
    if verdict == "FAIL":
        comment.append("WARNING: the model predicts violated robustness constraints for this tune:")
        comment += [f"  {ax}: {v}" for ax, vs in viol.items() for v in vs]
        comment.append("Do NOT fly without reviewing report.md (see 'Verdict').")
    if not an.nm.bands:
        comment.append("WARNING: no noise model (too little steady flight outside chirps) - motor noise unchecked")
    profile = fl.cfg.active_profile if "dump" in fl.cfg.source else None
    apply_txt, revert_txt, problems = cli_block(old, new, profile=profile, craft=an.craft,
                                                firmware=an.firmware, extra_comment=comment)
    (out / "tune_cli.txt").write_text(apply_txt)
    (out / "revert_cli.txt").write_text(revert_txt)
    rows = diff_table(old, new, reasons)
    plots = {
        "loop": P.loop_compare(an.idn, fl.loop_hz, old, new, out / "loop_compare.png").name,
    }
    sp, step_m = P.step_compare(an.idn, fl.loop_hz, rx_rate_hz(fl), old, new, out / "step_compare.png")
    plots["step"] = sp.name
    if an.nm.bands:
        n_old = predict_noise(an.nm, old).motor_rms
        n_new = predict_noise(an.nm, new).motor_rms
        from .model.params import thrust_linear_slope

        s_old = np.array([[thrust_linear_slope(old.i("thrust_linear"), b.throttle)] for b in an.nm.bands])
        s_new = np.array([[thrust_linear_slope(new.i("thrust_linear"), b.throttle)] for b in an.nm.bands])
        plots["noise"] = P.noise_compare(an.nm, n_old * s_old, n_new * s_new, noise_ref, out / "noise_compare.png").name
    result = {
        "verdict": verdict,
        "violations": viol,
        "noise_model": bool(an.nm.bands),
        "style": style,
        "changes": [{"setting": k, "old": o, "new": n, "reason": r} for k, o, n, r in rows],
        "evaluation": ev,
        "step": {f"{AXES[a]}/{lab}": {k: float(v) for k, v in m.items()} for (a, lab), m in step_m.items()},
        "search": {name: {"score": r.score, "history": r.history} for name, r in allr.items()},
        "problems": problems,
        "plots": plots,
    }
    (out / "tune.json").write_text(json.dumps(result, indent=1, default=float))
    write_report(out, an, result, apply_txt, revert_txt)
    log("")
    log("=" * 70)
    log(f"VERDICT: {verdict} (model-predicted robustness of the new tune)")
    for ax, e in ev["new"].items():
        h = e["hover"]
        log(f"  {ax:5s} hover fc {h['fc'] or float('nan'):5.1f} Hz  PM {h['pm'] or float('nan'):4.0f}°  Ms {h['ms'] or float('nan'):.2f}"
            f" | worst PM {e['worst']['pm']:4.0f}°  worst Ms {e['worst']['ms']:.2f} | noise "
            + (f"{e['noise_vs_budget']:.2f} of proven-safe level" if an.nm.bands else "n/a"))
        for v in e["violations"]:
            log(f"      violated: {v}")
    if not an.nm.bands:
        log("  WARNING: noise model empty - motor noise was NOT checked")
    log("=" * 70)
    log(f"wrote {out/'tune_cli.txt'}, {out/'revert_cli.txt'}, {out/'report.md'}")
    if problems:
        log("VALIDATION PROBLEMS: " + "; ".join(problems))
    return result


def print_evaluation(ev: dict, noise_model: bool, log=print) -> None:
    """Compact verdict table for `bftune evaluate` / optimize."""
    for name, per in ev.items():
        v = "PASS" if not any(e["violations"] for e in per.values()) else "FAIL"
        log(f"{name}: {v}")
        for ax, e in per.items():
            h, i, w = e["hover"], e["idle"], e["worst"]
            nb = f"{e['noise_vs_budget']:.2f}x safe" if noise_model else "n/a"
            log(f"  {ax:5s} hover fc {h['fc'] or float('nan'):5.1f} Hz PM {h['pm'] or float('nan'):4.0f}° Ms {h['ms'] or float('nan'):.2f}"
                f" | idle fc {i['fc'] or float('nan'):5.1f} Hz | worst PM {w['pm']:4.0f}° Ms {w['ms']:.2f} | noise {nb}")
            for x in e["violations"]:
                log(f"        violated: {x}")
