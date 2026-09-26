"""Analysis step and the optional global optimizer.

    analyze  : decode -> identify plant (chirp IV) -> validate -> noise model -> analysis.json/.pkl
               (also caches a flight summary and the proven-safe tunes for the fast workbench)
    optimize : automatic baseline tune (multi-start search + rules), emitted through the workbench

The interactive, agent-driven tools (assess, sweep, suggest, ff, noise, emit) live in workbench.py.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import __version__
from .flight import AXES, Flight, FlightSummary, load_flight, summarize
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
    summary: FlightSummary | None = None
    safe: list = field(default_factory=list)  # [(name, Tune)] tunes of this quad that flew with cool motors
    diagnosis: list = field(default_factory=list)  # findings from analysis.diagnose
    quad_tune: Tune | None = None  # tune on the quad now, when the dump belongs to a different tune than the log

    def flight(self) -> Flight:
        from .flight import exclude

        return exclude(load_flight(self.log_path, self.dump_path, self.log_index), self.extra.get("excluded"))


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
    dt = np.diff(fl.t)
    if dt.size:
        gaps = dt > 2.5 * np.median(dt)
        if gaps.sum() > 0:
            w.append(f"{int(gaps.sum())} time gap(s) in the log (missing frames, {float(dt[gaps].sum()):.2f} s in total): "
                     "the logging device may not keep up with this rate; spectra near gaps are less reliable")
    from .analysis.profile import control_scale, hover
    from .flight import motor_saturated

    thr_h, _ = hover(fl)
    part_thr = thr_h + 0.6 * (1 - thr_h)
    part = float(np.mean(motor_saturated(fl) & (fl.throttle < part_thr)))
    if part > 0.01:  # at full throttle (punch-outs) saturation is normal
        w.append(f"a motor is at its maximum for {100*part:.1f}% of the log below {100*part_thr:.0f}% throttle (authority "
                 "limit): check props/weight, P/D/FF, motor_output_limit")
    if "motor_poles" not in fl.cfg.values:
        w.append("motor_poles is not in the log or dump (assumed 14): every motor frequency, RPM notch and idle case "
                 "depends on it - ask the pilot or take it from a `diff all`")
    if idn is not None:
        chirp = getattr(idn, "source", "chirp") == "chirp"
        for a, ai in idn.axes.items():
            if not ai.chain.passed:
                w.append(f"{AXES[a]}: filter-chain check failed — the firmware model may not match this firmware")
            need = 1.3 * control_scale(fl)[0]  # ~25 Hz on a 5": the band must reach past the crossover
            if chirp and ai.coherent_to_hz < need:
                w.append(f"{AXES[a]}: coherent band only up to {ai.coherent_to_hz:.0f} Hz (this quad's control band "
                         f"needs ~{need:.0f} Hz) - use more chirp amplitude or calmer air")
        if not chirp:
            w.append("no chirp in this log: plant from stick inputs only (gain ±40 %, delay from priors); the verdict "
                     "becomes relative (no worse than the flown tune). Recommend a chirp flight for the next iteration")
        for a in range(3):
            if a not in idn.axes:
                w.append(f"no model for {AXES[a]}" + (" (no chirp on that axis)" if chirp else " (see the identification notes for why)")
                         + ": that axis cannot be assessed")
    return w


MODEL_FIRMWARE = (2026, 6)  # the controller/filter model is a port of this Betaflight release


def firmware_support(version: str | None) -> tuple[str, str]:
    """('ok' | 'unknown' | 'newer' | 'older', message) for the firmware a log was flown with."""
    try:
        v = tuple(int(x) for x in str(version).split(".")[:2]) if version else None
    except ValueError:
        v = None
    if v is None:
        return "unknown", ("firmware version unknown (no dump/header version): the model is a port of Betaflight 2026.6; "
                           "the filter-chain check shows whether it matches")
    if v == MODEL_FIRMWARE:
        return "ok", ""
    if v > MODEL_FIRMWARE:
        return "newer", (f"firmware {version} is newer than the model (Betaflight 2026.6): if the filter-chain check passes, "
                         "the controller and filters still match; if it fails, stop - the model needs updating")
    return "older", (f"firmware {version} is older than the model (Betaflight 2026.6). Older releases differ in the "
                     "controller and filters (e.g. biquad instead of SVF filters, d_min instead of d_max, TPA, chirp), "
                     "so margins, noise predictions and the CLI would be wrong. Data-only tools still work: diagnose, "
                     "motors, errspec, tunes, inspect. Update to 2026.6 for model-based tuning")


def analyze(log_path: str, dump_path: str | None, out: Path, log_index: int | None = None, plots: bool = True,
            log=print, safe_logs: list[str] | None = None, safe_cli: list[str] | None = None,
            any_firmware: bool = False, excluded: list[tuple[float, float]] | None = None,
            keep_crashes: bool = False) -> Analysis:
    out.mkdir(parents=True, exist_ok=True)
    log(f"decoding {log_path} ...")
    fl = load_flight(log_path, dump_path, log_index)
    auto_excl = []
    if not keep_crashes:
        from .analysis.motors import crash_windows

        auto_excl = crash_windows(fl)
        excluded = list(excluded or []) + auto_excl
    if excluded:
        from .flight import exclude

        exclude(fl, excluded)
        log(f"excluded (treated as disarmed): {', '.join(f'{a:g}-{b:g} s' for a, b in excluded)}")
    level, msg = firmware_support(fl.cfg.firmware_version)
    if level == "older" and not any_firmware:
        raise SystemExit("bftune analyze: " + msg + " (override with --any-firmware at your own risk)")
    tune = Tune.from_config(fl.cfg)
    log(f"{fl.n} frames, {fl.t[-1]:.0f} s, log rate {fl.fs:.0f} Hz, loop {fl.loop_hz:.0f} Hz, firmware {fl.cfg.firmware_version}")
    log("identifying plant ...")
    idn = identify(fl, tune)
    log(idn.summary())
    log("validating model ...")
    cl = closed_loop_check(fl, tune, idn)
    val = {"closed_loop": {AXES[a]: {"rms_db": c.rms_db, "rms_deg": c.rms_deg} for a, c in cl.items()}
           or "n/a (no chirp)", "replay": {}}
    for a in idn.axes:
        rr = replay(fl, tune, idn, a)
        if rr is not None:
            val["replay"][AXES[a]] = {"fit_pct": rr.fit_pct, "windows": len(rr.windows)}
    log(json.dumps(val, indent=1))
    log("building alias-aware noise model ...")
    nm = build_noise(fl, tune, idn.time_scale)
    warns = sanity_warnings(fl, idn)
    if auto_excl:
        warns.append("crash(es) detected and left out of the analysis: " + ", ".join(f"{a:g}-{b:g} s" for a, b in auto_excl)
                     + " (a crash ruins identification and noise statistics; `--keep-crashes` keeps them)")
    if level != "ok":
        warns.insert(0, msg)
    bad = [b.throttle for b in nm.bands if np.max(b.calib_err) > 1.0]
    if bad:
        warns.append(f"noise-model fit error > 1 in throttle bands {', '.join(f'{t:.2f}' for t in bad)}: noise predictions there are less certain")
    if not nm.bands:
        warns.append("noise model empty: fly >=20 s of steady flight outside the chirps (hover/cruise) so motor noise can be checked")
    an = Analysis(str(Path(log_path).resolve()), str(Path(dump_path).resolve()) if dump_path else None, log_index, tune, idn, nm,
                  fl.cfg.craft_name,
                  fl.cfg.firmware_version, val, warns)
    an.extra["excluded"] = list(excluded or [])
    an.summary = summarize(fl)
    if fl.cfg.mismatch:
        an.quad_tune = Tune.from_config(fl.cfg.quad)
        ex = ", ".join(f"{k} {h}->{d}" for k, h, d in fl.cfg.mismatch[:5])
        an.warnings.append(
            f"the dump does not belong to this log: {len(fl.cfg.mismatch)} tuning settings differ ({ex}, ...). The model "
            "uses the log's own settings (what flew); the dump is the tune on the quad now: candidates start from it and "
            "the CLI/revert are relative to it. Assess it with `assess --with-current` (entry 'on_quad').")
    from .analysis.diagnose import diagnose

    an.diagnosis = diagnose(fl)
    from .report.logs import measured_steps, step_summary

    steps = measured_steps(fl)  # stick lag as flown: the direct check of the model's stick-lag prediction
    an.extra["measured_step"] = {ax: step_summary(r) for ax, r in steps.items()}
    from .analysis.profile import flight_profile

    an.extra["bftune"] = __version__
    an.extra["profile"] = flight_profile(fl)  # how this quad is flown: scales for cases, step tests, diagnostics
    from .analysis.logstep import step_windows

    # the pilot's own setpoint and the estimator windows: the workbench replays them through any candidate tune
    # the windows the measured step accepted: model replays use exactly these, for every candidate
    an.extra["replay"] = {"fs": float(fl.fs), "setpoint": fl.setpoint[:, :3].astype(np.float32),
                          "starts": {a: (steps[AXES[a]]["used"] if AXES[a] in steps else step_windows(fl, a))
                                     for a in range(3)}}
    for pth in safe_logs or []:
        an.safe.append((f"safe:{Path(pth).name}", safe_tune_from_log(pth)))
    for pth in safe_cli or []:
        an.safe.append((f"safe:{Path(pth).name}", tune_from_cli_text(tune, Path(pth).read_text())))
    for w in warns:
        log(f"WARNING: {w}")
    save_analysis(an, out)
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
        "identification_source": getattr(idn, "source", "chirp"),
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
        "flight": {
            "natural_idle_hz": {str(k): round(v, 1) for k, v in an.summary.idle_q.items()},
            "natural_idle_rpm_p20": round(an.summary.idle_hz(20) * 60),
            "rx_rate_hz": an.summary.header_int("rc_smoothing_rx_smoothed", 0),
            "hover_motor_hz": round(float(np.mean([np.mean(ai.op.motor_hz) for ai in idn.axes.values()])), 1),
        },
        "safe_tunes": [n for n, _ in an.safe],
        "dump_mismatch": [{"setting": k, "log": h, "dump": d} for k, h, d in fl.cfg.mismatch],
        "diagnosis": an.diagnosis,
        "measured_step": an.extra["measured_step"],
        "notes": idn.notes,
        "warnings": an.warnings,
    }
    (out / "analysis.json").write_text(json.dumps(summary, indent=1, default=float))
    if plots:
        from .report import plots as P
        from .report.analysis import analysis_figures, write_analysis_report

        P.plant_bode(idn, out / "plant_bode.png")
        P.motor_model_plot(idn.motor, out / "motor_model.png")
        model = model_steps(out, fl)
        an.extra["model_step"] = summary["model_step"] = {ax: step_summary(m) for ax, m in model.items()}
        save_analysis(an, out)
        (out / "analysis.json").write_text(json.dumps(summary, indent=1, default=float))
        analysis_figures(out, fl, steps, model)
        log(f"wrote {write_analysis_report(out, an, summary)}")
    log(f"wrote {out/'analysis.json'}")
    return an


def model_steps(out: Path, fl: Flight | None = None) -> dict:
    """The model's step response for the flown tune per axis, through the same estimator as the measured one."""
    try:
        from .workbench import Workbench

        wb = Workbench(Path(out))
        res = {AXES[a]: wb.model_step(wb.logged, a) for a in wb.idn.axes}
        return {k: v for k, v in res.items() if v is not None}
    except Exception:  # noqa: BLE001 - a figure must not break the analysis
        return {}


def load_analysis(out: Path) -> Analysis:
    with open(Path(out) / "analysis.pkl", "rb") as fh:
        return pickle.load(fh)


def save_analysis(an: Analysis, out: Path) -> None:
    with open(Path(out) / "analysis.pkl", "wb") as fh:
        pickle.dump(an, fh)


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


def optimize(out: Path, style: str | None = None, passes: int = 2, maxiter: int = 25, noise_budget: float | None = None,
             log=print, seeds: dict | None = None) -> dict:
    """Automatic baseline: multi-start global search + rules, emitted through the workbench.

    This is a second opinion / reproducible baseline. The recommended workflow is the
    agent-driven procedure in skills/tune (assess, sweep, suggest, ff, emit).
    """
    from .optimize import rules
    from .optimize.search import FILTER_KEYS, multi_start_search
    from .workbench import Workbench

    out = Path(out)
    wb = Workbench(out, style=style, noise_budget=noise_budget)
    an, src, old = wb.an, wb.src, wb.logged
    safe = [t for _, t in an.safe]
    goals = wb.goals  # the design targets of this analysis (data-derived bands, conventions, the agent's overrides)
    style = goals.style
    if seeds is None:
        seeds = {"current": {}, "rpm-first": archetype_seed(an)}
        for name, t in an.safe:
            seeds[f"{name}-filters"] = {k: t.values[k] for k in FILTER_KEYS}
    best, allr = multi_start_search(src, an.idn, an.nm, old, goals, seeds, passes=passes, maxiter=maxiter, log=log,
                                    safe_tunes=safe)
    new = best.tune.copy()
    reasons = {}
    for k in new.values:
        if str(old.values.get(k)) != str(new.values[k]):
            reasons[k] = ("global search: robust loop optimization" if k.startswith(("p_", "i_", "d_"))
                          else "global search: best disturbance rejection within the noise budget")
    for d in (rules.tune_feedforward(src, an.idn, new, style, overshoot_max=goals.ff_overshoot_flick)
              + rules.judgement(src, an.idn, new, style)):
        new.values[d.key] = d.value
        reasons[d.key] = d.reason
    return wb.emit(new, reasons, extra={"search": {n: {"score": r.score, "history": r.history} for n, r in allr.items()},
                                        "method": "automatic global search (baseline)"}, log=log)


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
    if t.i("gyro_lpf2_static_hz") == 0:
        keep_old("gyro_lpf2_type")
    if t.i("dterm_lpf2_static_hz") == 0:
        keep_old("dterm_lpf2_type")
    return t
