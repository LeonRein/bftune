"""The analysis report (analyze): what was measured about this quad, in the same style as every report."""

from __future__ import annotations

from pathlib import Path

from .doc import Doc, evidence
from .tune import plant_section


def write_analysis_report(out: Path, an, summary: dict) -> Path:
    out = Path(out)
    idn = an.idn
    doc = Doc(f"Analysis of {an.craft or 'the quad'}", kind="bftune analysis report",
              subtitle=f"Betaflight {an.firmware or '?'} · log {Path(an.log_path).name} · "
                       f"loop {summary['loop_hz']:.0f} Hz · log rate {summary['log_rate_hz']:.0f} Hz")

    # ---------------------------------------------------------------- summary
    src = summary.get("identification_source", "chirp")
    runs = summary.get("chirp_runs", [])
    cards = [("Model from", "chirp" if src == "chirp" else "stick inputs (±40 %)", "pass" if src == "chirp" else "warn"),
             ("Chirp runs", str(len(runs)), None)]
    for ax, r in (summary["validation"].get("replay") or {}).items():
        cards.append((f"Replay fit {ax}", f"{r['fit_pct']:.0f} %", "pass" if r["fit_pct"] >= 60 else "warn"))
    fl_s = summary.get("flight", {})
    if fl_s.get("hover_motor_hz"):
        cards.append(("Hover motor", f"{fl_s['hover_motor_hz']:.0f} Hz", None))
    if fl_s.get("natural_idle_hz"):
        cards.append(("Idle motor p20", f"{fl_s['natural_idle_hz'].get('20', float('nan')):.0f} Hz", None))
    cards.append(("Proven-safe tunes", str(len(summary.get("safe_tunes", [])) + 1), None))
    doc.cards(cards)
    for w in an.warnings:
        doc.note(w, "warn")

    # ---------------------------------------------------------------- findings
    doc.section("Findings from the flight")
    rows = []
    for f in an.diagnosis or []:
        ev = evidence(f["evidence"])
        rows.append([f["severity"], f["id"], f["summary"], ev])
    if rows:
        doc.table(["severity", "id", "finding", "evidence"], rows, status_col=0)
    else:
        doc.p("No findings.", "muted")

    # ---------------------------------------------------------------- model
    doc.section("Model of the quad")
    plant_section(doc, an)
    doc.figure("plant_bode.png" if (out / "plant_bode.png").exists() else None,
               "Identified plant (pidSum → gyro) vs the chirp measurement; the shaded band is where it was fitted")
    if idn.motor is not None:
        mm = summary.get("motor_model") or {}
        doc.p(f"Motor time constant vs speed; full-throttle authority {mm.get('authority_full_over_hover', float('nan')):.2f}× hover.",
              "muted")
        doc.figure("motor_model.png" if (out / "motor_model.png").exists() else None, "Motor time constant per speed bin")
    if runs:
        doc.sub("Chirp runs")
        doc.table(["axis", "start [s]", "band [Hz]", "throttle", "mode", "reconstructed"],
                  [[r["axis"], round(r["t0"], 1), f"{r['f'][0]:.0f}–{r['f'][1]:.0f}", round(r["throttle"], 2),
                    "level" if r["level_mode"] else "acro", "yes" if r["reconstructed"] else "no"] for r in runs])
    if idn.notes:
        doc.items([f"note: {n}" for n in idn.notes])

    # ---------------------------------------------------------------- noise
    doc.section("Noise")
    bands = summary.get("noise_bands") or []
    if bands:
        doc.table(["throttle", "motor Hz", "D-term RMS r/p/y", "gyro RMS r/p/y", "model fit error r/p/y"],
                  [[round(b["throttle"], 2), round(b["motor_hz"]), " / ".join(f"{x:.2f}" for x in b["measured_dterm_rms"]),
                    " / ".join(f"{x:.2f}" for x in b["measured_gyro_rms"]), " / ".join(f"{x:.2f}" for x in b["fit_error"])]
                   for b in bands])
        doc.p("The noise model predicts these levels for any filter/D setting; fit error > 1 means less certain "
              "predictions in that band. Motor noise is judged against the proven-safe tunes and the noise budget.", "muted")
    else:
        doc.note("No noise model: fly ≥ 20 s of steady flight outside the chirps.", "warn")
    doc.figure("spectrogram.png" if (out / "spectrogram.png").exists() else None,
               "Noise vs throttle: lines following the white motor curves are motor harmonics (RPM filter), fixed-frequency "
               "stripes are frame resonances (dynamic notch / lowpass), broad energy at low throttle is propwash or body motion.")

    # ---------------------------------------------------------------- measured response
    if (out / "log_steps.png").exists():
        doc.section("Stick response as flown")
        doc.figure("log_steps.png", "Setpoint → gyro step response deconvolved from the freestyle part of this log "
                                    "(normalised; 50 % time = stick lag as flown ±1 ms). The model's predicted stick lag "
                                    "for the flown tune should be close to it.")
        ms = summary.get("measured_step") or {}
        mod = summary.get("model_step") or {}
        if ms:
            doc.table(["axis", "measured 50 % [ms]", "model 50 % [ms]", "measured peak / dip [%]", "model peak / dip [%]",
                       "windows", "confidence"],
                      [[ax, m["delay_50_ms"], mod.get(ax, {}).get("delay_50_ms", "—"),
                        f"+{m['overshoot_pct']:.0f} / -{m.get('undershoot_pct', 0):.0f}",
                        (f"+{mod[ax]['overshoot_pct']:.0f} / -{mod[ax]['undershoot_pct']:.0f}" if ax in mod else "—"),
                        m["windows"], m.get("confidence", "")] for ax, m in ms.items()])
            doc.p("Same definition on both sides (a stick step through RC smoothing, FF and the loop). Timing should agree "
                  "within about 1-2 ms; the measured peak is usually higher (real stick moves are faster and rougher than a "
                  "clean step, plus nonlinear effects), so compare the peak between tunes rather than with the model.", "muted")

    doc.section("Settings that shaped the flight")
    t = an.tune
    keys = ["p_roll", "d_roll", "d_max_roll", "p_pitch", "d_pitch", "d_max_pitch", "p_yaw", "tpa_rate", "tpa_breakpoint",
            "gyro_lpf1_static_hz", "gyro_lpf2_static_hz", "dterm_lpf1_static_hz", "dterm_lpf2_static_hz",
            "dyn_notch_count", "dyn_notch_min_hz", "rpm_filter_harmonics", "rpm_filter_min_hz", "dyn_idle_min_rpm",
            "thrust_linear", "feedforward_weight_roll", "feedforward_smooth_factor", "rc_smoothing_auto_factor"]
    rows = [[k, t.values[k]] for k in keys if k in t.values]
    if rows:
        doc.table(["setting", "logged value"], rows)
    doc.p("Full tune: `bftune candidate -o <this folder> cand.txt`.", "muted")
    return doc.write(out, "analysis")


def analysis_figures(out: Path, fl, steps: dict | None = None, model: dict | None = None) -> None:
    """Data figures for the analysis report (spectrogram, measured step response)."""
    from .logs import measured_steps, spectrogram_plot, step_plot

    out = Path(out)
    try:
        spectrogram_plot(fl, out / "spectrogram.png")
    except Exception:  # noqa: BLE001 - figures are optional
        pass
    try:
        st = steps if steps is not None else measured_steps(fl)
        if st:
            step_plot({"flown": st}, out / "log_steps.png", model)
    except Exception:  # noqa: BLE001
        pass
