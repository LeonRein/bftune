"""bftune command-line interface.

Workflow (see skills/bf-tune/SKILL.md):
    bftune inspect LOG --dump DUMP
    bftune analyze LOG --dump DUMP -o OUT [--safe-log OTHER.BFL]    # slow step, once (~10 s)
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

STYLES = ["freestyle", "race", "cinematic"]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _wb(a):
    from .workbench import Workbench

    return Workbench(Path(a.out), style=a.style, noise_budget=a.noise_budget)


def cmd_inspect(a) -> int:
    from .flight import AXES, flight_from_log, load_flight
    from .io.bbl import decode
    from .io.dump import config_from_headers, load_dump, merge
    from .pipeline import sanity_warnings
    from .sysid.chirp import find_chirps

    if a.log.endswith(".pkl"):
        fl = load_flight(a.log)
        print(f"{a.log}: synthetic flight, {fl.n} frames, {fl.t[-1]:.1f} s, rate {fl.fs:.0f} Hz, loop {fl.loop_hz:.0f} Hz")
        for r in find_chirps(fl):
            print(f"    chirp {AXES[r.axis]:5s} t={fl.t[r.start]:6.1f}s {r.f_start:.1f}->{r.f_end:.0f} Hz thr {r.throttle:.2f}")
        return 0
    logs = decode(a.log)
    print(f"{a.log}: {len(logs)} log session(s)")
    for lg in logs:
        cfg = merge(load_dump(a.dump) if a.dump else None, config_from_headers(lg))
        fl = flight_from_log(lg, cfg)
        print(f"\n[{lg.index}] {fl.n} frames, {fl.t[-1]:.1f} s, rate {fl.fs:.0f} Hz (loop {fl.loop_hz:.0f} Hz / {fl.log_ratio}),"
              f" firmware {cfg.firmware_version}, craft '{cfg.craft_name}', corrupt frames {lg.stats['corrupt']}")
        if fl.motor_hz is not None:
            print(f"    debug_mode {fl.debug_mode}, high_resolution {lg.headers.get('blackbox_high_resolution')}, "
                  f"motor Hz {np.percentile(fl.motor_hz, 5):.0f}–{np.percentile(fl.motor_hz, 99):.0f}")
        for r in find_chirps(fl):
            print(f"    chirp {AXES[r.axis]:5s} t={fl.t[r.start]:6.1f}s {r.f_start:.1f}->{r.f_end:.0f} Hz thr {r.throttle:.2f}"
                  f"{' (angle/horizon)' if r.level_mode else ''}{' (reconstructed)' if r.reconstructed else ''}")
        for w in sanity_warnings(fl, None):
            print("    warning:", w)
    return 0


def cmd_analyze(a) -> int:
    from .pipeline import analyze

    analyze(a.log, a.dump, Path(a.out), a.index, log=_log, safe_logs=a.safe_log, safe_cli=a.safe_cli)
    return 0


def cmd_safe(a) -> int:
    from .pipeline import load_analysis, safe_tune_from_log, save_analysis, tune_from_cli_text

    an = load_analysis(Path(a.out))
    for p in a.log or []:
        an.safe.append((f"safe:{Path(p).name}", safe_tune_from_log(p)))
    for p in a.cli or []:
        an.safe.append((f"safe:{Path(p).name}", tune_from_cli_text(an.tune, Path(p).read_text())))
    save_analysis(an, Path(a.out))
    print("proven-safe tunes:", ", ".join(n for n, _ in an.safe) or "(none: the logged tune is the only reference)")
    return 0


def cmd_candidate(a) -> int:
    wb = _wb(a)
    tune, reasons = wb.load(a.base) if a.base else (wb.logged, {})
    p = wb.write_candidate(Path(a.file), tune, reasons)
    print(f"wrote {p} (logged tune; edit values and add '# reason' comments)")
    return 0


def cmd_assess(a) -> int:
    from .workbench import format_assessment

    wb = _wb(a)
    res = {}
    if a.with_current:
        res["current"] = wb.assess(wb.logged, steps=not a.fast)
    if a.with_safe:
        for name, t in wb.an.safe:
            res[name] = wb.assess(t, steps=not a.fast)
    for f in a.files:
        t, _ = wb.load(f)
        res[Path(f).name] = wb.assess(t, steps=not a.fast)
    print(json.dumps(res, indent=1, default=float) if a.json else format_assessment(res))
    return 0


def cmd_sweep(a) -> int:
    from .workbench import format_sweep, parse_values

    wb = _wb(a)
    base, _ = wb.load(a.file)
    rows = wb.sweep(base, a.key, parse_values(a.values), steps=a.steps)
    print(json.dumps(rows, indent=1, default=float) if a.json else format_sweep(a.key, rows))
    return 0


def cmd_suggest(a) -> int:
    from .flight import AXES

    wb = _wb(a)
    base, _ = wb.load(a.file)
    axes = [AXES.index(x) for x in a.axis] if a.axis else list(wb.idn.axes)
    res = wb.suggest(base, axes, maxiter=a.maxiter)
    for ax, r in res.items():
        print(f"{ax:5s}: P {r['p']} I {r['i']} D {r['d']} d_max {r['d_max']}  feasible={r['feasible']} "
              f"noise {r['noise_vs_safe']:.2f}x safe  worst PM {r['worst']['pm']:.0f}° Ms {r['worst']['ms']:.2f}")
    print("(proposal with all other settings fixed; judge it, then edit your candidate and run assess)")
    return 0


def cmd_ff(a) -> int:
    from .flight import AXES
    from .workbench import parse_values

    wb = _wb(a)
    base, _ = wb.load(a.file)
    axis = AXES.index(a.axis)
    rows = wb.ff_table(base, axis, parse_values(a.values))
    print(f"feedforward f_{a.axis}: flick = 300°/s in 50 ms, snap = fast move below the max-rate limit")
    print(f"{'F':>5s} | {'flick lag':>9s} {'overshoot':>9s} {'settle':>7s} | {'snap lag':>8s} {'overshoot':>9s}")
    for r in rows:
        fl_, sn = r["flick"], r["snap"]
        print(f"{r['f']:>5} | {fl_['tracking_lag_ms']:8.1f}ms {fl_['overshoot_pct']:8.0f}% {fl_['settle_5pct_ms']:6.0f}ms | "
              f"{sn['tracking_lag_ms']:7.1f}ms {sn['overshoot_pct']:8.0f}%")
    return 0


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
            print(f"    motor-noise proven-safe level {b['safe_level']}" + (f"  candidate {b['candidate']}" if "candidate" in b else ""))
        for p in b["non_rpm_peaks"]:
            print(f"    non-RPM peak {p['axis']:5s} at {p['apparent_hz']:6.1f} Hz (+{p['db_above_floor']:.0f} dB)"
                  + ("  PERSISTENT across throttle" if p.get("persistent") else "  (moves with throttle)"))
    print("persistent non-RPM peaks:", rep.get("persistent_peaks_hz") or "none")
    print(rep["note"])
    return 0


def cmd_emit(a) -> int:
    wb = _wb(a)
    tune, reasons = wb.load(a.file)
    res = wb.emit(tune, reasons, extra={"method": "engineering procedure (bf-tune skill)"}, log=_log)
    print((Path(a.out) / "tune_cli.txt").read_text())
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

    from .synth.quad import CRAFTS, simulate

    fl = simulate(CRAFTS[a.craft], chirp_s=a.chirp_s, hover_s=a.hover_s, seed=a.seed)
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
    s.add_argument("--style", default="freestyle", choices=STYLES)
    s.add_argument("--noise-budget", type=float, default=0.9,
                   help="allowed motor noise as a multiple of the proven-safe level (default 0.9)")


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
        s.add_argument("-o", "--out", default="bftune_out")
        if name == "all":
            s.add_argument("--style", default="freestyle", choices=STYLES)
            s.add_argument("--noise-budget", type=float, default=0.9)
            s.add_argument("--passes", type=int, default=2)
            s.add_argument("--maxiter", type=int, default=25)
        s.set_defaults(fn=fn)

    s = sub.add_parser("safe", help="add proven-safe tunes (logs or CLI diffs) to an existing analysis")
    s.add_argument("-o", "--out", default="bftune_out")
    s.add_argument("--log", action="append")
    s.add_argument("--cli", action="append")
    s.set_defaults(fn=cmd_safe)

    s = sub.add_parser("candidate", help="write an editable candidate tune file (starts from the logged tune)")
    _wb_args(s)
    s.add_argument("file")
    s.add_argument("--base", help="start from another candidate file instead of the logged tune")
    s.set_defaults(fn=cmd_candidate)

    s = sub.add_parser("assess", aliases=["evaluate"], help="verdict, margins, step and noise metrics for candidates")
    _wb_args(s)
    s.add_argument("files", nargs="*", help="candidate files ('set x = y' lines on top of the logged tune)")
    s.add_argument("--with-current", action="store_true", help="also assess the logged tune")
    s.add_argument("--with-safe", action="store_true", help="also assess the proven-safe tunes")
    s.add_argument("--fast", action="store_true", help="skip step simulations")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_assess)

    s = sub.add_parser("sweep", help="tradeoff table: vary one setting on top of a candidate")
    _wb_args(s)
    s.add_argument("file")
    s.add_argument("key", help="CLI setting name, e.g. d_roll, dterm_lpf2_static_hz, tpa_rate")
    s.add_argument("values", help="start:stop:step or comma list (';' list for array values)")
    s.add_argument("--steps", action="store_true", help="include stick-response metrics (slower)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser("suggest", help="per-axis P/I/D/d_max proposal with everything else fixed")
    _wb_args(s)
    s.add_argument("file")
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
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_synth)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
