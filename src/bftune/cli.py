"""bftune command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from . import __version__


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
        print(f"    debug_mode {fl.debug_mode}, high_resolution {lg.headers.get('blackbox_high_resolution')}, "
              f"motor Hz {np.percentile(fl.motor_hz, 5):.0f}–{np.percentile(fl.motor_hz, 99):.0f}" if fl.motor_hz is not None else "")
        for r in find_chirps(fl):
            print(f"    chirp {AXES[r.axis]:5s} t={fl.t[r.start]:6.1f}s {r.f_start:.1f}->{r.f_end:.0f} Hz thr {r.throttle:.2f}"
                  f"{' (angle/horizon)' if r.level_mode else ''}{' (reconstructed)' if r.reconstructed else ''}")
        for w in sanity_warnings(fl, None):
            print("    warning:", w)
    return 0


def cmd_analyze(a) -> int:
    from .pipeline import analyze

    analyze(a.log, a.dump, Path(a.out), a.index)
    return 0


def cmd_optimize(a) -> int:
    from .pipeline import optimize

    optimize(Path(a.out), style=a.style, safe_logs=a.safe_log, safe_cli=a.safe_cli, passes=a.passes,
             maxiter=a.maxiter, noise_budget=a.noise_budget)
    print((Path(a.out) / "tune_cli.txt").read_text())
    return 0


def cmd_all(a) -> int:
    cmd_analyze(a)
    return cmd_optimize(a)


def cmd_evaluate(a) -> int:
    from .optimize.search import Goals, reference_noise
    from .pipeline import evaluate_tunes, load_analysis, safe_tune_from_log, tune_from_cli_text

    an = load_analysis(Path(a.out))
    fl = an.flight()
    tunes = {"current": an.tune}
    for p in a.cli:
        tunes[Path(p).stem] = tune_from_cli_text(an.tune, Path(p).read_text())
    safe = [safe_tune_from_log(p) for p in (a.safe_log or [])]
    goals = Goals.for_style(a.style)
    ref = reference_noise(an.nm, [an.tune] + safe, fl.loop_hz, an.idn) if an.nm.bands else None
    print(json.dumps(evaluate_tunes(an, fl, tunes, goals, ref), indent=1, default=float))
    return 0


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bftune", description="Model-based Betaflight tuning from blackbox chirp logs")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="decode a log and list sessions, chirps and warnings")
    s.add_argument("log")
    s.add_argument("--dump", help="CLI 'dump' or 'diff all' text file")
    s.set_defaults(fn=cmd_inspect)

    for name, fn, hlp in (("analyze", cmd_analyze, "identify plant + noise model from a chirp log"),
                          ("all", cmd_all, "analyze + optimize in one go")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("log")
        s.add_argument("--dump", help="CLI 'dump' or 'diff all' text file (recommended)")
        s.add_argument("--index", type=int, default=None, help="log session index (default: longest)")
        s.add_argument("-o", "--out", default="bftune_out")
        if name == "all":
            _opt_args(s)
        s.set_defaults(fn=fn)

    s = sub.add_parser("optimize", help="search the optimal tune for an analyzed log")
    s.add_argument("-o", "--out", default="bftune_out")
    _opt_args(s)
    s.set_defaults(fn=cmd_optimize)

    s = sub.add_parser("evaluate", help="evaluate CLI tunes (set ... lines) against the identified model")
    s.add_argument("-o", "--out", default="bftune_out")
    s.add_argument("cli", nargs="+", help="text files with 'set x = y' lines (applied on top of the logged tune)")
    s.add_argument("--safe-log", action="append", help="log of another tune that flew with cool motors (noise budget)")
    s.add_argument("--style", default="freestyle", choices=["freestyle", "race", "cinematic"])
    s.set_defaults(fn=cmd_evaluate)

    s = sub.add_parser("synth", help="generate a synthetic chirp flight (testing)")
    s.add_argument("craft", choices=["whoop65", "3.5inch", "5inch", "10inch"])
    s.add_argument("--truth", action="store_true", help="also print the true plant parameters")
    s.add_argument("-o", "--output", default="synthetic.pkl")
    s.add_argument("--chirp-s", type=float, default=12.0)
    s.add_argument("--hover-s", type=float, default=4.0)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_synth)

    a = p.parse_args(argv)
    return a.fn(a)


def _opt_args(s) -> None:
    s.add_argument("--style", default="freestyle", choices=["freestyle", "race", "cinematic"])
    s.add_argument("--safe-log", action="append",
                   help="blackbox log of another tune of the same quad that flew with cool motors (raises the noise budget to its level)")
    s.add_argument("--safe-cli", action="append", help="CLI diff of another proven-safe tune")
    s.add_argument("--noise-budget", type=float, default=1.0, help="multiplier on the proven-safe motor-noise level")
    s.add_argument("--passes", type=int, default=2)
    s.add_argument("--maxiter", type=int, default=25)


if __name__ == "__main__":
    sys.exit(main())
