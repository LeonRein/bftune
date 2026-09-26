"""bftune command-line interface.

These are the tools the bftune agent uses (skills/tune). Users normally don't call them directly.

    bftune inspect LOG --dump DUMP
    bftune diagnose LOG [LOG2 ...] [-v]                              # problem finder (no chirp needed)
    bftune logs    LOG [LOG2 ...] -o DIR [--window 12:15]            # log report / comparison (HTML + figures)
    bftune plot    LOG --window 12:15 -o fig.png                     # one time window, to look at
    bftune analyze LOG --dump DUMP -o OUT [--safe-log OTHER.BFL]    # slow step, once (~10 s)
    bftune brief   -o OUT                                            # situation report (JSON)
    bftune candidate -o OUT cand.txt                                 # editable tune file
    bftune noise   -o OUT [cand.txt]                                 # filter decisions
    bftune assess  -o OUT cand.txt [other.txt ...]                   # verdict + metrics (~1 s)
    bftune sweep   -o OUT cand.txt d_roll 20:50:5                    # tradeoff table
    bftune suggest -o OUT cand.txt [--axis roll]                     # per-axis P/D proposal
    bftune ff      -o OUT cand.txt --axis roll                       # feedforward table
    bftune emit    -o OUT cand.txt                                   # CLI + revert + report
    bftune optimize -o OUT                                           # optional automatic baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from . import __version__

STYLES = ["freestyle", "race", "cinematic", "longrange"]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _wb(a):
    from .workbench import Workbench

    return Workbench(Path(a.out), style=a.style, noise_budget=a.noise_budget)


def cmd_inspect(a) -> int:
    from .flight import AXES, flight_from_log, load_flight, tuning_keys
    from .io.bbl import decode
    from .io.dump import config_from_headers, load_dump, reconcile
    from .pipeline import sanity_warnings
    from .sysid.chirp import find_chirps

    if a.log.endswith(".pkl"):
        fl = load_flight(a.log)
        print(f"{a.log}: synthetic flight (1 session), {fl.n} frames, {fl.t[-1]:.1f} s, rate {fl.fs:.0f} Hz "
              f"(loop {fl.loop_hz:.0f} Hz / {fl.log_ratio}), firmware {fl.cfg.firmware_version}")
        if fl.motor_hz is not None:
            print(f"    debug_mode {fl.debug_mode}, motor Hz {np.percentile(fl.motor_hz, 5):.0f}–{np.percentile(fl.motor_hz, 99):.0f}")
        runs = find_chirps(fl)
        for r in runs:
            print(f"    chirp {AXES[r.axis]:5s} t={fl.t[r.start]:6.1f}s {r.f_start:.1f}->{r.f_end:.0f} Hz thr {r.throttle:.2f}")
        if not runs:
            print("    no chirp runs (freestyle only)")
        for w in sanity_warnings(fl, None):
            print("    warning:", w)
        return 0
    logs = decode(a.log)
    print(f"{a.log}: {len(logs)} log session(s)")
    for lg in logs:
        cfg = reconcile(load_dump(a.dump) if a.dump else None, config_from_headers(lg), tuning_keys())
        fl = flight_from_log(lg, cfg)
        print(f"\n[{lg.index}] {fl.n} frames, {fl.t[-1]:.1f} s, rate {fl.fs:.0f} Hz (loop {fl.loop_hz:.0f} Hz / {fl.log_ratio}),"
              f" firmware {cfg.firmware_version}, craft '{cfg.craft_name}', corrupt frames {lg.stats['corrupt']}")
        if fl.motor_hz is not None:
            print(f"    debug_mode {fl.debug_mode}, high_resolution {lg.headers.get('blackbox_high_resolution')}, "
                  f"motor Hz {np.percentile(fl.motor_hz, 5):.0f}–{np.percentile(fl.motor_hz, 99):.0f}")
        for r in find_chirps(fl):
            print(f"    chirp {AXES[r.axis]:5s} t={fl.t[r.start]:6.1f}s {r.f_start:.1f}->{r.f_end:.0f} Hz thr {r.throttle:.2f}"
                  f"{' (angle/horizon)' if r.level_mode else ''}{' (reconstructed)' if r.reconstructed else ''}")
        ch = {k: cfg.values.get(k) for k in ("chirp_amplitude_roll", "chirp_amplitude_pitch", "chirp_amplitude_yaw",
                                             "chirp_frequency_start_deci_hz", "chirp_frequency_end_deci_hz",
                                             "chirp_time_seconds") if cfg.values.get(k) is not None}
        if ch:
            print("    chirp settings: " + ", ".join(f"{k.replace('chirp_', '')} {v}" for k, v in ch.items()))
        if cfg.mismatch:
            print(f"    warning: the dump does not belong to this log: {len(cfg.mismatch)} tuning settings differ "
                  f"(e.g. {', '.join(f'{k} {h}->{d}' for k, h, d in cfg.mismatch[:4])}). The model uses the log's own "
                  "settings; the dump is treated as the tune on the quad now.")
        from .pipeline import firmware_support

        level, msg = firmware_support(cfg.firmware_version)
        if level != "ok":
            print("    warning:", msg)
        for w in sanity_warnings(fl, None):
            print("    warning:", w)
    return 0


def cmd_analyze(a) -> int:
    from .pipeline import analyze

    analyze(a.log, a.dump, Path(a.out), a.index, log=_log, safe_logs=a.safe_log, safe_cli=a.safe_cli,
            any_firmware=a.any_firmware, excluded=_windows(a.exclude))
    return 0


def cmd_safe(a) -> int:
    from .pipeline import load_analysis, safe_tune_from_log, save_analysis, tune_from_cli_text

    an = load_analysis(Path(a.out))
    if not a.log and not a.cli:
        from .emit.cli import changed_keys
        from .workbench import GROUPS

        keys = [k for g in GROUPS.values() for k in g]
        for name, t in an.safe:
            ch = [k for k in changed_keys(an.tune, t) if k in keys]
            print(f"{name}: differs from the logged tune in")
            for k in ch:
                print(f"    {k:28s} {an.tune.values.get(k)!s:>12} -> {t.values[k]}")
        if not an.safe:
            print("no proven-safe tunes stored (the logged tune is the only noise reference)")
        return 0
    for p in a.log or []:
        an.safe.append((f"safe:{Path(p).name}", safe_tune_from_log(p)))
    for p in a.cli or []:
        an.safe.append((f"safe:{Path(p).name}", tune_from_cli_text(an.tune, Path(p).read_text())))
    save_analysis(an, Path(a.out))
    print("proven-safe tunes:", ", ".join(n for n, _ in an.safe) or "(none: the logged tune is the only reference)")
    return 0


def cmd_candidate(a) -> int:
    from .emit.cli import changed_keys
    from .workbench import parse_candidate

    wb = _wb(a)
    tune, reasons = wb.load(a.base) if a.base else (wb.on_quad.copy(), {})
    for kv in a.set or []:
        body, _, why = kv.partition("#")
        if "=" not in body:
            raise SystemExit(f"candidate: --set expects KEY=VALUE[#reason], got {kv!r}")
        k, v = (x.strip() for x in body.split("=", 1))
        if not v:
            raise SystemExit(f"candidate: empty value for {k!r}")
        tune, r = parse_candidate(tune, f"set {k} = {v}" + (f"  # {why.strip()}" if why.strip() else ""))
        reasons.update(r)
    p = wb.write_candidate(Path(a.file), tune, reasons)
    ch = changed_keys(wb.on_quad, tune)
    start = Path(a.base).name if a.base else "the tune on the quad" if wb.on_quad is not wb.logged else "the logged tune"
    print(f"wrote {p}: {start}" + (f" + changes: {', '.join(f'{k}={tune.values[k]}' for k in ch)}" if ch else ""))
    return 0


def cmd_assess(a) -> int:
    from .workbench import format_assessment

    wb = _wb(a)
    res = {}
    if a.with_current:
        res["logged"] = wb.assess(wb.logged, steps=not a.fast)  # the tune that flew in the log
        if wb.on_quad is not wb.logged:
            res["on_quad"] = wb.assess(wb.on_quad, steps=not a.fast)
    if a.with_safe:
        seen = []
        for name, t in wb.an.safe:
            if any(t.values == u.values for u in seen):  # the same tune logged twice
                continue
            seen.append(t)
            res[name] = wb.assess(t, steps=not a.fast)
    for f in a.files:
        t, _ = wb.load(f)
        res[Path(f).name] = wb.assess(t, steps=not a.fast)
    print(json.dumps(res, indent=1, default=float) if a.json else format_assessment(res))
    return 0


def _pairs(items: list[str], what: str) -> list[tuple[str, list]]:
    from .workbench import parse_values

    if len(items) % 2:
        raise SystemExit(f"{what}: expected KEY VALUES pairs, got {items!r} (e.g. d_roll 20:50:5 p_roll 30,40,50)")
    out = []
    for k, v in zip(items[::2], items[1::2]):
        vals = [x for x in parse_values(v) if str(x).strip() != ""]
        if not vals:
            raise SystemExit(f"{what}: no values given for {k} (got {v!r}); e.g. 20:50:5 or 100,120,140")
        out.append((k, vals))
    return out


def cmd_sweep(a) -> int:
    from .workbench import format_sweep

    wb = _wb(a)
    base, _ = wb.load(a.file)
    res = {}
    for key, vals in _pairs(a.pairs, "sweep"):
        rows = wb.sweep(base, key, vals, steps=a.steps)
        res[key] = rows
        if not a.json:
            print(format_sweep(key, rows) + "\n")
    if a.json:
        print(json.dumps(res if len(res) > 1 else next(iter(res.values())), indent=1, default=float))
    return 0


def cmd_grid(a) -> int:
    from .workbench import format_grid

    wb = _wb(a)
    base, _ = wb.load(a.file)
    (k1, v1), (k2, v2) = _pairs([a.key1, a.values1, a.key2, a.values2], "grid")
    res = wb.grid(base, k1, v1, k2, v2, steps=a.steps)
    print(json.dumps(res, indent=1, default=float) if a.json else format_grid(res))
    return 0


def cmd_suggest(a) -> int:
    from .flight import AXES

    wb = _wb(a)
    axes = [AXES.index(x) for x in a.axis] if a.axis else list(wb.idn.axes)
    for f in a.files:
        base, _ = wb.load(f)
        res = wb.suggest(base, axes, maxiter=a.maxiter)
        t = base.copy()
        for ax, r in res.items():
            t.update(**{f"p_{ax}": r["p"], f"i_{ax}": r["i"], f"d_{ax}": r["d"], f"d_max_{ax}": r["d_max"]})
        ass = wb.assess(t, steps=False, axes=axes)
        tot = sum(e["objective_db"] * (0.5 if ax == "yaw" else 1.0) for ax, e in ass["axes"].items())
        print(f"== {Path(f).name}: verdict with suggested gains {ass['verdict']}, total objective {tot:.2f} dB")
        for n in ass["notes"]:
            if "1-3 kHz" in n or "STALE" in n.upper() or "older" in n:
                print(f"   note: {n}")
        for ax, r in res.items():
            lag = (f"  as flown {r['as_flown_50_ms'][0]:.1f} -> {r['as_flown_50_ms'][1]:.1f} ms, peak "
                   f"+{r['as_flown_peak_pct'][0]:.0f} -> +{r['as_flown_peak_pct'][1]:.0f} %") if r.get("as_flown_50_ms") else ""
            print(f"{ax:5s}: P {r['p']} I {r['i']} D {r['d']} d_max {r['d_max']}  {'passes' if r['feasible'] else 'FAILS'} "
                  f"noise {r['noise_vs_safe']:.2f}x safe  worst PM {r['worst']['pm']:.0f}° Ms {r['worst']['ms']:.2f}"
                  f"  obj {ass['axes'][ax]['objective_db']:.2f} dB{lag}" + (f"  [{r['range']}]" if "range" in r else ""))
            for v in r.get("violations", []):
                print(f"        violated: {v}")
    print("(proposals with all other settings fixed; they optimise disturbance rejection (obj), not stick lag, and set\n"
          " I = i_over_p x P (a target, `bftune targets`); several files = compare filter/TPA variants at their best gains)")
    return 0


def cmd_diagnose(a) -> int:
    from .analysis.diagnose import diagnose, format_findings
    from .flight import exclude, load_flight

    for path in a.logs:
        fl = exclude(load_flight(path, a.dump, a.index), _windows(a.exclude))
        res = diagnose(fl)
        if a.json:
            print(json.dumps({"log": path, "findings": res}, indent=1, default=float))
        else:
            print(f"== {path} ({fl.t[-1]:.0f} s)")
            print(format_findings(res, verbose=a.verbose))
    return 0


def cmd_brief(a) -> int:
    from .workbench import brief

    b = brief(_wb(a))
    txt = json.dumps(b, indent=1, default=float, ensure_ascii=False)
    Path(a.out, "brief.json").write_text(txt)
    print(txt)
    return 0


def cmd_project(a) -> int:
    from .project import init_project, next_tune_dir

    if a.action == "init":
        made = init_project(Path(a.path), a.name)
        print(f"project at {Path(a.path).resolve()}: " + (", ".join(p.name for p in made) + " created" if made else "exists"))
    elif a.action == "latest-tune-dir":
        from .project import latest_tune_dir

        d = latest_tune_dir(Path(a.path))
        if d is None:
            raise SystemExit("no tune folder yet: use `bftune project next-tune-dir`")
        print(d)
    else:
        d = next_tune_dir(Path(a.path))
        d.mkdir(parents=True, exist_ok=True)
        print(d)
    return 0


def cmd_coverage(a) -> int:
    from .coverage import coverage, format_coverage

    wb = _wb(a)
    cand = wb.load(a.file)[0] if a.file else None
    rows = coverage(wb.on_quad, cand)
    print(json.dumps(rows, indent=1) if a.json else format_coverage(rows, cand is not None))
    return 0


def cmd_motors(a) -> int:
    from .analysis.motors import format_motors, motor_health, rpm_events
    from .flight import exclude, load_flight

    res = {}
    for p in a.logs:
        fl = exclude(load_flight(p), _windows(a.exclude))
        ev, h = rpm_events(fl), motor_health(fl)
        res[p] = {"events": ev, "health": h}
        if not a.json:
            print(format_motors(p, ev, h))
    if a.json:
        print(json.dumps(res, indent=1, default=float))
    return 0


def cmd_applied(a) -> int:
    from .tunes import applied, format_applied

    res = applied(a.tune_cli, a.new_dump, a.old)
    print(json.dumps(res, indent=1) if a.json else format_applied(res))
    return 0 if not res["not_applied"] and res["profile_ok"] else 1


def cmd_targets(a) -> int:
    from .optimize.targets import TARGET_KEYS, build_goals, format_targets, goals_dict, load_targets, save_targets

    wb = _wb(a)
    data = load_targets(Path(a.out)) if not a.reset else {"overrides": {}, "reasons": {}}
    for kv in a.set or []:
        body, _, why = kv.partition("#")
        if "=" not in body:
            raise SystemExit(f"targets: --set expects KEY=VALUE[#reason], got {kv!r}")
        k, v = (x.strip() for x in body.split("=", 1))
        if k != "style" and k not in TARGET_KEYS:
            raise SystemExit(f"unknown target {k!r}; targets: style, {', '.join(TARGET_KEYS)}")
        data["overrides"][k] = v
        if why.strip():
            data["reasons"][k] = why.strip()
    for k in a.unset or []:
        data["overrides"].pop(k, None)
        data["reasons"].pop(k, None)
    g, src = build_goals(None, wb.flown_hover_fc, data["overrides"], None, wb.profile, wb._unc, wb.an.tune)  # checks the floor
    if a.set or a.unset or a.reset:
        save_targets(Path(a.out), data)
    if g.peak_max is None and wb.peak_max:  # default: the flown tune's own as-flown peak (computed by the workbench)
        g.peak_max = tuple(wb.peak_max.get(ax) for ax in ("roll", "pitch", "yaw"))
        src["peak_max"] = wb.target_sources.get("peak_max", "from the flown tune (as-flown peak)")
    print(json.dumps({"targets": goals_dict(g), "sources": src, "reasons": data["reasons"]}, indent=1, default=list)
          if a.json else format_targets(g, src, data["reasons"]))
    if (a.set or a.unset or a.reset) and not a.json:
        # what the new targets mean for the tune that flew (brief/assess from before are now stale)
        from .workbench import Workbench

        ass = Workbench(Path(a.out)).assess(wb.logged, steps=False)
        viol = [f"{ax}: {v}" for ax, e in ass["axes"].items() for v in e["violations"]] + ass.get("noise_violations", [])
        print(f"\nflown tune under these targets: {ass['verdict']}" + ("" if not viol else f" ({len(viol)} violated: "
              + "; ".join(viol[:4]) + (" ..." if len(viol) > 4 else "") + ")")
              + ". Re-run `brief`/`assess` for everything else: earlier outputs used the old targets.")
    return 0


def cmd_tunes(a) -> int:
    from .tunes import format_tunes, group_tunes

    res = group_tunes(a.logs, a.dump, [k.strip() for k in a.keys.split(",")] if a.keys else None)
    print(json.dumps(res, indent=1) if a.json else format_tunes(res))
    return 0


def cmd_errspec(a) -> int:
    from .analysis.errspec import error_spectrum
    from .flight import exclude, load_flight

    splits = [None] if a.by_throttle is None else [(0.0, a.by_throttle), (a.by_throttle, 1.01)]
    fls = {Path(p).name: exclude(load_flight(p), _windows(a.exclude)) for p in a.logs}
    res = {}
    for sp in splits:
        tag = "" if sp is None else f" thr {'<' if sp[0] == 0 else '>='}{a.by_throttle:.2f}"
        for name, fl in fls.items():
            res[name + tag] = error_spectrum(fl, throttle=sp)
    if a.json:
        print(json.dumps(res, indent=1, default=float))
        return 0
    first = next(iter(res.values()))
    print("free-flight tracking error (setpoint - gyro) PSD [dB (deg/s)^2/Hz] per band, acro windows only")
    print(f"{'log':30s} {'axis':5s} " + " ".join(f"{b:>7s}" for b in first["bands"]))
    for name, r in res.items():
        for ax, vals in r["axes"].items():
            print(f"{name[:30]:30s} {ax:5s} " + " ".join(f"{v:7.1f}" for v in vals) + f"   ({r['windows']} windows)")
    print("\nhow hard each flight was flown (compare spectra only between similar flights):")
    for name, r in res.items():
        act = r.get("activity")
        if act:
            print(f"  {name[:30]:30s} stick RMS r/p/y {act['stick_rms_deg_s']} deg/s, throttle p25/50/75 "
                  f"{act['throttle_p25_p50_p75']}, motor saturation {act['motor_saturation_pct']} %")
    print("A bump at 30-60 Hz = sensitivity peak (propwash wobble); higher 5-30 Hz = weaker rejection. A flat shift in every")
    print("band with more stick activity is flying style, not the tune. Compare shapes and large differences (>3 dB).")
    return 0


def _windows(specs):
    out = []
    for w in specs or []:
        try:
            t0, t1 = (float(x) for x in w.split(":"))
        except ValueError:
            raise SystemExit(f"--window {w!r}: expected T0:T1 in seconds, e.g. 12.5:15") from None
        out.append((t0, t1))
    return out


def cmd_logs(a) -> int:
    from .report.logs import format_summary, log_report

    html, summary = log_report(a.logs, Path(a.out), a.dump, a.index, _windows(a.window), not a.no_spectrogram,
                               _windows(a.exclude))
    (Path(a.out) / "logs.json").write_text(json.dumps(summary, indent=1, default=float))
    print(json.dumps(summary, indent=1, default=float) if a.json else format_summary(summary, html))
    return 0


def cmd_plot(a) -> int:
    from .flight import load_flight
    from .report.logs import spectrogram_plot, window_plot

    fl = load_flight(a.log, a.dump, a.index)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.spectrogram:
        print(spectrogram_plot(fl, out, Path(a.log).name))
        return 0
    wins = _windows(a.window) or [(0.0, float(fl.t[-1]))]
    for i, (t0, t1) in enumerate(wins):
        p = out if len(wins) == 1 else out.with_name(f"{out.stem}_{i}{out.suffix}")
        print(window_plot(fl, t0, t1, p, f"{Path(a.log).name}  {t0:g}-{t1:g} s"))
    return 0


def cmd_ff(a) -> int:
    from .flight import AXES
    from .workbench import parse_values

    wb = _wb(a)
    base, _ = wb.load(a.file)
    axis = AXES.index(a.axis)
    rows = wb.ff_table(base, axis, parse_values(a.values))
    st = wb.stimuli(axis)
    print(f"feedforward f_{a.axis}: flick = {st['flick'][0]:.0f}°/s in {st['flick'][1] * 1000:.0f} ms, snap = "
          f"{st['snap'][0]:.0f}°/s in {st['snap'][1] * 1000:.0f} ms ({st['source']})")
    print("lag = gyro vs smoothed setpoint; stick = gyro vs raw stick (end-to-end, includes RC smoothing);")
    print("as flown = the model replaying this pilot's logged stick inputs (the measured step of the flown tune is the check)")
    print(f"{'F':>5s} | {'flick lag':>9s} {'stick':>7s} {'overshoot':>9s} {'settle':>7s} | {'snap lag':>8s} {'stick':>7s} "
          f"{'overshoot':>9s} | {'as flown 50%':>12s} {'peak':>6s}")
    tf, ts = wb.goals.ff_overshoot_flick, wb.goals.ff_overshoot_snap
    for r in rows:
        fl_, sn = r["flick"], r["snap"]
        af = r.get("as_flown")
        pk = wb.peak_max.get(a.axis)
        if af and pk is not None:
            over = f"  above the peak target +{pk:.0f} %" if af["peak_pct"] > pk + 1.0 else ""
        else:
            over = "  over target" if fl_["overshoot_pct"] > tf or sn["overshoot_pct"] > ts else ""
        print(f"{r['f']:>5} | {fl_['tracking_lag_ms']:8.1f}ms {fl_['stick_lag_ms']:5.1f}ms {fl_['overshoot_pct']:8.0f}% "
              f"{fl_['settle_5pct_ms']:6.0f}ms | {sn['tracking_lag_ms']:7.1f}ms {sn['stick_lag_ms']:5.1f}ms {sn['overshoot_pct']:8.0f}%"
              + (f" | {r['as_flown']['delay_50_ms']:10.1f}ms {r['as_flown']['peak_pct']:5.0f}%" if r.get("as_flown") else "")
              + over)
    pk = wb.peak_max.get(a.axis)
    if pk is not None:
        print(f"peak target (as flown): +{pk:.0f} % ({wb.target_sources.get('peak_max')}; `bftune targets --set "
              "'peak_max=R,P,Y # why'` for this pilot)")
    else:
        print(f"overshoot targets: flick <= {tf:.0f} %, snap <= {ts:.0f} % ({wb.target_sources.get('ff_overshoot_flick')}; "
              "`bftune targets` to change them for this pilot)")
    return 0


def _per_axis(d) -> str:
    from .flight import AXES

    if isinstance(d, dict):
        return ", ".join(f"{AXES[k] if isinstance(k, int) else k} {float(v):.3g}" for k, v in d.items())
    return str(d)


def cmd_noise(a) -> int:
    wb = _wb(a)
    tune = wb.load(a.file)[0] if a.file else None
    rep = wb.noise_report(tune)
    if a.json:
        print(json.dumps(rep, indent=1, default=float))
        return 0
    print(f"noise report (log rate {rep['log_rate_hz']:.0f} Hz)")
    for b in rep["bands"]:
        print(f"throttle {b['throttle']:.2f} (motors {b['motor_hz']:.0f} Hz): D-term rms {b['measured_dterm_rms']} "
              f"gyro rms {b['measured_gyro_rms']} fit error {b['fit_error']}")
        if "safe_level" in b:
            print(f"    motor noise: proven-safe level {_per_axis(b['safe_level'])}"
                  + (f"  candidate {_per_axis(b['candidate'])}" if "candidate" in b else ""))
        for p in b["non_rpm_peaks"]:
            print(f"    non-RPM peak {p['axis']:5s} at {p['apparent_hz']:6.1f} Hz (+{p['db_above_floor']:.0f} dB)"
                  + ("  PERSISTENT across throttle" if p.get("persistent") else "  (moves with throttle)"))
    print("persistent non-RPM peaks:", rep.get("persistent_peaks_hz") or "none")
    print(rep["note"])
    return 0


def cmd_emit(a) -> int:
    wb = _wb(a)
    tune, reasons = wb.load(a.file)
    dest = Path(a.to) if a.to else Path(a.out)
    res = wb.emit(tune, reasons, extra={"method": "bftune agent (tune skill)"}, log=_log, dest=dest, profile=a.profile,
                  experiment=a.experiment)
    print((dest / "tune_cli.txt").read_text())
    return 0 if res["verdict"] == "PASS" else 2


def cmd_optimize(a) -> int:
    from .pipeline import optimize

    res = optimize(Path(a.out), style=a.style, passes=a.passes, maxiter=a.maxiter, noise_budget=a.noise_budget, log=_log)
    print((Path(a.out) / "tune_cli.txt").read_text())
    return 0 if res["verdict"] == "PASS" else 2


def cmd_all(a) -> int:
    cmd_analyze(a)
    return cmd_optimize(a)


def cmd_synth(a) -> int:
    import pickle
    from dataclasses import replace

    from .synth.quad import CRAFTS, default_tune, simulate

    spec = CRAFTS[a.craft]
    if a.frame_mode_hz is not None:
        spec = replace(spec, frame_mode_hz=a.frame_mode_hz, frame_mode_amp=a.frame_mode_amp)
    tune = None
    if a.set:
        tune = default_tune(spec)
        for kv in a.set:
            k, v = kv.split("=", 1)
            tune.set(k.strip(), v.strip())
    fl = simulate(spec, tune=tune, chirp_axes=() if a.no_chirp else (0, 1, 2), chirp_repeats=a.repeats,
                  chirp_s=a.chirp_s, hover_s=a.hover_s, freestyle_s=a.freestyle_s, seed=a.seed)
    with open(a.output, "wb") as fh:
        pickle.dump(fl, fh)
    print(f"wrote synthetic flight {a.output} ({fl.n} frames)")
    if a.truth:
        from .synth.quad import true_plant

        for axis, name in enumerate(("roll", "pitch", "yaw")):
            tp = true_plant(CRAFTS[a.craft], axis)
            print(f"true {name}: {tp.structure} " + ", ".join(f"{k}={v:.4g}" for k, v in tp.params.items()))
    return 0


def _wb_args(s, out_default="bftune_out") -> None:
    s.add_argument("-o", "--out", default=out_default, help="analysis directory (from `bftune analyze`)")
    s.add_argument("--style", default=None, choices=STYLES,
                   help="flying style (default: the one stored with `bftune targets`, else freestyle)")
    s.add_argument("--noise-budget", type=float, default=None,
                   help="allowed motor noise as a multiple of the proven-safe level (default: targets / style)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bftune", description="Model-based Betaflight tuning from blackbox chirp logs",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="decode a log and list sessions, chirps and warnings")
    s.add_argument("log")
    s.add_argument("--dump", help="CLI 'dump' or 'diff all' text file")
    s.set_defaults(fn=cmd_inspect)

    for name, fn, hlp in (("analyze", cmd_analyze, "identify plant + noise model from a chirp log (run once)"),
                          ("all", cmd_all, "analyze + automatic baseline optimization")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("log")
        s.add_argument("--dump", help="CLI 'dump' or 'diff all' text file (recommended)")
        s.add_argument("--index", type=int, default=None, help="log session index (default: longest)")
        s.add_argument("--safe-log", action="append",
                       help="log of another tune of this quad that flew with cool motors (sets the noise budget)")
        s.add_argument("--safe-cli", action="append", help="CLI diff of another proven-safe tune")
        s.add_argument("--exclude", action="append", metavar="T0:T1",
                       help="ignore this time window (s), e.g. a crash; repeatable")
        s.add_argument("--any-firmware", action="store_true",
                       help="analyze firmware older than the model (2026.6) anyway: margins/CLI may be wrong")
        s.add_argument("-o", "--out", default="bftune_out")
        if name == "all":
            s.add_argument("--style", default=None, choices=STYLES)
            s.add_argument("--noise-budget", type=float, default=None)
            s.add_argument("--passes", type=int, default=2)
            s.add_argument("--maxiter", type=int, default=25)
        s.set_defaults(fn=fn)

    s = sub.add_parser("diagnose", help="automatic problem finder on one or more logs (works without chirps)")
    s.add_argument("logs", nargs="+")
    s.add_argument("--dump", help="CLI dump (optional)")
    s.add_argument("--index", type=int, default=None, help="log session index (default: longest)")
    s.add_argument("-v", "--verbose", action="store_true", help="show evidence, causes and knobs")
    s.add_argument("--json", action="store_true")
    s.add_argument("--exclude", action="append", metavar="T0:T1",
                   help="ignore this time window (s), e.g. a crash; repeatable")
    s.set_defaults(fn=cmd_diagnose)

    s = sub.add_parser("brief", help="compact JSON situation report for the agent (writes OUT/brief.json)")
    _wb_args(s)
    s.set_defaults(fn=cmd_brief)

    s = sub.add_parser("project", help="per-quad project folder: init | next-tune-dir")
    s.add_argument("action", choices=["init", "next-tune-dir", "latest-tune-dir"])
    s.add_argument("path")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_project)

    s = sub.add_parser("coverage", help="every flight-behaviour feature: logged vs candidate, what the model can test")
    _wb_args(s)
    s.add_argument("file", nargs="?", help="candidate file (optional)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_coverage)

    s = sub.add_parser("motors", help="motor health: rpm-collapse events classified (stall / mixer / crash), "
                                      "rpm per command, telemetry jitter")
    s.add_argument("logs", nargs="+")
    s.add_argument("--json", action="store_true")
    s.add_argument("--exclude", action="append", metavar="T0:T1",
                   help="ignore this time window (s), e.g. a crash; repeatable")
    s.set_defaults(fn=cmd_motors)

    s = sub.add_parser("applied", help="check a dump taken after pasting: is the delivered CLI on the quad, "
                                       "what else changed (--old = the dump before)")
    s.add_argument("tune_cli")
    s.add_argument("new_dump")
    s.add_argument("--old", help="the dump from before the tune was pasted")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_applied)

    s = sub.add_parser("targets", help="show or set the design targets for this analysis (each value says where "
                                       "it came from: the data, a convention or your override; the safety floor is fixed)")
    s.add_argument("-o", "--out", default="bftune_out")
    s.add_argument("--set", action="append", metavar="KEY=VALUE[#reason]",
                   help="e.g. --set 'style=race' --set 'ms_max=2.2 # racer accepts more sensitivity for crossover'")
    s.add_argument("--unset", action="append", metavar="KEY")
    s.add_argument("--reset", action="store_true", help="drop all overrides")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_targets, style=None, noise_budget=None)

    s = sub.add_parser("tunes", help="group logs by the tune they flew (headers only, instant); check a dump against them")
    s.add_argument("logs", nargs="+")
    s.add_argument("--dump", help="CLI dump/diff to match against the logs' tunes")
    s.add_argument("--keys", help="comma list of settings to show per log (e.g. p_pitch,tpa_rate,feedforward_averaging)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_tunes)

    s = sub.add_parser("logs", help="log report (one log) or comparison (several): findings, measured step response, "
                       "error spectrum, noise-vs-throttle spectrograms, time windows -> DIR/logs.html + PNGs")
    s.add_argument("logs", nargs="+")
    s.add_argument("-o", "--out", default="bftune_logs")
    s.add_argument("--dump", help="CLI dump (single log only)")
    s.add_argument("--index", type=int, default=None, help="log session index (default: longest)")
    s.add_argument("--window", action="append", metavar="T0:T1", help="also plot this time window (seconds)")
    s.add_argument("--no-spectrogram", action="store_true", help="skip the spectrograms (faster)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--exclude", action="append", metavar="T0:T1",
                   help="ignore this time window (s), e.g. a crash; repeatable")
    s.set_defaults(fn=cmd_logs)

    s = sub.add_parser("plot", help="one figure to look at: time window (sticks, gyro, D-term, motors, rpm) "
                       "or --spectrogram (noise vs throttle)")
    s.add_argument("log")
    s.add_argument("-o", "--out", default="plot.png")
    s.add_argument("--dump")
    s.add_argument("--index", type=int, default=None)
    s.add_argument("--window", action="append", metavar="T0:T1")
    s.add_argument("--spectrogram", action="store_true")
    s.set_defaults(fn=cmd_plot)

    s = sub.add_parser("errspec", help="free-flight tracking-error spectrum of one or more logs (pilot cross-check)")
    s.add_argument("logs", nargs="+")
    s.add_argument("--by-throttle", type=float, metavar="T", help="split each log at this throttle (e.g. 0.35)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--exclude", action="append", metavar="T0:T1",
                   help="ignore this time window (s), e.g. a crash; repeatable")
    s.set_defaults(fn=cmd_errspec)

    s = sub.add_parser("safe", help="list proven-safe tunes, or add them (logs or CLI diffs) to an existing analysis")
    s.add_argument("-o", "--out", default="bftune_out")
    s.add_argument("--log", action="append")
    s.add_argument("--cli", action="append")
    s.set_defaults(fn=cmd_safe)

    s = sub.add_parser("candidate", help="write a candidate tune file (starts from the tune on the quad; --set for variants)")
    _wb_args(s)
    s.add_argument("file")
    s.add_argument("--base", help="start from another candidate file instead of the tune on the quad")
    s.add_argument("--set", action="append", metavar="KEY=VALUE[#reason]",
                   help="change a setting (repeatable); e.g. --set 'dterm_lpf2_type=PT3 # steeper filter'")
    s.set_defaults(fn=cmd_candidate)

    s = sub.add_parser("assess", aliases=["evaluate"], help="verdict, margins, step and noise metrics for candidates")
    _wb_args(s)
    s.add_argument("files", nargs="*", help="candidate files ('set x = y' lines on top of the logged tune)")
    s.add_argument("--with-current", action="store_true", help="also assess the logged tune")
    s.add_argument("--with-safe", action="store_true", help="also assess the proven-safe tunes")
    s.add_argument("--fast", action="store_true", help="skip step simulations")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_assess)

    s = sub.add_parser("sweep", help="tradeoff tables: vary one setting at a time on top of a candidate "
                                     "(several KEY VALUES pairs = several independent sweeps)")
    _wb_args(s)
    s.add_argument("file")
    s.add_argument("pairs", nargs="+", metavar="KEY VALUES",
                   help="setting name and values: start:stop:step, comma list, ';' list for arrays. Repeat pairs.")
    s.add_argument("--steps", action="store_true", help="include stick-response metrics (slower)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser("grid", help="two settings at once (e.g. P x D): verdict, hover Ms, worst PM, noise, objective")
    _wb_args(s)
    s.add_argument("file")
    s.add_argument("key1")
    s.add_argument("values1")
    s.add_argument("key2")
    s.add_argument("values2")
    s.add_argument("--steps", action="store_true", help="also the as-flown step (50 %% time, peak) per cell")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_grid)

    s = sub.add_parser("suggest", help="per-axis P/I/D/d_max proposal with everything else fixed")
    _wb_args(s)
    s.add_argument("files", nargs="+", help="one or more candidates (e.g. filter variants to compare at their best gains)")
    s.add_argument("--axis", action="append", choices=["roll", "pitch", "yaw"])
    s.add_argument("--maxiter", type=int, default=30)
    s.set_defaults(fn=cmd_suggest)

    s = sub.add_parser("ff", help="feedforward table (tracking lag vs overshoot)")
    _wb_args(s)
    s.add_argument("file")
    s.add_argument("--axis", required=True, choices=["roll", "pitch", "yaw"])
    s.add_argument("--values", default="40:260:20")
    s.set_defaults(fn=cmd_ff)

    s = sub.add_parser("noise", help="noise report: measured noise, non-RPM peaks, candidate vs proven-safe level")
    _wb_args(s)
    s.add_argument("file", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_noise)

    s = sub.add_parser("emit", help="final CLI + revert block + report for a candidate (exit 2 on FAIL)")
    _wb_args(s)
    s.add_argument("file")
    s.add_argument("--to", help="write the deliverables here instead of the analysis directory (e.g. the tune folder)")
    s.add_argument("--profile", type=int, choices=range(4), metavar="N",
                   help="PID profile index when no dump gives it (e.g. from the project's earlier dumps; confirm with the pilot)")
    s.add_argument("--experiment", metavar="WHY",
                   help="mark the tune as a supervised experiment (e.g. a noise-headroom flight): stamped on the CLI, "
                        "report and tune.json")
    s.set_defaults(fn=cmd_emit)

    s = sub.add_parser("optimize", help="optional automatic baseline (slow global search, 10-60 min)")
    _wb_args(s)
    s.add_argument("--passes", type=int, default=2, help="coordinate-descent passes per seed")
    s.add_argument("--maxiter", type=int, default=25, help="differential-evolution generations per axis evaluation")
    s.set_defaults(fn=cmd_optimize)

    s = sub.add_parser("synth", help="generate a synthetic chirp flight (testing)")
    s.add_argument("craft", choices=["whoop65", "3.5inch", "5inch", "10inch"])
    s.add_argument("-o", "--output", default="synthetic.pkl")
    s.add_argument("--truth", action="store_true", help="also print the true plant parameters")
    s.add_argument("--chirp-s", type=float, default=12.0)
    s.add_argument("--hover-s", type=float, default=4.0)
    s.add_argument("--freestyle-s", type=float, default=12.0)
    s.add_argument("--repeats", type=int, default=1, help="chirp rounds over all axes")
    s.add_argument("--no-chirp", action="store_true", help="freestyle only (tests the no-chirp path)")
    s.add_argument("--set", action="append", metavar="KEY=VALUE", help="override the twin's flown tune")
    s.add_argument("--frame-mode-hz", type=float, help="plant a structural resonance (noise) at this frequency")
    s.add_argument("--frame-mode-amp", type=float, default=2.0)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_synth)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
