---
name: diagnosis
description: Catalogue for diagnosing Betaflight flight problems - pilot symptoms and bftune diagnose findings mapped to physical causes, the evidence that separates them, and the knobs that fix them (propwash, oscillation, bounce-back, hot motors, wobble, mushy or twitchy feel, desync, imbalance, saturation).
user-invocable: false
---

# Symptom → cause → evidence → knob

Always name the mechanism before you touch a knob, and look for evidence that separates the
candidate causes. `bftune diagnose -v` gives finding ids with evidence; look at the moment itself
with `bftune plot LOG --window T0:T1` (findings give `worst_at_s`). The model (`assess`, `sweep`)
tells you what a knob would do. The pilot tells you what matters.

Frequencies below are *this quad's*: the sensitivity peak is where `assess` reports `ms_hz`, and
diagnose scales its bands with the control bandwidth it measures in the log (`brief` →
`flight_profile.control_bandwidth_hz`; a 10" lives at about half a 5"'s). Thresholds here are the model's targets for this quad (`bftune targets`), not fixed numbers.

## Pilot symptoms

| symptom | likely causes | evidence that separates them | knobs |
|---|---|---|---|
| **propwash** (wobble in dives, after flips, at low throttle) | (a) a sensitivity peak from too little phase margin; (b) low authority at low rpm (motor lag, low idle); (c) too little damping | (a) hover/idle `ms` above `ms_max`, a `resonance_*` finding at the same frequency as `ms_hz`, an `errspec` bump there; (b) idle crossover `idle.fc` low compared with the flown tunes the pilot liked, `natural_idle_rpm` low, dyn idle off; (c) idle PM low, D/P below the flown tunes the pilot liked | (a) less filter lag (dterm/gyro LPF), P/D balance, less D if D sits past the delay region; (b) `dyn_idle_min_rpm`, `thrust_linear`; (c) D or `d_max` if the noise budget allows |
| **oscillation on punch-outs** | full-throttle PM low (plant gain grows with rpm) | `full` PM/Ms, worst case `full/...` | `tpa_rate`, `tpa_breakpoint`, `tpa_mode PD` |
| **fast oscillation or buzz at hover** | Ms too high, or a mechanical resonance | `resonance_*` at hover band, `noise` persistent non-RPM peaks | lower P/D, filter lag, `dyn_notch` for structural peaks |
| **bounce-back** after flips and rolls | I-term windup, too much FF, low damping | `bounce_back_*` finding, `ff` overshoot table, idle/hover PM | `iterm_relax_cutoff` (lower), FF lower, D |
| **hot motors** | D-term/gyro noise reaching the motors; a resonance; hardware | `motor_hf_noise` vs this quad's other logs, `noise_vs_safe`, D-term RMS per band, the spectrogram, `motors` | find the cause first (`bftune:filters-noise`); then stronger D-term LPF, lower D, gyro LPF, RPM filter Q/harmonics, or back off to the last cool tune |
| **mushy, laggy, floaty** | low crossover, low FF, heavy RC smoothing or filtering | hover `fc`, the as-flown 50 % time (model and measured), `rc_smoothing_auto_factor` | P (within margins), FF, less filtering, `rc_smoothing_auto_factor` lower (`sweep --steps` shows which moves lag on this quad) |
| **twitchy, nervous** | too much FF or FF jitter, P too high, rates | as-flown peak (measured and model), `ff` table, hover Ms | FF lower, `feedforward_jitter_factor`, `feedforward_smooth_factor` |
| **drifts in turns, poor attitude hold** | I too low, anti-gravity | tracking error in the lowest `errspec` bands vs other logs of this quad | I (from the flown I/P upward), `anti_gravity_gain` |
| **yaw spin-up, yaw washout** | yaw authority saturated, yaw I | motor saturation during yaw, `pidsum_limit_yaw` | yaw P/I, `iterm_relax` on yaw, check props |

## `bftune diagnose` findings

| id | what it means | what to do |
|---|---|---|
| `resonance_<axis>_<band>` | a peak in the tracking-error spectrum, averaged over a throttle band | Compare with `assess` hover/mid `ms_hz`. If they coincide, it is the loop's sensitivity peak (tunable). If not, look for a mechanical resonance (`noise` persistent peaks). **Low throttle:** the finding says whether the peak sits at the idle motor frequency and whether a motor line survives into the filtered gyro. A motor line below `rpm_filter_min_hz` means lowering `rpm_filter_min_hz` (seen on a 3.5" with dyn idle raising idle rpm). A broad hump without a line is body motion (seen on a 5"): leave the filter. |
| `propwash` | error in this quad's propwash band (shown in the finding) after throttle chops, split into gentle chops and flips/rolls | Compare logs only stratum by stratum. It depends strongly on how hard the pilot flew. Look at `worst_at_s` with `plot`: an oscillation, or motors at the idle floor trailing the setpoint (authority)? |
| `bounce_back_<axis>` | opposite rebound after this pilot's fast moves, also as % of the move | see the table above; compare the % between logs |
| `motor_saturation` | a motor at its maximum command (respecting `motor_output_limit`); the fraction below part throttle is what matters | at full-throttle punch-outs it is normal; at part throttle authority is missing (weight, props, P/D/FF) |
| `motor_imbalance` | uneven motor outputs at hover (always reported, with the direction) | A steady offset: usually CG or trim; tell the pilot the direction and continue. One motor off in `motors` (rpm per command), a change between logs, or a desync: bent prop, bearing, weak motor; fix the hardware first. |
| `motor_stall` | a motor's rpm collapsed although it was commanded up: a real stall or desync | Check that motor's prop, bell and bearing (often right after a crash). If it repeats on good hardware, look at ESC demag/timing or a higher idle floor. Don't raise gains until it is resolved. `bftune motors` gives the full review. |
| `rpm_dips_explained` | rpm dips where the mixer commanded the motor down (hard flips/rolls), or during a crash | normal; no action |
| `throttle_punch_dip` | low-frequency attitude error while throttle rises fast, sticks centred | `anti_gravity_gain` (and `anti_gravity_p_gain`), I. Fix the CG first if `motor_imbalance` shows up too. |
| `pidsum_clipping_<axis>` | PID sum at `pidsum_limit(_yaw)` | yaw spin-up or weak yaw: raise the limit only if motors are not saturated. Otherwise lower P/FF. |
| `motor_hf_noise` | motor-command content above this quad's control band | no severity: compare with other logs of the same quad and the temperatures the pilot reported (the calibration table in `quad.md`). |
| `no_chirp`, `chirp_axes_missing` | the model is low-confidence or incomplete | small relative changes; plan a chirp flight (`bftune:flight-plan`) |
| `low_log_rate`, `low_resolution`, `corrupt_frames` | data limits | handled, but they widen uncertainty; fix for the next flight if cheap |
| `no_rpm_telemetry` | no eRPM | no RPM filter or motor model; see `bftune:craft-classes` |

## Rules of thumb
- One cause per symptom is rare. Rank causes by evidence and fix the dominant one first.
- A tune change never fixes hardware problems: desyncs, imbalance, loose parts, broken props.
- If a finding and the pilot disagree (e.g. strong propwash finding, but the pilot is happy), the
  pilot's priorities win. Mention it and move on.
