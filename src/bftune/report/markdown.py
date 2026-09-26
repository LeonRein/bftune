"""Markdown tuning report."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

from ..flight import AXES


def _f(v, fmt="{:.1f}"):
    try:
        return fmt.format(v)
    except (TypeError, ValueError):
        return "—"


def _chain(ai) -> str:
    c = ai.chain
    if not np.isfinite(c.fg_rms_db):
        return "n/a (not checkable)"
    return f"{'ok' if c.passed else 'FAIL'} ({c.fg_rms_db:.2f} dB / {c.fg_rms_deg:.1f}°)"


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
        gate = result.get("assessment", {}).get("new", {}).get("gate", "absolute")
        if v == "PASS" and gate == "relative":
            L.append("**Relative gate (no chirp in the log):** the plant is only known to about ±40 %, so the design "
                     "margins cannot be verified. PASS means the model predicts this tune is **no worse than the flown "
                     "tune** in every case where the design limits are not met, and motor noise stays within the "
                     "budget. Fly a chirp set for a verified tune.")
        elif v == "PASS":
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
                 f"{ai.fit_band[0]:.0f}–{ai.fit_band[1]:.0f} Hz | {_chain(ai)} |")
    L.append("")
    v = an.validation
    if v:
        cl = ", ".join(f"{k}: {x['rms_db']:.2f} dB / {x['rms_deg']:.1f}°" for k, x in v.get("closed_loop", {}).items())
        rp = ", ".join(f"{k}: {x['fit_pct']:.0f}%" for k, x in v.get("replay", {}).items())
        L.append(f"Validation — closed-loop chirp response error: {cl or 'n/a (no chirp)'}. Freestyle replay fit: {rp or 'n/a'}.")
        L.append("")
    L.append("![plant](plant_bode.png)")
    L.append("")
    L.append("## Current vs new (model predictions)")
    L.append("")
    if result.get("method"):
        L.append(f"Method: {result['method']}.")
        L.append("")
    L.append("| axis | tune | verdict | hover crossover | hover PM | hover Ms | idle crossover | idle Ms | full-throttle PM | worst PM | worst Ms | noise vs proven-safe | stick flick: stick→gyro lag / overshoot |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, a in result["assessment"].items():
        for ax, e in a["axes"].items():
            h, i, fu, w = e["hover"], e["idle"], e["full"], e["worst"]
            nb = _f(e.get("noise_vs_safe"), "{:.2f}") if result.get("noise_model", True) else "n/a"
            st = e.get("step", {}).get("flick")
            stt = (f"{st.get('stick_lag_ms', st['tracking_lag_ms']):.1f} ms / {st['overshoot_pct']:.0f}%") if st else "—"
            L.append(f"| {ax} | {name} | {a['verdict']} | {_f(h['fc'])} Hz | {_f(h['pm'], '{:.0f}')}° | {_f(h['ms'], '{:.2f}')} | "
                     f"{_f(i['fc'])} Hz | {_f(i['ms'], '{:.2f}')} | {_f(fu['pm'], '{:.0f}')}° | {_f(w['pm'], '{:.0f}')}° | "
                     f"{_f(w['ms'], '{:.2f}')} | {nb} | {stt} |")
    L.append("")
    notes = result["assessment"].get("new", {}).get("notes", [])
    if notes:
        L += [f"- note: {n}" for n in notes]
        L.append("")
    unc = getattr(idn, "uncertainty", None) or {"k_hi": 1.10, "k_lo": 0.88, "dT": 0.0003}
    L.append(f"Constraints (every case: idle/hover/mid/full throttle, base D and D-max, gain +{(unc['k_hi'] - 1) * 100:.0f} % / "
             f"−{(1 - unc['k_lo']) * 100:.0f} %, +{unc['dT'] * 1000:.1f} ms delay, "
             "dyn notch at its minimum): PM ≥ 45° (35° in uncertainty variants), GM ≥ 6 dB (4 dB), Ms ≤ 2.0 (2.4), "
             "motor noise ≤ 0.9× the level of a tune proven to fly with cool motors.")
    L.append("")
    for key in ("loop", "step", "noise"):
        if key in result["plots"]:
            L.append(f"![{key}]({result['plots'][key]})")
            L.append("")
    L.append("## Changes")
    L.append("")
    if result.get("unexplained_changes"):
        L.append("Changes without a stated reason: " + ", ".join(result["unexplained_changes"]))
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
        "4. Record the flight with the same blackbox settings (and a chirp set) and give it to the bftune agent with how the tune felt (`/bftune:feedback`).",
    ]
    if result.get("problems"):
        L.append("")
        L.append("**Range problems:** " + "; ".join(result["problems"]))
    p = out / "report.md"
    p.write_text("\n".join(L) + "\n")
    from .html import markdown_to_html

    (out / "report.html").write_text(markdown_to_html(p.read_text(), out, title=f"bftune report — {an.craft or 'craft'}"))
    return p
