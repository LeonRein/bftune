"""The tune report (emit): one structure rendered as report.html (the deliverable) and report.md."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

from ..flight import AXES
from ..optimize.targets import SAFETY_FLOOR, TARGET_KEYS
from .doc import Doc


def _f(v, fmt="{:.1f}"):
    try:
        return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else fmt.format(v)
    except (TypeError, ValueError):
        return str(v)


def _chain(ai) -> str:
    c = ai.chain
    if not np.isfinite(c.fg_rms_db):
        return "n/a (not checkable)"
    return f"{'ok' if c.passed else 'FAIL'} ({c.fg_rms_db:.2f} dB / {c.fg_rms_deg:.1f}°)"


def plant_section(doc: Doc, an) -> None:
    idn = an.idn
    rows = []
    for a, ai in idn.axes.items():
        p = ai.plant.params
        extra = ", ".join(f"{k}={v:.3g}" for k, v in p.items() if k not in ("K", "tau", "T"))
        rows.append([AXES[a], ai.plant.structure, round(p["K"], 1), round(p["tau"] * 1000, 1), round(p["T"] * 1000, 2),
                     extra, f"{ai.fit_band[0]:.0f}–{ai.fit_band[1]:.0f} Hz", _chain(ai)])
    doc.table(["axis", "model", "K [°/s² per pidSum]", "motor lag τ [ms]", "delay T [ms]", "extra", "measured band",
               "filter-chain check"], rows)
    v = an.validation or {}
    cl = v.get("closed_loop")
    rp = v.get("replay") or {}
    if isinstance(cl, dict) and cl:
        doc.p("Closed-loop chirp check: " + ", ".join(f"{k} {x['rms_db']:.2f} dB / {x['rms_deg']:.1f}°" for k, x in cl.items())
              + ". Replay fit on the freestyle part: " + (", ".join(f"{k} {x['fit_pct']:.0f} %" for k, x in rp.items()) or "n/a") + ".",
              "muted")
    src = getattr(idn, "source", "chirp")
    if src != "chirp":
        doc.note("No chirp in this log: the plant is identified from stick inputs only (gain ±40 %). Predictions are "
                 "relative to the flown tune.", "warn")


def margins_table(doc: Doc, assessment: dict, noise_model: bool) -> None:
    rows = []
    for name, a in assessment.items():
        for ax, e in a["axes"].items():
            h, i, fu, w = e["hover"], e["idle"], e["full"], e["worst"]
            st = e.get("step", {}).get("flick")
            af = e.get("step", {}).get("as_flown")
            rows.append([name, ax, "ok" if not e.get("violations") else "FAIL", _f(h["fc"]), _f(h["pm"], "{:.0f}"),
                         f"{_f(h['ms'], '{:.2f}')} @ {_f(h.get('ms_hz'), '{:.0f}')} Hz", _f(i["fc"]), _f(i["ms"], "{:.2f}"),
                         _f(fu["pm"], "{:.0f}"), f"{_f(w['pm'], '{:.0f}')} / {_f(w['ms'], '{:.2f}')}",
                         _f(e.get("noise_vs_safe"), "{:.2f}") if noise_model else "n/a",
                         f"{af['delay_50_ms']:.1f} ms / +{af['peak_pct']:.0f} %" if af else "—",
                         f"{st.get('stick_lag_ms', st['tracking_lag_ms']):.1f} ms / {st['overshoot_pct']:.0f} %" if st else "—"])
    doc.table(["tune", "axis", "limits", "hover fc [Hz]", "hover PM [°]", "hover Ms", "idle fc [Hz]", "idle Ms",
               "full PM [°]", "worst PM / Ms", "noise × safe", "as flown: 50 % / peak", "typical move: lag / overshoot"],
              rows, status_col=2)


def targets_section(doc: Doc, result: dict, idn) -> None:
    g, src, why = result.get("goals") or {}, result.get("target_sources") or {}, result.get("target_reasons") or {}
    rows = []
    for k in TARGET_KEYS:
        if k in g:
            fl = SAFETY_FLOOR.get(k if k != "noise_budget" else "noise_budget_max")
            rows.append([k, str(g[k]), src.get(k, ""), "" if fl is None else fl, why.get(k, "")])
    u = float(g.get("gain_uncertainty", 0.1))
    doc.p(f"Every case is checked: idle, hover, mid (throttle {g.get('mid_throttle', 0.5)}) and full throttle; base D and "
          f"D-max; the gain +{u * 100:.0f} % / −{(1 - 1 / (1 + 1.25 * u)) * 100:.0f} % and delay "
          f"+{g.get('delay_uncertainty_ms', 0.3)} ms variants ({src.get('gain_uncertainty', 'identification')}); "
          "the dynamic notch at its minimum. Idle limits fall back to the flown tune's idle margins where it misses them. "
          "Targets marked 'convention' are starting points, not measurements; the safety floor is fixed.", "muted")
    doc.table(["target", "value", "source", "safety floor", "reason"], rows)


def write_report(out: Path, an, result: dict, apply_txt: str, revert_txt: str) -> Path:
    idn = an.idn
    new = result["assessment"].get("new", {})
    cur = result["assessment"].get("on_quad") or result["assessment"].get("logged", {})
    v = result.get("verdict")
    gate = new.get("gate", "absolute")
    doc = Doc(f"Tune for {an.craft or 'the quad'}", kind="bftune tune report",
              subtitle=f"Betaflight {an.firmware or '?'} · log {Path(an.log_path).name} · style {result['style']} · "
                       f"{dt.date.today().isoformat()}")

    # ---------------------------------------------------------------- summary
    cards = [("Verdict", v, "pass" if v == "PASS" else "fail"), ("Gate", gate, None)]
    for ax in ("roll", "pitch", "yaw"):
        a0, a1 = cur.get("axes", {}).get(ax), new.get("axes", {}).get(ax)
        if a0 and a1 and a0.get("step") and a1.get("step"):
            f0, f1 = a0["step"].get("as_flown"), a1["step"].get("as_flown")
            if f0 and f1:  # the pilot's own stick inputs replayed: the most realistic stick-response prediction
                cards.append((f"Stick response {ax} (as flown)",
                              f"{f0['delay_50_ms']:.1f} → {f1['delay_50_ms']:.1f} ms · +{f0['peak_pct']:.0f} → +{f1['peak_pct']:.0f} %",
                              None))
            else:
                l0, l1 = a0["step"]["flick"]["stick_lag_ms"], a1["step"]["flick"]["stick_lag_ms"]
                cards.append((f"Stick lag {ax}", f"{l0:.1f} → {l1:.1f} ms", "pass" if l1 <= l0 + 0.05 else "warn"))
    for ax in ("roll", "pitch"):
        a0, a1 = cur.get("axes", {}).get(ax), new.get("axes", {}).get(ax)
        if a0 and a1:
            cards.append((f"Hover Ms {ax}", f"{_f(a0['hover']['ms'], '{:.2f}')} → {_f(a1['hover']['ms'], '{:.2f}')}", None))
    nz = [e.get("noise_vs_safe") for e in new.get("axes", {}).values() if e.get("noise_vs_safe") is not None]
    if nz:
        cards.append(("Motor noise", f"{max(nz):.2f}× proven-safe", None))
    doc.cards(cards)
    if result.get("experiment"):
        doc.note(f"EXPERIMENT: {result['experiment']}. The model passes it, but it goes beyond what has flown on this "
                 "quad (see the notes and the targets' sources); fly it with the checks below.", "warn")
    if v == "PASS" and gate == "relative":
        doc.note("PASS under the relative gate (no chirp): the tune is predicted to be no worse than the flown tune "
                 "wherever the design targets are missed. Fly a chirp set for a verified tune.", "warn")
    elif v == "PASS":
        nb_src = (result.get("target_sources") or {}).get("noise_budget", "")
        doc.note("Every design target and the safety floor are met in every case, with the noise within the budget"
                 + (" given for this tune only (command line), not the stored one" if nb_src == "command line" else "")
                 + ".", "pass")
    else:
        doc.note("The model predicts violated targets - do not fly without reviewing them:", "fail")
        doc.items([f"{ax}: {x}" for ax, xs in result.get("violations", {}).items() for x in xs])
    if not result.get("noise_model", True):
        doc.note("No noise model: motor noise and heat were not checked.", "warn")

    # ---------------------------------------------------------------- changes + CLI
    doc.section("Changes")
    if result.get("unexplained_changes"):
        doc.p("Changes without a stated reason: " + ", ".join(result["unexplained_changes"]), "warn")
    doc.table(["setting", "old", "new", "why"], [[c["setting"], c["old"], c["new"], c["reason"]] for c in result["changes"]])
    doc.section("CLI")
    doc.p("Back up first with `diff all`. Then paste into the Betaflight CLI tab; `save` reboots the flight controller.")
    doc.code(apply_txt, "tune")
    doc.code(revert_txt, "revert (back to the tune on the quad)")
    if result.get("problems"):
        doc.note("Range problems: " + "; ".join(result["problems"]), "fail")

    # ---------------------------------------------------------------- predictions
    doc.section("Predicted behaviour")
    margins_table(doc, result["assessment"], result.get("noise_model", True))
    doc.items([f"note: {n}" for n in new.get("notes", [])])
    meas = (getattr(an, "extra", None) or {}).get("measured_step") or {}
    if meas:
        mod = (getattr(an, "extra", None) or {}).get("model_step") or {}
        doc.p("'As flown' = the model replaying the pilot's own logged stick inputs, turned into a step by the same "
              "estimator as the log. Measured in the log of the flown tune (the check for the 'logged' row): "
              + ", ".join(f"{ax} {m['delay_50_ms']:.1f} ms / +{m['overshoot_pct']:.0f} %"
                          + (f" (model {mod[ax]['delay_50_ms']:.1f} ms / +{mod[ax]['overshoot_pct']:.0f} %)" if ax in mod else "")
                          + f", {m.get('confidence', '?')} confidence" for ax, m in meas.items())
              + ". Trust the model's *changes* in peak more than its absolute peak where the two differ.", "muted")
    plots = result.get("plots", {})
    doc.figure(plots.get("loop"), "Sensitivity |S| (disturbance rejection): lower is better; the peak is what is felt as "
                                  "propwash wobble")
    doc.figure(plots.get("step"), "Stick response: simulated flick with feedforward, RC smoothing and the full controller")
    doc.figure(plots.get("noise"), "Predicted motor noise per throttle band vs the proven-safe level")

    # ---------------------------------------------------------------- targets
    doc.section("Design targets and safety floor")
    targets_section(doc, result, idn)

    # ---------------------------------------------------------------- model
    doc.section("Model of the quad")
    plant_section(doc, an)
    doc.figure("plant_bode.png" if (Path(out) / "plant_bode.png").exists() else None, "Identified plant vs measurement")
    if an.warnings:
        doc.sub("Warnings")
        doc.items(an.warnings)

    # ---------------------------------------------------------------- first flight
    doc.section("First flight")
    doc.items([
        "Hover 20–30 s, land, touch the motors (warm is fine, too hot to hold is not).",
        "Three punch-outs to full throttle: listen for oscillation at the top.",
        "Flips, rolls and a dive with a hard pull-out: look for propwash wobble and bounce-back.",
        "Log the flight with the same blackbox settings and tell the bftune agent how it felt (/bftune:feedback).",
    ])
    return doc.write(out, "report")
