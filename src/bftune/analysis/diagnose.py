"""Automatic problem finder: what is wrong with, or limiting, this quad in this log?

Every finding carries an id, severity (info | warn | problem), a one-line summary, the
measured evidence and pointers to likely causes and knobs. The agent decides what to do.
Findings are heuristics on flight data only (no model), so they also work on logs without
chirps and on quads whose plant could not be identified.

Scales come from the log itself, not from a 5": frequency bands scale with the control bandwidth measured in the log
(profile.control_scale; the ratios reproduce the bands first tuned on a 5" crossing over near 19 Hz), throttle bands
with the hover throttle, stick thresholds with the pilot's own moves. Severity only ranks findings
for attention; its thresholds are heuristics from a few quads. Judge the evidence, and compare logs of this quad.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from ..flight import AXES, BOX_ANGLE, BOX_HORIZON, Flight, motor_saturated
from ..sysid.chirp import find_chirps
from .profile import control_scale, hover, scaled_band


@dataclass
class Finding:
    id: str
    severity: str  # info | warn | problem
    summary: str
    evidence: dict = field(default_factory=dict)
    causes: list[str] = field(default_factory=list)
    knobs: list[str] = field(default_factory=list)


def _acro_mask(fl: Flight) -> np.ndarray:
    ok = fl.mode(0) & ~fl.mode(BOX_ANGLE) & ~fl.mode(BOX_HORIZON)
    for r in find_chirps(fl):
        ok[r.start : r.end] = False
    return ok


def _segments(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    idx = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    return [(a, b) for a, b in zip(idx[::2], idx[1::2]) if b - a >= min_len]


def log_quality(fl: Flight) -> list[Finding]:
    out = []
    h = fl.log.headers
    runs = find_chirps(fl)
    axes = sorted({r.axis for r in runs})
    if not runs:
        out.append(Finding("no_chirp", "warn", "log has no chirp runs: only a low-confidence model from stick inputs",
                           {}, ["debug_mode not CHIRP and no CHIRP mode switch used"],
                           ["fly the flight protocol (/bftune:flight-plan) for a precise model"]))
    elif len(axes) < 3:
        out.append(Finding("chirp_axes_missing", "warn", f"chirps only on {', '.join(AXES[a] for a in axes)}",
                           {"axes": [AXES[a] for a in axes]}, ["CHIRP switch toggled fewer than 3 times"],
                           ["repeat the chirp set; the axis advances roll→pitch→yaw on each switch-off"]))
    if h.get("blackbox_high_resolution", "0").strip() not in ("1", "ON"):
        out.append(Finding("low_resolution", "info", "blackbox_high_resolution OFF (1 deg/s quantization)", {},
                           [], ["set blackbox_high_resolution = ON for the next log if the SD card keeps up"]))
    if fl.fs < 1500:
        out.append(Finding("low_log_rate", "info", f"log rate {fl.fs:.0f} Hz: noise above {fl.fs/2:.0f} Hz aliased",
                           {"log_rate_hz": round(fl.fs)}, [], ["the noise model handles aliasing; faster logging helps"]))
    if fl.log.stats.get("corrupt", 0) > 5:
        out.append(Finding("corrupt_frames", "warn", f"{fl.log.stats['corrupt']} corrupt frames",
                           dict(fl.log.stats), ["SD card too slow for the logging rate"], ["lower blackbox_sample_rate"]))
    if fl.motor_hz is None:
        out.append(Finding("no_rpm_telemetry", "problem", "no eRPM telemetry: RPM filter cannot work", {},
                           ["dshot_bidir OFF or ESC firmware without bidirectional DShot"],
                           ["enable dshot_bidir (BLHeli_32/AM32/Bluejay) — biggest single filtering improvement"]))
    return out


def saturation(fl: Flight) -> list[Finding]:
    out = []
    armed = fl.mode(0)
    if not armed.any() or fl.motor_raw.size == 0:
        return out
    hi = motor_saturated(fl) & armed
    thr_h, _ = hover(fl)
    part_thr = thr_h + 0.6 * (1 - thr_h)  # well below a punch-out (0.71 on a 5" hovering at 0.27)
    part = hi & (fl.throttle < part_thr)  # saturation below full throttle: authority is missing
    frac = float(hi.mean() / max(armed.mean(), 1e-9))
    pfrac = float(part.mean() / max(armed.mean(), 1e-9))
    if frac > 0:
        segs = _segments(hi, int(0.02 * fl.fs))
        thr = float(np.median(fl.throttle[hi])) if hi.any() else 0.0
        sev = "problem" if pfrac > 0.03 else ("warn" if pfrac > 0.01 else "info")
        out.append(Finding("motor_saturation", sev,
                           f"a motor is at its maximum for {100*frac:.1f} % of armed time "
                           f"({100*pfrac:.1f} % below {100*part_thr:.0f} % throttle)",
                           {"fraction": round(frac, 4), "fraction_below_part_throttle": round(pfrac, 4),
                            "part_throttle": round(part_thr, 2), "episodes": len(segs), "median_throttle": round(thr, 2)},
                           ["punch-outs at full throttle (normal)", "too much P/D (or FF) for the available authority",
                            "heavy build / weak motors (saturation at part throttle)"],
                           ["TPA", "check that saturation is only at full throttle", "motor_output_limit"]))
    # motor imbalance at hover: one motor consistently higher -> CG offset, bent prop, weak motor
    calm = np.all(np.abs(fl.setpoint[:, :3]) < 30, axis=1)
    no_chirp = np.ones(fl.n, bool)
    for r in find_chirps(fl):
        no_chirp[r.start : r.end] = False
    hov = armed & calm & no_chirp & (np.abs(fl.throttle - thr_h) < 0.05)
    if hov.sum() > fl.fs * 2:
        med = np.median(fl.motor[hov], axis=0)
        tl = fl.cfg.int("thrust_linear", 0) if fl.cfg is not None else 0
        if tl > 0:  # compare on the thrust scale: undo the firmware curve (pid.c pidApplyThrustLinearization)
            e, m = tl / 100.0, np.linspace(0, 1, 2001)
            med = np.interp(med, m * (1 + e * (1 - m) * (1 + e * (1 - 2 * m))), m)
        spread = float((med.max() - med.min()) / max(med.mean(), 1e-6))
        # always reported: the spread and trim are data; the severity below is a heuristic
        ev = {"median_outputs": np.round(med, 3).tolist()}
        where = ""
        quadx = len(med) == 4 and (fl.cfg is None or fl.cfg.str("mixer", "QUADX").upper() in ("QUADX", ""))
        if quadx:  # Betaflight QUADX mixer order (mixer.c): 1 rear-right, 2 front-right, 3 rear-left, 4 front-left
            cols = {"roll": np.array([-1, -1, 1, 1]), "pitch": np.array([1, -1, 1, -1]),
                    "yaw": np.array([-1, 1, 1, -1])}
            trim = {k: float(med @ c / 4) / max(float(med.mean()), 1e-6) for k, c in cols.items()}
            ev["trim_pct"] = {k: round(100 * v, 1) for k, v in trim.items()}
            desc = {"roll": ("left motors work harder: CG left of centre", "right motors work harder: CG right of centre"),
                    "pitch": ("rear motors work harder: CG behind centre", "front motors work harder: CG ahead of centre"),
                    "yaw": ("motors 2+3 work harder (a constant yaw correction: twisted motor, prop mismatch)",
                            "motors 1+4 work harder (a constant yaw correction: twisted motor, prop mismatch)")}
            k = max(trim, key=lambda q: abs(trim[q]))
            where = (f"; mostly {k}: {desc[k][0 if trim[k] > 0 else 1]} (QUADX mixer positions; "
                     "motor_output_reordering only remaps output pins, not these positions)")
        ev["spread_pct"] = round(100 * spread, 1)
        if tl > 0:
            ev["thrust_scale"] = f"thrust_linear {tl} undone, so logs with different thrust_linear compare"
        out.append(Finding("motor_imbalance", "info" if spread < 0.10 else ("warn" if spread < 0.3 else "problem"),
                           f"motor outputs at hover differ by {100*spread:.0f} %{where}", ev,
                           ["CG offset (battery position)", "bent/chipped prop", "weak motor or bearing"],
                           ["check props/motors; move battery; rerun after fixing (tuning cannot fix this)"]))
    return out


def desync(fl: Flight) -> list[Finding]:
    """rpm collapses, classified by what the motor was commanded (see analysis.motors)."""
    from .motors import rpm_events

    ev = rpm_events(fl)
    if not ev:
        return []
    real = [e for e in ev if e["kind"] in ("stall", "unclear")]
    mixer = sum(e["kind"] == "mixer" for e in ev)
    crash = sum(e["kind"] == "crash" for e in ev)
    hits = [e for e in ev if e["kind"] == "impact"]
    out = []
    if hits:
        out.append(Finding("motor_dropout_after_impact", "warn",
                           f"{len(hits)} rpm drop(s) right after an impact (all axes jolted first): motor "
                           f"{', '.join(str(e['motor']) for e in hits)} at t={', '.join(str(e['t']) for e in hits)} s",
                           {"events": hits}, ["prop strike on something", "the hit bent the prop or shaft"],
                           ["check that prop for nicks and spin the motor by hand; not a tuning issue unless it repeats "
                            "without a hit"]))
    if real:
        stalls = [e for e in real if e["kind"] == "stall"]
        out.append(Finding("motor_stall", "problem" if stalls else "warn",
                           f"{len(real)} event(s) where a motor's rpm collapsed although it was commanded up "
                           f"(motor {', '.join(str(e['motor']) for e in real)} at t={', '.join(str(e['t']) for e in real)} s)",
                           {"events": real},
                           ["ESC desync (timing, demag)", "prop strike / damaged prop", "bearing or bell rub", "telemetry dropout"],
                           ["inspect prop, bell and bearing of that motor", "ESC demag/timing settings",
                            "raise dyn_idle_min_rpm", "`bftune motors LOG` for the full review"]))
    if mixer or crash:
        out.append(Finding("rpm_dips_explained", "info",
                           f"{mixer} rpm dip(s) where the mixer commanded the motor down (hard flips/rolls: normal)"
                           + (f", {crash} during a crash" if crash else ""),
                           {"mixer": mixer, "crash": crash,
                            "crash_at_s": sorted({e["t"] for e in ev if e["kind"] == "crash"})},
                           [], ["crashes are left out of the other findings and of `analyze` automatically"]
                           if crash else []))
    return out


def oscillation(fl: Flight) -> list[Finding]:
    """Resonant peaks in the tracking-error spectrum (setpoint - gyro), per throttle band.

    Spectra are averaged over all acro windows of a throttle band; a log-log linear trend is fitted
    over 15-200 Hz and local maxima more than 6 dB above the trend are reported. A sensitivity peak
    (low phase margin) or a resonance shows up here; random maneuvers average out.
    """
    out = []
    ok = _acro_mask(fl)
    n = int(0.5 * fl.fs)
    if ok.sum() < 8 * n:
        return out
    f = np.fft.rfftfreq(n, 1 / fl.fs)
    w = np.hanning(n)
    thr_h, hz_h = hover(fl)
    fc, _ = control_scale(fl)
    # from the crossover region up to the motor fundamental (15-200 Hz on a 5")
    f_lo, f_hi = scaled_band(fc, 0.8, max(10.0, 1.2 * (hz_h or 0) / fc), fl.fs, (15.0, 200.0))
    m = (f >= f_lo) & (f <= min(f_hi, fl.fs / 2 - 10))
    lf = np.log(f[m])
    lo_t, hi_t = max(0.02, thr_h - 0.04), thr_h + 0.35 * (1 - thr_h)  # 0.2 / 0.5 on a 5" hovering at 0.24
    bands = (("low throttle", 0.0, lo_t), ("mid throttle", lo_t, hi_t), ("high throttle", hi_t, 1.01))
    for axis in range(3):
        found = []
        for label, lo, hi in bands:
            specs = []
            for a, b in _segments(ok, n):
                for s in range(a, b - n, n // 2):
                    if lo <= np.mean(fl.throttle[s : s + n]) < hi:
                        e = fl.setpoint[s : s + n, axis] - fl.gyro[s : s + n, axis]
                        specs.append(np.abs(np.fft.rfft((e - e.mean()) * w)) ** 2)
            if len(specs) < 8:
                continue
            P = 10 * np.log10(np.mean(specs, axis=0)[m] + 1e-9)
            trend = np.polyval(np.polyfit(lf, P, 1), lf)
            r = P - trend
            k = int(np.argmax(r))
            if 0 < k < len(r) - 1 and r[k] > 6.0 and r[k] >= r[k - 1] and r[k] >= r[k + 1]:
                found.append((label, float(f[m][k]), float(r[k]), len(specs), lo, hi))
        for label, fr, db, nwin, lo, hi in found:
            ev = {"freq_hz": round(fr, 1), "db_above_trend": round(db, 1), "windows": nwin,
                  "band_hz": [round(f_lo), round(f_hi)], "throttle_band": label}
            causes = ["sensitivity peak from low phase margin (P/D too high for the delay, too much filter lag)",
                      "mechanical resonance not filtered"]
            if label.startswith("high"):
                causes.append("TPA insufficient (high throttle)")
            extra = ""
            if fr > 5 * fc:  # far above the crossover: not the loop's sensitivity peak
                ev["above_control_band"] = True
                causes = ["a motor line not fully notched (RPM filter Q/weights/harmonics, telemetry)",
                          "vibration or a frame resonance (prop balance, bent shaft, soft mount, loose part)"]
                extra = f"; far above the control band (~{fc:.0f} Hz): not the loop's sensitivity peak"
                selb = fl.mode(0) & (fl.throttle >= lo) & (fl.throttle < hi)
                if fl.motor_hz is not None and fl.motor_hz.size and selb.sum() > fl.fs:
                    mhz = float(np.median(fl.motor_hz[selb].mean(axis=1)))
                    for h in (1, 2, 3):
                        fa = abs(((h * mhz + fl.fs / 2) % fl.fs) - fl.fs / 2)  # where that harmonic lands after aliasing
                        if abs(fr - fa) < 0.12 * max(fa, 1.0):
                            ev["motor_harmonic"] = h
                            extra += (f", at the motor {'fundamental' if h == 1 else f'harmonic x{h}'} of this band "
                                      f"(~{fa:.0f} Hz{' aliased' if h * mhz > fl.fs / 2 else ''})")
                            break
            if label.startswith("low") and fl.motor_hz is not None and not ev.get("above_control_band"):
                lowthr = fl.mode(0) & (fl.throttle < lo_t)
                if lowthr.sum() > fl.fs:
                    idle = np.percentile(fl.motor_hz[lowthr].mean(axis=1), [10, 50])
                    rmin = fl.cfg.int("rpm_filter_min_hz", 100)
                    ev.update({"idle_motor_hz_p10_p50": [round(float(x), 1) for x in idle], "rpm_filter_min_hz": rmin})
                    if 0.85 * idle[0] <= fr <= 1.15 * idle[1]:
                        line = _motor_line_db(fl, axis, lowthr)
                        ev["motor_line_db"] = round(line, 1)
                        if rmin > idle[0] and line >= 3.5:
                            extra = (f"; matches the idle motor frequency ({idle[0]:.0f}-{idle[1]:.0f} Hz) below rpm_filter_min_hz "
                                     f"{rmin}, and the motor line is visible in the filtered gyro (+{line:.1f} dB)")
                            causes.insert(0, "motor fundamental at idle not notched (rpm_filter_min_hz above the idle motor Hz)")
                        elif rmin > idle[0]:
                            extra = (f"; near the idle motor frequency ({idle[0]:.0f}-{idle[1]:.0f} Hz, below rpm_filter_min_hz "
                                     f"{rmin}) but no distinct motor line in the filtered gyro ({line:+.1f} dB): more likely "
                                     "body motion; verify before lowering rpm_filter_min_hz")
                            causes.insert(0, "body motion at low throttle (broad hump) - or the un-notched motor fundamental")
                        else:
                            extra = f"; near the idle motor frequency ({idle[0]:.0f}-{idle[1]:.0f} Hz), which the RPM filter covers"
                            causes.insert(0, "body motion at low throttle (broad hump, not a motor line) or idle-case loop peak")
            sev = "info" if ev.get("above_control_band") else ("problem" if db > 10 else "warn")
            out.append(Finding(f"resonance_{AXES[axis]}_{label.split()[0]}", sev,
                               f"{AXES[axis]}: tracking-error peak at ~{fr:.0f} Hz, +{db:.0f} dB above trend ({label}){extra}",
                               ev, causes,
                               ["compare with assess 'Ms' and its frequency; reduce P/D or filter lag; TPA; dyn notch",
                                "low throttle: rpm_filter_min_hz vs idle motor Hz (assess notes)"]))
    return out


def _motor_line_db(fl: Flight, axis: int, mask: np.ndarray, win_s: float = 0.25) -> float:
    """How strongly the filtered gyro follows the motors' own rotation frequency (order tracking): energy
    demodulated at the instantaneous motor frequency vs. at +-7/13 Hz offsets. > ~3.5 dB = a motor line."""
    x = fl.gyro[:, axis] - np.mean(fl.gyro[mask, axis])
    n = int(win_s * fl.fs)
    idx = np.flatnonzero(mask)
    starts = [s for s in range(0, len(idx) - n, n) if idx[s + n - 1] - idx[s] == n - 1][:400]
    if not starts:
        return 0.0
    res = []
    for off in (0.0, 7.0, -7.0, 13.0, -13.0):
        tot = 0.0
        for mot in range(fl.motor_hz.shape[1]):
            z = x * np.exp(-2j * np.pi * np.cumsum(fl.motor_hz[:, mot] + off) / fl.fs)
            tot += sum(abs(z[idx[s] : idx[s] + n].mean()) ** 2 for s in starts)
        res.append(tot)
    return float(10 * np.log10(res[0] / max(np.mean(res[1:]), 1e-30)))


def propwash(fl: Flight) -> list[Finding]:
    """Wobble at low throttle after throttle chops (dives, flips): the propwash signature.

    Uses the 15-80 Hz band of the roll/pitch tracking error, so stick-driven tracking lag (which
    lives below ~10 Hz) does not count. Reported as absolute RMS and relative to calm cruise.
    """
    from scipy.signal import butter, sosfiltfilt

    ok = _acro_mask(fl)
    thr = fl.throttle
    dthr = np.gradient(thr) * fl.fs
    thr_h, _ = hover(fl)
    b_lo, b_hi = scaled_band(control_scale(fl)[0], 0.8, 4.2, fl.fs, (15.0, 80.0))  # 15-80 Hz on a 5" (fc ~19 Hz)
    sos = butter(2, [b_lo, b_hi], "bandpass", fs=fl.fs, output="sos")
    e = fl.setpoint[:, :2] - fl.gyro[:, :2]
    eb = np.zeros_like(e)
    for a, b in _segments(ok, int(0.5 * fl.fs)):
        eb[a:b] = sosfiltfilt(sos, e[a:b], axis=0)
    n = int(0.25 * fl.fs)
    chop, flip, calm, worst = [], [], [], []
    for a, b in _segments(ok, n * 2):
        for s in range(a + n // 2, b - n - n // 2, n // 2):
            rms = float(np.sqrt(np.mean(eb[s : s + n] ** 2)))
            sp_move = np.std(fl.setpoint[s : s + n, :2])
            t = np.mean(thr[s : s + n])
            if t < thr_h and np.min(dthr[max(a, s - n) : s + n]) < -1.5:
                if sp_move < 100:
                    chop.append(rms)
                    worst.append((rms, float(fl.t[s])))
                elif sp_move > 200:
                    flip.append(rms)
                    worst.append((rms, float(fl.t[s])))
            elif thr_h <= t < thr_h + 0.25 and sp_move < 30:
                calm.append(rms)
    if len(calm) < 3 or len(chop) + len(flip) < 3:
        return []
    c0 = float(np.median(calm))
    ev = {"calm_rms_deg_s": round(c0, 2), "band_hz": [round(b_lo), round(b_hi)],
          "worst_at_s": [round(t, 1) for _, t in sorted(worst, reverse=True)[:3]]}  # for `bftune plot --window`
    parts = []
    for name, v in (("chops", chop), ("flips_rolls", flip)):
        if len(v) >= 3:
            ev[name] = {"windows": len(v), "rms_deg_s": round(float(np.median(v)), 2),
                        "ratio_to_calm": round(float(np.median(v)) / max(c0, 1e-6), 2)}
            parts.append(f"{name.replace('_', '/')} {np.median(v):.1f} deg/s ({len(v)} windows)")
    if "chops" in ev:
        r, pw = ev["chops"]["ratio_to_calm"], ev["chops"]["rms_deg_s"]
        sev = "problem" if r > 4 and pw > 4 else ("warn" if r > 2.5 and pw > 2 else "info")
    else:
        sev = "info"
    return [Finding("propwash", sev,
                    f"{b_lo:.0f}-{b_hi:.0f} Hz wobble at low throttle after chops: {'; '.join(parts)}; calm cruise "
                    f"{c0:.1f} deg/s",
                    ev,
                    ["low authority at low throttle (motor lag, idle rpm)", "sensitivity peak in 20-60 Hz",
                     "too little D",
                     "NOTE: depends strongly on how hard the pilot flew; compare logs only stratum by stratum"],
                    ["dyn_idle_min_rpm", "thrust_linear", "D / d_max", "compare idle/hover Ms and its frequency",
                     "errspec 20-60 Hz band"])]


def bounce_back(fl: Flight) -> list[Finding]:
    """Overshoot/opposite-sign rebound at the end of fast rolls/flips."""
    out = []
    ok = _acro_mask(fl)
    for axis in range(2):
        sp = fl.setpoint[:, axis]
        if not ok.any():
            continue
        fast_th = max(150.0, 0.5 * float(np.percentile(np.abs(sp[ok]), 99.5)))  # the pilot's own fast moves
        fast = ok & (np.abs(sp) > fast_th)
        ends, at, amps = [], [], []
        for a, b in _segments(fast, int(0.08 * fl.fs)):
            e_end = b + int(0.15 * fl.fs)
            if e_end < fl.n and np.all(np.abs(sp[b : e_end]) < 60):
                sign = np.sign(np.mean(sp[a:b]))
                peak_opp = float(np.max(-sign * fl.gyro[b:e_end, axis]))
                ends.append(peak_opp)
                amps.append(float(np.max(np.abs(sp[a:b]))))
                at.append((peak_opp, float(fl.t[b])))
        if len(ends) >= 3:
            med = float(np.median(ends))
            rel = 100 * med / max(float(np.median(amps)), 1.0)
            if med > 0:
                sev = "info" if rel < 8 else ("warn" if rel < 20 else "problem")
                out.append(Finding(f"bounce_back_{AXES[axis]}", sev,
                                   f"{AXES[axis]}: median {med:.0f} deg/s opposite rebound after fast moves "
                                   f"({rel:.0f} % of the move, moves > {fast_th:.0f} deg/s)",
                                   {"events": len(ends), "median_rebound_deg_s": round(med, 1), "rebound_pct_of_move": round(rel, 1),
                                    "worst_at_s": [round(t, 1) for _, t in sorted(at, reverse=True)[:3]]},
                                   ["I-term windup (iterm_relax too high/cutoff)", "FF overshoot", "low damping (D)"],
                                   ["iterm_relax_cutoff", "feedforward", "D"]))
    return out


def throttle_punch(fl: Flight) -> list[Finding]:
    """Attitude error during fast throttle increases vs calm cruise: what anti-gravity is for."""
    from scipy.signal import butter, sosfiltfilt

    ok = _acro_mask(fl)
    dthr = np.gradient(fl.throttle) * fl.fs
    lp = scaled_band(control_scale(fl)[0], 0.53, 0.53, fl.fs, (10.0, 10.0))[1]  # drift band (I-term job): 10 Hz on a 5"
    sos = butter(2, lp, "lowpass", fs=fl.fs, output="sos")
    e = fl.setpoint[:, :2] - fl.gyro[:, :2]
    el = np.zeros_like(e)
    for a, b in _segments(ok, int(0.5 * fl.fs)):
        el[a:b] = sosfiltfilt(sos, e[a:b], axis=0)
    n = int(0.2 * fl.fs)
    punch, calm, at = [], [], []
    for a, b in _segments(ok, 2 * n):
        for s in range(a, b - n, n // 2):
            sp = np.std(fl.setpoint[s : s + n, :2])
            if sp > 40:
                continue
            rms = float(np.sqrt(np.mean(el[s : s + n] ** 2)))
            if np.max(dthr[s : s + n]) > 3.0:
                punch.append(rms)
                at.append((rms, float(fl.t[s])))
            elif abs(np.mean(dthr[s : s + n])) < 0.3:
                calm.append(rms)
    if len(punch) < 3 or len(calm) < 3:
        return []
    r = float(np.median(punch) / max(np.median(calm), 1e-6))
    sev = "warn" if r > 3 and np.median(punch) > 5 else "info"
    return [Finding("throttle_punch_dip", sev,
                    f"low-frequency attitude error during fast throttle rises: {np.median(punch):.1f} deg/s "
                    f"({r:.1f}x calm, {len(punch)} windows, sticks near centre)",
                    {"punch_rms_deg_s": round(float(np.median(punch)), 2), "ratio_to_calm": round(r, 2),
                     "windows": len(punch), "worst_at_s": [round(t, 1) for _, t in sorted(at, reverse=True)[:3]]},
                    ["I-term too slow for throttle-induced torque changes", "CG offset / motor imbalance"],
                    ["anti_gravity_gain (and anti_gravity_p_gain)", "I", "fix CG first if motor_imbalance is reported"])]


def pidsum_clipping(fl: Flight) -> list[Finding]:
    armed = fl.mode(0)
    if armed.sum() < fl.fs:
        return []
    lim = np.array([fl.cfg.int("pidsum_limit", 500), fl.cfg.int("pidsum_limit", 500), fl.cfg.int("pidsum_limit_yaw", 400)])
    frac = np.mean(np.abs(fl.pidsum[armed]) >= 0.98 * lim, axis=0)
    out = []
    for a in range(3):
        if frac[a] > 0.005:
            out.append(Finding(f"pidsum_clipping_{AXES[a]}", "warn" if frac[a] > 0.02 else "info",
                               f"{AXES[a]}: PID sum at its limit {100*frac[a]:.1f} % of armed time",
                               {"fraction": round(float(frac[a]), 4), "limit": int(lim[a])},
                               ["authority-limited moves (yaw spin-up, hard snaps)", "too much P/FF for the craft"],
                               ["pidsum_limit / pidsum_limit_yaw", "FF", "check motor_saturation first"]))
    return out


def heat_risk(fl: Flight) -> list[Finding]:
    """High-frequency content of the motor commands: the main driver of motor heat."""
    armed = fl.mode(0)
    if fl.motor.size == 0 or armed.sum() < fl.fs * 2:
        return []
    from scipy.signal import butter, sosfiltfilt

    fc = scaled_band(control_scale(fl)[0], 3.7, 3.7, fl.fs, (70.0, 70.0))[1]  # above the control band: 70 Hz on a 5"
    sos = butter(2, fc, "highpass", fs=fl.fs, output="sos")
    hp = sosfiltfilt(sos, fl.motor, axis=0)[armed]
    rms = float(np.sqrt(np.mean(hp[20:-20] ** 2)) * 100)
    # no severity from an absolute level: it depends on craft, log rate and pilot. Compare logs of this quad
    # and the pilot's motor temperatures (build a table in quad.md).
    return [Finding("motor_hf_noise", "info", f"motor-command noise above {fc:.0f} Hz: {rms:.2f} % RMS",
                    {"hf_rms_pct": round(rms, 3), "above_hz": round(fc)}, ["D-term noise", "weak filtering", "resonance"],
                    ["compare with other logs of this quad and the motor temperatures the pilot reported"])]


def diagnose(fl: Flight) -> list[dict]:
    """All findings. Detected crashes are reported (desync / rpm_dips_explained) and left out of every other
    statistic, so a crash can't pose as propwash, a resonance or a punch dip."""
    from ..flight import without_crashes

    findings: list[Finding] = []
    flx, cw = without_crashes(fl)
    if cw:
        findings.append(Finding("crash_excluded", "info", "crash(es) left out of the other findings: "
                                + ", ".join(f"{a:g}-{b:g} s" for a, b in cw), {"windows_s": cw}))
    for fn in (log_quality, saturation, desync, oscillation, propwash, bounce_back, throttle_punch, pidsum_clipping,
               heat_risk):
        try:
            findings += fn(fl if fn is desync else flx)
        except Exception as e:  # a failing heuristic must not hide the others
            findings.append(Finding(f"{fn.__name__}_error", "info", f"{fn.__name__} check failed: {e}"))
    order = {"problem": 0, "warn": 1, "info": 2}
    return [_plain(asdict(f)) for f in sorted(findings, key=lambda f: order.get(f.severity, 3))]


def _plain(x):
    """numpy scalars/arrays -> Python types, so evidence prints as `27.0`, not `np.float64(27.0)`."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return _plain(x.tolist())
    if isinstance(x, np.generic):
        return x.item()
    return x


def format_findings(findings: list[dict], verbose: bool = False) -> str:
    if not findings:
        return "no findings"
    lines = []
    for f in findings:
        lines.append(f"[{f['severity']:7s}] {f['id']}: {f['summary']}")
        if verbose:
            if f["evidence"]:
                lines.append(f"           evidence: {f['evidence']}")
            if f["causes"]:
                lines.append(f"           causes:   {'; '.join(f['causes'])}")
            if f["knobs"]:
                lines.append(f"           knobs:    {'; '.join(f['knobs'])}")
    return "\n".join(lines)
