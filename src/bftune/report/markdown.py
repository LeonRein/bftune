"""Markdown tuning report."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..flight import AXES


def _f(v, fmt="{:.1f}"):
    try:
        return fmt.format(v)
    except (TypeError, ValueError):
        return "—"


def write_report(out: Path, an, result: dict, apply_txt: str, revert_txt: str) -> Path:
    idn = an.idn
    L = []
    L.append(f"# bftune report — {an.craft or 'craft'}")
    L.append("")
    L.append(f"Firmware {an.firmware or '?'} · log `{Path(an.log_path).name}` · style **{result['style']}** · {dt.date.today().isoformat()}")
    L.append("")
    v = result.get("verdict")
    if v:
        L.append(f"## Verdict: **{v}**")
        L.append("")
        if v == "PASS":
            L.append("The model predicts that every robustness constraint (phase margin, gain margin, peak sensitivity, "
                     "delay margin) is met at idle, hover, mid and full throttle, with battery and delay variations, "
                     "and that motor noise stays within the proven-safe budget.")
        else:
            L.append("**The model predicts violated constraints. Review before flying:**")
            L += [f"- {ax}: {x}" for ax, xs in result.get("violations", {}).items() for x in xs]
        if not result.get("noise_model", True):
            L.append("")
            L.append("**No noise model** - motor noise/heat was not checked.")
        L.append("")
    if an.warnings:
        L.append("## Warnings")
        L += [f"- {w}" for w in an.warnings]
        L.append("")
    L.append("## Identified plant (pidSum → gyro)")
    L.append("")
    L.append("| axis | model | K [°/s² per pidSum] | motor lag τ [ms] | delay T [ms] | extra | coherent band | filter-chain check |")
    L.append("|---|---|---|---|---|---|---|---|")
    for a, ai in idn.axes.items():
        p = ai.plant.params
        extra = ", ".join(f"{k}={v:.3g}" for k, v in p.items() if k not in ("K", "tau", "T"))
        L.append(f"| {AXES[a]} | {ai.plant.structure} | {p['K']:.1f} | {p['tau']*1000:.1f} | {p['T']*1000:.2f} | {extra} | "
                 f"{ai.fit_band[0]:.0f}–{ai.fit_band[1]:.0f} Hz | {'ok' if ai.chain.passed else 'FAIL'} "
                 f"({ai.chain.fg_rms_db:.2f} dB / {ai.chain.fg_rms_deg:.1f}°) |")
    L.append("")
    v = an.validation
    if v:
        cl = ", ".join(f"{k}: {x['rms_db']:.2f} dB / {x['rms_deg']:.1f}°" for k, x in v.get("closed_loop", {}).items())
        rp = ", ".join(f"{k}: {x['fit_pct']:.0f}%" for k, x in v.get("replay", {}).items())
        L.append(f"Validation — closed-loop chirp response error: {cl}. Freestyle replay fit: {rp or 'n/a'}.")
        L.append("")
    L.append("![plant](plant_bode.png)")
    L.append("")
    L.append("## Current vs new (model predictions)")
    L.append("")
    L.append("| axis | tune | hover crossover | hover PM | hover GM | hover Ms | idle crossover | full-throttle PM | worst PM (all cases) | worst Ms | noise vs proven-safe |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, per in result["evaluation"].items():
        for ax, e in per.items():
            h, i, fu, w = e["hover"], e["idle"], e["full"], e["worst"]
            nb = _f(e["noise_vs_budget"], "{:.2f}") if result.get("noise_model", True) else "n/a"
            L.append(f"| {ax} | {name} | {_f(h['fc'])} Hz | {_f(h['pm'], '{:.0f}')}° | {_f(h['gm_db'])} dB | {_f(h['ms'], '{:.2f}')} | "
                     f"{_f(i['fc'])} Hz | {_f(fu['pm'], '{:.0f}')}° | {_f(w['pm'], '{:.0f}')}° | {_f(w['ms'], '{:.2f}')} | "
                     f"{nb} |")
    L.append("")
    st = result.get("step", {})
    if st:
        L.append("Stick flick 300°/s in 50 ms (model, with feedforward):")
        L.append("")
        L.append("| axis | tune | tracking lag | overshoot | settle (5%) |")
        L.append("|---|---|---|---|---|")
        for k, m in st.items():
            ax, lab = k.split("/")
            L.append(f"| {ax} | {lab} | {m['tracking_lag_ms']:.1f} ms | {m['overshoot_pct']:.0f}% | {m['settle_5pct_ms']:.0f} ms |")
        L.append("")
    for key in ("loop", "step", "noise"):
        if key in result["plots"]:
            L.append(f"![{key}]({result['plots'][key]})")
            L.append("")
    L.append("## Changes")
    L.append("")
    L.append("| setting | old | new | why |")
    L.append("|---|---|---|---|")
    for c in result["changes"]:
        L.append(f"| `{c['setting']}` | {c['old']} | **{c['new']}** | {c['reason']} |")
    L.append("")
    L.append("## CLI (paste into Betaflight CLI)")
    L.append("")
    L.append("```")
    L.append(apply_txt.rstrip())
    L.append("```")
    L.append("")
    L.append("## Revert")
    L.append("")
    L.append("```")
    L.append(revert_txt.rstrip())
    L.append("```")
    L.append("")
    L.append("## First flight checklist")
    L.append("")
    L += [
        "1. Props on, hover 20–30 s, land, touch the motors (warm is fine, too hot to hold is not).",
        "2. Three punch-outs to full throttle — listen for oscillation at the top.",
        "3. Flips/rolls and a dive with hard pull-out (propwash) — look for wobble.",
        "4. Record the flight with the same blackbox settings (and a chirp set) and run `bftune analyze` again.",
    ]
    if result.get("problems"):
        L.append("")
        L.append("**Range problems:** " + "; ".join(result["problems"]))
    p = out / "report.md"
    p.write_text("\n".join(L) + "\n")
    return p
