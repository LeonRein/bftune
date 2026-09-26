"""Coverage of every Betaflight 2026.6 (multirotor) setting that shapes flight behaviour.

For each feature group: the settings, what bftune can test for them ("model" = margins/noise in
assess/sweep, "step" = the stick-response simulation, "none" = judgement from flight data and the
pilot), and the evidence to use. `bftune coverage` prints this against the logged tune and a
candidate, so the agent can show it looked at everything. Classification verified by perturbing
each setting on a real analysis (see docs/DEVELOPMENT.md). Wing-only settings (s_*, spa_*,
tpa_speed_*, tpa_curve_*) are not in multirotor builds and are left out.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model.params import Tune


@dataclass(frozen=True)
class Feature:
    group: str
    keys: tuple[str, ...]
    tested: str  # model | step | model+step | idle case | none
    how: str


A = ("roll", "pitch", "yaw")
FEATURES: tuple[Feature, ...] = (
    Feature("RPM filter", ("rpm_filter_harmonics", "rpm_filter_weights", "rpm_filter_q", "rpm_filter_min_hz"),
            "model+step", "sweep rpm_filter_q 500:900:100 and rpm_filter_weights (e.g. 100,50,100); harmonics visible in "
            "gyroUnfilt keep weight; min_hz below idle motor Hz"),
    Feature("RPM filter (fine)", ("rpm_filter_fade_range_hz", "rpm_filter_lpf_hz"), "none",
            "defaults unless telemetry is noisy (rpm_filter_lpf_hz) or the idle motor Hz sits in the fade range"),
    Feature("Dynamic notch", ("dyn_notch_count", "dyn_notch_q", "dyn_notch_min_hz", "dyn_notch_max_hz"), "model",
            "`noise`: persistent non-RPM peaks need it; else try count 0 (+ yaw_lowpass) or fewer notches / higher Q; "
            "check the dn@min case"),
    Feature("Static gyro notches", ("gyro_notch1_hz", "gyro_notch1_cutoff", "gyro_notch2_hz", "gyro_notch2_cutoff"),
            "model", "only for a fixed-frequency resonance the dyn notch can't hold; usually off (hz 0)"),
    Feature("Gyro lowpass", ("gyro_lpf1_type", "gyro_lpf1_static_hz", "gyro_lpf1_dyn_min_hz", "gyro_lpf1_dyn_max_hz",
                             "gyro_lpf1_dyn_expo", "gyro_lpf2_type", "gyro_lpf2_static_hz"), "model+step",
            "least filtering within the noise budget; *_static_hz is ignored while dyn_min_hz > 0; watch the 1-3 kHz note"),
    Feature("D-term filters", ("dterm_lpf1_type", "dterm_lpf1_static_hz", "dterm_lpf1_dyn_min_hz", "dterm_lpf1_dyn_max_hz",
                               "dterm_lpf1_dyn_expo", "dterm_lpf2_type", "dterm_lpf2_static_hz", "dterm_notch_hz",
                               "dterm_notch_cutoff", "yaw_lowpass_hz"), "model+step",
            "compare layouts at their best gains (`suggest` on several files); yaw_lowpass_hz filters yaw P"),
    Feature("PID gains", tuple(f"{t}_{a}" for a in A for t in ("p", "i", "d")), "model+step",
            "`suggest`, then `sweep`; I ~1.5-1.7x P (hold is judged by the pilot)"),
    Feature("D-max", tuple(f"d_max_{a}" for a in A[:2]) + ("d_max_gain", "d_max_advance"), "model+step",
            "d_max_<axis> sweep (cases …/dmax); gain = gyro-driven, advance = stick-driven boost; both 0 = D-max off"),
    Feature("Feedforward", tuple(f"f_{a}" for a in A) + ("feedforward_boost", "feedforward_smooth_factor",
                                                         "feedforward_averaging", "feedforward_yaw_hold_gain",
                                                         "feedforward_yaw_hold_time"), "step",
            "`ff` per axis; sweep boost/smoothing/averaging with --steps; overshoot limits by style"),
    Feature("Feedforward (RC link)", ("feedforward_jitter_factor", "feedforward_max_rate_limit", "feedforward_transition"),
            "none", "jitter_factor vs RC link quality (higher for noisy/low-rate links); max_rate_limit vs overshoot at "
            "full stick; transition for smooth centre-stick feel"),
    Feature("RC smoothing", ("rc_smoothing", "rc_smoothing_auto_factor", "rc_smoothing_setpoint_cutoff"), "step",
            "sweep rc_smoothing_auto_factor --steps (lag); lower = less lag but more RC jitter (unmodelled)"),
    Feature("TPA", ("tpa_mode", "tpa_rate", "tpa_breakpoint", "tpa_low_rate", "tpa_low_breakpoint", "tpa_low_always"),
            "model", "full-throttle cases → tpa_rate/breakpoint/mode; tpa_low_* only acts with tpa_low_always = ON"),
    Feature("Throttle authority", ("thrust_linear", "motor_output_limit"), "model+step",
            "thrust_linear for low-throttle authority (check noise); motor_output_limit only for over-powered setups"),
    Feature("Throttle response", ("throttle_boost", "throttle_boost_cutoff", "vbat_sag_compensation"), "none",
            "pilot feel: punch response (throttle_boost), consistent feel through the pack (vbat_sag_compensation)"),
    Feature("Idle", ("dyn_idle_min_rpm",), "idle case",
            "sweep dyn_idle_min_rpm above natural idle (flight.natural_idle_rpm_p20); needs RPM telemetry"),
    Feature("Idle (fine)", ("dyn_idle_p_gain", "dyn_idle_i_gain", "dyn_idle_d_gain", "dyn_idle_max_increase", "motor_idle"),
            "none", "defaults unless the log shows idle rpm hunting below the floor; motor_idle when dyn idle is off"),
    Feature("I-term relax / integrated yaw", ("iterm_relax", "use_integrated_yaw"), "model+step",
            "sweep iterm_relax OFF,RP,RPY --steps and use_integrated_yaw OFF,ON (yaw plant with integrated yaw)"),
    Feature("I-term (fine)", ("iterm_relax_type", "iterm_relax_cutoff", "iterm_windup", "iterm_rotation",
                              "integrated_yaw_relax"), "none",
            "bounce_back finding → iterm_relax_cutoff lower; iterm_rotation for long turns/funnels; windup default"),
    Feature("Anti-gravity", ("anti_gravity_gain", "anti_gravity_cutoff_hz", "anti_gravity_p_gain"), "none",
            "attitude dips on fast throttle changes (errspec low bands, pilot report) → gain up; default otherwise"),
    Feature("Limits", ("pidsum_limit", "pidsum_limit_yaw"), "none",
            "raise only if `diagnose`/log shows pidsum clipping (yaw spin-up, weak yaw)"),
    Feature("Rates (pilot preference)", ("rates_type", "roll_rc_rate", "roll_srate", "roll_expo", "pitch_rc_rate",
                                         "pitch_srate", "pitch_expo", "yaw_rc_rate", "yaw_srate", "yaw_expo"), "none",
            "pilot's choice: change only on request (max rate feeds the FF snap test)"),
)


def coverage(logged: Tune, cand: Tune | None = None) -> list[dict]:
    rows = []
    for ft in FEATURES:
        items = []
        for k in ft.keys:
            old = logged.values.get(k)
            new = cand.values.get(k) if cand is not None else None
            items.append({"key": k, "logged": old, "candidate": new,
                          "changed": cand is not None and new is not None and str(new).upper() != str(old).upper()})
        rows.append({"group": ft.group, "tested": ft.tested, "how": ft.how, "settings": items,
                     "changed": any(i["changed"] for i in items)})
    return rows


def format_coverage(rows: list[dict], with_cand: bool) -> str:
    out = ["feature coverage (tested: model = assess/sweep margins+noise, step = stick-response sim, none = judgement"
           " from flight data and pilot)"]
    for r in rows:
        mark = "CHANGED" if r["changed"] else ("unchanged" if with_cand else "")
        out.append(f"\n[{r['tested']:10s}] {r['group']}  {mark}")
        vals = []
        for i in r["settings"]:
            v = "?" if i["logged"] is None else i["logged"]
            if with_cand and i["changed"]:
                v = f"{v} -> {i['candidate']}"
            vals.append(f"{i['key']}={v}")
        out.append("    " + ", ".join(vals))
        out.append(f"    how: {r['how']}")
    out.append("\n'?' = not in the dump/log header (unknown current value). Record a decision for every group in the"
               " worklog: changed (with evidence) or unchanged (with the reason).")
    return "\n".join(out)
