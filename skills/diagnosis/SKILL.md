---
name: diagnosis
description: Catalogue for diagnosing Betaflight flight problems - pilot symptoms and bftune diagnose findings mapped to physical causes, the evidence that separates them, and the knobs that fix them (propwash, oscillation, bounce-back, hot motors, wobble, mushy or twitchy feel, desync, imbalance, saturation).
user-invocable: false
---

# Symptom → cause → evidence → knob

Always name the mechanism before you touch a knob, and look for evidence that separates the
candidate causes. `bftune diagnose -v` gives finding ids with evidence. The model (`assess`,
`sweep`) tells you what a knob would do. The pilot tells you what matters.

## Pilot symptoms

| symptom | likely causes | evidence that separates them | knobs |
|---|---|---|---|
| **propwash** (wobble in dives, after flips, at low throttle) | (a) sensitivity peak at 30-60 Hz from too little phase margin; (b) low authority at low rpm (motor lag, low idle); (c) too little D | (a) hover/idle `ms` > 2 with `ms_hz` 30-60, `resonance_*` finding at the same frequency, `errspec` bump; (b) idle crossover `idle.fc` low and `natural_idle_rpm` low, dyn idle off; (c) idle PM low, D/P < 0.6 | (a) less filter lag (dterm/gyro LPF), P/D balance, less D if D sits past the delay region; (b) `dyn_idle_min_rpm`, `thrust_linear`; (c) D or `d_max` if the noise budget allows |
| **oscillation on punch-outs** | full-throttle PM low (plant gain grows with rpm) | `full` PM/Ms, worst case `full/...` | `tpa_rate`, `tpa_breakpoint`, `tpa_mode PD` |
| **fast oscillation or buzz at hover** | Ms too high, or a mechanical resonance | `resonance_*` at hover band, `noise` persistent non-RPM peaks | lower P/D, filter lag, `dyn_notch` for structural peaks |
| **bounce-back** after flips and rolls | I-term windup, too much FF, low damping | `bounce_back_*` finding, `ff` overshoot table, idle/hover PM | `iterm_relax_cutoff` (lower), FF lower, D |
| **hot motors** | D-term/gyro noise reaching the motors | `motor_hf_noise` %, `noise_vs_safe` of the current tune, D-term RMS per band | stronger D-term LPF, lower D, gyro LPF, RPM filter Q/harmonics; back off to the last cool tune |
| **mushy, laggy, floaty** | low crossover, low FF, heavy RC smoothing or filtering | hover `fc`, `step` stick lag, `rc_smoothing_auto_factor` | P (within margins), FF, less filtering, `rc_smoothing_auto_factor` lower |
| **twitchy, nervous** | too much FF or FF jitter, P too high, rates | `ff` overshoot, hover Ms | FF lower, `feedforward_jitter_factor`, `feedforward_smooth_factor` |
| **drifts in turns, poor attitude hold** | I too low, anti-gravity | tracking error at 1-5 Hz (`errspec` low bands) | I (keep I ≈ 1.5-1.7 × P), `anti_gravity_gain` |
| **yaw spin-up, yaw washout** | yaw authority saturated, yaw I | motor saturation during yaw, `pidsum_limit_yaw` | yaw P/I, `iterm_relax` on yaw, check props |

## `bftune diagnose` findings

| id | what it means | what to do |
|---|---|---|
| `resonance_<axis>_<band>` | a peak in the tracking-error spectrum, averaged over a throttle band | Compare with `assess` hover/mid `ms_hz`. If they coincide, it is the loop's sensitivity peak (tunable). If not, look for a mechanical resonance (`noise` persistent peaks). |
| `propwash` | 15-80 Hz error after throttle chops, split into gentle chops and flips/rolls | Compare logs only stratum by stratum. It depends strongly on how hard the pilot flew. |
| `bounce_back_<axis>` | opposite rebound after fast moves | see the table above |
| `motor_saturation` / `motor_imbalance` | motors at their limit / uneven motor outputs at hover | 10-30 %: usually CG or trim. Tell the pilot the direction the finding reports and continue tuning. Above 30 %, or with a desync: bent prop, weak motor; fix the hardware first. |
| `possible_desync` | one motor's rpm collapsed while the others ran | Can be a telemetry glitch (one event) or a real desync. Ask about the ESC/motor, and don't raise gains until it is resolved. |
| `throttle_punch_dip` | low-frequency attitude error while throttle rises fast, sticks centred | `anti_gravity_gain` (and `anti_gravity_p_gain`), I. Fix the CG first if `motor_imbalance` shows up too. |
| `pidsum_clipping_<axis>` | PID sum at `pidsum_limit(_yaw)` | yaw spin-up or weak yaw: raise the limit only if motors are not saturated. Otherwise lower P/FF. |
| `motor_hf_noise` | high-frequency content in the motor commands | heat risk indicator. Compare with other logs of the same quad. |
| `no_chirp`, `chirp_axes_missing` | the model is low-confidence or incomplete | small relative changes; plan a chirp flight (`bftune:flight-plan`) |
| `low_log_rate`, `low_resolution`, `corrupt_frames` | data limits | handled, but they widen uncertainty; fix for the next flight if cheap |
| `no_rpm_telemetry` | no eRPM | no RPM filter or motor model; see `bftune:craft-classes` |

## Rules of thumb
- One cause per symptom is rare. Rank causes by evidence and fix the dominant one first.
- A tune change never fixes hardware problems: desyncs, imbalance, loose parts, broken props.
- If a finding and the pilot disagree (e.g. strong propwash finding, but the pilot is happy), the
  pilot's priorities win. Mention it and move on.
