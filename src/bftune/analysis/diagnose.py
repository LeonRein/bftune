"""Automatic problem finder: what is wrong with, or limiting, this quad in this log?

Every finding carries an id, severity (info | warn | problem), a one-line summary, the
measured evidence and pointers to likely causes and knobs. The agent decides what to do.
Findings are heuristics on flight data only (no model), so they also work on logs without
chirps and on quads whose plant could not be identified.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from ..flight import AXES, BOX_ANGLE, BOX_HORIZON, Flight
from ..sysid.chirp import find_chirps


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
    hi = np.any(fl.motor_raw >= 2046, axis=1) & armed
    part = hi & (fl.throttle < 0.75)  # saturation below full throttle: authority is missing
    frac = float(hi.mean() / max(armed.mean(), 1e-9))
    pfrac = float(part.mean() / max(armed.mean(), 1e-9))
    if frac > 0.01:
        segs = _segments(hi, int(0.02 * fl.fs))
        thr = float(np.median(fl.throttle[hi])) if hi.any() else 0.0
        sev = "problem" if pfrac > 0.03 else ("warn" if pfrac > 0.01 else "info")
        out.append(Finding("motor_saturation", sev,
                           f"a motor is at 100 % for {100*frac:.1f} % of armed time "
                           f"({100*pfrac:.1f} % below 75 % throttle)",
                           {"fraction": round(frac, 4), "fraction_below_75pct_throttle": round(pfrac, 4),
                            "episodes": len(segs), "median_throttle": round(thr, 2)},
                           ["punch-outs at full throttle (normal)", "too much P/D (or FF) for the available authority",
                            "heavy build / weak motors (saturation at part throttle)"],
                           ["TPA", "check that saturation is only at full throttle", "motor_output_limit"]))
    # motor imbalance at hover: one motor consistently higher -> CG offset, bent prop, weak motor
    calm = np.all(np.abs(fl.setpoint[:, :3]) < 30, axis=1)
    no_chirp = np.ones(fl.n, bool)
    for r in find_chirps(fl):
        no_chirp[r.start : r.end] = False
    hov = armed & calm & no_chirp & (np.abs(fl.throttle - np.median(fl.throttle[armed])) < 0.05)
    if hov.sum() > fl.fs * 2:
        med = np.median(fl.motor[hov], axis=0)
        spread = float((med.max() - med.min()) / max(med.mean(), 1e-6))
        if spread > 0.10:
            ev = {"median_outputs": np.round(med, 3).tolist()}
            where = ""
            if len(med) == 4:  # Betaflight QUADX mixer order: 1 rear-right, 2 front-right, 3 rear-left, 4 front-left
                cols = {"roll": np.array([-1, -1, 1, 1]), "pitch": np.array([1, -1, 1, -1]),
                        "yaw": np.array([-1, 1, 1, -1])}
                trim = {k: float(med @ c / 4) / max(float(med.mean()), 1e-6) for k, c in cols.items()}
                ev["trim_pct"] = {k: round(100 * v, 1) for k, v in trim.items()}
                desc = {"roll": ("left motors work harder: CG left of centre", "right motors work harder: CG right of centre"),
                        "pitch": ("rear motors work harder: CG behind centre", "front motors work harder: CG ahead of centre"),
                        "yaw": ("motors 2+3 work harder (a constant yaw correction: twisted motor, prop mismatch)",
                                "motors 1+4 work harder (a constant yaw correction: twisted motor, prop mismatch)")}
                k = max(trim, key=lambda q: abs(trim[q]))
                where = f"; mostly {k}: {desc[k][0 if trim[k] > 0 else 1]} (assumes QUADX motor order)"
            out.append(Finding("motor_imbalance", "warn" if spread < 0.3 else "problem",
                               f"motor outputs at hover differ by {100*spread:.0f} %{where}", ev,
                               ["CG offset (battery position)", "bent/chipped prop", "weak motor or bearing"],
                               ["check props/motors; move battery; rerun after fixing (tuning cannot fix this)"]))
    return out


def desync(fl: Flight) -> list[Finding]:
    if fl.motor_hz is None:
        return []
    armed = fl.mode(0) & (fl.throttle > 0.1)
    if armed.sum() < fl.fs:
        return []
    # one motor far below the others while flying (not at idle)
    drop = armed & np.any(fl.motor_hz < 0.3 * np.median(fl.motor_hz, axis=1, keepdims=True), axis=1)
    n = len(_segments(drop, max(2, int(0.005 * fl.fs))))
    if n:
        return [Finding("possible_desync", "problem" if n > 1 else "warn", f"{n} event(s) where one motor's rpm collapsed while others ran",
                        {"events": n}, ["ESC desync (timing, demag)", "prop strike", "telemetry dropout"],
                        ["check ESC timing/demag settings", "raise dyn_idle_min_rpm", "inspect props"])]
    return []


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
    m = (f >= 15) & (f <= min(200, fl.fs / 2 - 10))
    lf = np.log(f[m])
    bands = (("low throttle", 0.0, 0.2), ("mid throttle", 0.2, 0.5), ("high throttle", 0.5, 1.01))
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
                found.append((label, float(f[m][k]), float(r[k]), len(specs)))
        for label, fr, db, nwin in found:
            out.append(Finding(f"resonance_{AXES[axis]}_{label.split()[0]}", "problem" if db > 10 else "warn",
                               f"{AXES[axis]}: tracking-error peak at ~{fr:.0f} Hz, +{db:.0f} dB above trend ({label})",
                               {"freq_hz": round(fr, 1), "db_above_trend": round(db, 1), "windows": nwin},
                               ["sensitivity peak from low phase margin (P/D too high for the delay, too much filter lag)",
                                "mechanical resonance not filtered", "TPA insufficient (high throttle)"],
                               ["compare with assess 'Ms' and its frequency; reduce P/D or filter lag; TPA; dyn notch"]))
    return out


def propwash(fl: Flight) -> list[Finding]:
    """Wobble at low throttle after throttle chops (dives, flips): the propwash signature.

    Uses the 15-80 Hz band of the roll/pitch tracking error, so stick-driven tracking lag (which
    lives below ~10 Hz) does not count. Reported as absolute RMS and relative to calm cruise.
    """
    from scipy.signal import butter, sosfiltfilt

    ok = _acro_mask(fl)
    thr = fl.throttle
    dthr = np.gradient(thr) * fl.fs
    sos = butter(2, [15, min(80, 0.45 * fl.fs)], "bandpass", fs=fl.fs, output="sos")
    e = fl.setpoint[:, :2] - fl.gyro[:, :2]
    eb = np.zeros_like(e)
    for a, b in _segments(ok, int(0.5 * fl.fs)):
        eb[a:b] = sosfiltfilt(sos, e[a:b], axis=0)
    n = int(0.25 * fl.fs)
    chop, flip, calm = [], [], []
    for a, b in _segments(ok, n * 2):
        for s in range(a + n // 2, b - n - n // 2, n // 2):
            rms = float(np.sqrt(np.mean(eb[s : s + n] ** 2)))
            sp_move = np.std(fl.setpoint[s : s + n, :2])
            t = np.mean(thr[s : s + n])
            if t < 0.25 and np.min(dthr[max(a, s - n) : s + n]) < -1.5:
                if sp_move < 100:
                    chop.append(rms)
                elif sp_move > 200:
                    flip.append(rms)
            elif 0.25 < t < 0.5 and sp_move < 30:
                calm.append(rms)
    if len(calm) < 3 or len(chop) + len(flip) < 3:
        return []
    c0 = float(np.median(calm))
    ev = {"calm_rms_deg_s": round(c0, 2)}
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
                    f"15-80 Hz wobble at low throttle after chops: {'; '.join(parts)}; calm cruise {c0:.1f} deg/s",
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
        fast = ok & (np.abs(sp) > 300)
        ends = []
        for a, b in _segments(fast, int(0.08 * fl.fs)):
            e_end = b + int(0.15 * fl.fs)
            if e_end < fl.n and np.all(np.abs(sp[b : e_end]) < 60):
                sign = np.sign(np.mean(sp[a:b]))
                peak_opp = float(np.max(-sign * fl.gyro[b:e_end, axis]))
                ends.append(peak_opp)
        if len(ends) >= 3:
            med = float(np.median(ends))
            if med > 40:
                out.append(Finding(f"bounce_back_{AXES[axis]}", "warn" if med < 100 else "problem",
                                   f"{AXES[axis]}: median {med:.0f} deg/s opposite rebound after fast moves",
                                   {"events": len(ends), "median_rebound_deg_s": round(med, 1)},
                                   ["I-term windup (iterm_relax too high/cutoff)", "FF overshoot", "low damping (D)"],
                                   ["iterm_relax_cutoff", "feedforward", "D"]))
    return out


def throttle_punch(fl: Flight) -> list[Finding]:
    """Attitude error during fast throttle increases vs calm cruise: what anti-gravity is for."""
    from scipy.signal import butter, sosfiltfilt

    ok = _acro_mask(fl)
    dthr = np.gradient(fl.throttle) * fl.fs
    sos = butter(2, 10.0, "lowpass", fs=fl.fs, output="sos")  # attitude drift band (I-term job)
    e = fl.setpoint[:, :2] - fl.gyro[:, :2]
    el = np.zeros_like(e)
    for a, b in _segments(ok, int(0.5 * fl.fs)):
        el[a:b] = sosfiltfilt(sos, e[a:b], axis=0)
    n = int(0.2 * fl.fs)
    punch, calm = [], []
    for a, b in _segments(ok, 2 * n):
        for s in range(a, b - n, n // 2):
            sp = np.std(fl.setpoint[s : s + n, :2])
            if sp > 40:
                continue
            rms = float(np.sqrt(np.mean(el[s : s + n] ** 2)))
            if np.max(dthr[s : s + n]) > 3.0:
                punch.append(rms)
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
                     "windows": len(punch)},
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
    m = fl.motor[armed]
    hp = m - np.apply_along_axis(lambda x: np.convolve(x, np.ones(15) / 15, "same"), 0, m)
    rms = float(np.sqrt(np.mean(hp[20:-20] ** 2)) * 100)
    # absolute levels depend on craft and log rate: compare between logs of the same quad
    sev = "warn" if rms > 3.0 else "info"
    return [Finding("motor_hf_noise", sev, f"high-frequency motor-command noise {rms:.2f} % RMS",
                    {"hf_rms_pct": round(rms, 2)}, ["D-term noise", "weak filtering", "resonance"],
                    ["compare with the pilot's motor temperatures; noise model budget"])]


def diagnose(fl: Flight) -> list[dict]:
    findings: list[Finding] = []
    for fn in (log_quality, saturation, desync, oscillation, propwash, bounce_back, throttle_punch, pidsum_clipping,
               heat_risk):
        try:
            findings += fn(fl)
        except Exception as e:  # a failing heuristic must not hide the others
            findings.append(Finding(f"{fn.__name__}_error", "info", f"{fn.__name__} check failed: {e}"))
    order = {"problem": 0, "warn": 1, "info": 2}
    return [asdict(f) for f in sorted(findings, key=lambda f: order.get(f.severity, 3))]


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
