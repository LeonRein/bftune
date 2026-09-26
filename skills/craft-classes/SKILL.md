---
name: craft-classes
description: How Betaflight tuning with bftune differs by craft class and setup - tinywhoops, 2.5-3.5", 5", 7-10" long range, plus missing RPM telemetry, no chirp, 4 kHz loops and frame resonances - with plausible plant parameter ranges to sanity-check an identification.
user-invocable: false
---

# Craft classes and special setups

## Plausible identified parameters
A result far outside its class points to a problem: a damaged prop, a loose FC, desyncs, or a wrong
`motor_poles`.

| class | loop | hover motor Hz | K roll [°/s² per pidSum] | motor τ at hover | delay T | typical crossover |
|---|---|---|---|---|---|---|
| 1S tinywhoop 65-75 mm | 4-8 kHz | 500-900 | 150-400 | 20-40 ms | 1.2-2.5 ms | 10-25 Hz |
| 2.5-3.5" (4S) | 8 kHz | 180-300 | 80-200 | 12-25 ms | 1.0-2.0 ms | 15-35 Hz |
| 5" (6S) | 8 kHz | 130-220 | 60-150 | 18-35 ms | 1.0-2.0 ms | 15-35 Hz |
| 7-10" | 2-4 kHz | 60-130 | 20-60 | 40-90 ms | 1.5-3 ms | 6-15 Hz |

The yaw reaction-torque zero is 1/(2π t_z): whoop 3-6 Hz, 3.5-5" 1-2 Hz, 7-10" 0.4-1 Hz.

**Reference (3.5" 4S, 1960 kV):** roll K 130, pitch K 96 (thrust_linear 0), τ 19 ms at 230 Hz,
T 1.3-1.7 ms, yaw K 11 with t_z 0.11 s. Noise is dominated by the motor fundamental, which the RPM
filter removes.

## Class playbooks
- **Tinywhoop:**
  - high motor frequency, a noisy flexible frame, often brushed or low-pole motors;
  - often a 4 kHz loop: the delay is larger, so expect lower crossovers;
  - RPM telemetry needs Bluejay: without it see below;
  - authority saturates easily, so watch `motor_saturation`;
  - thrust_linear is especially useful.
- **2.5-5":**
  - the standard case;
  - 5" freestyle frames often have 150-300 Hz resonances, so a single dynamic notch usually pays off;
  - propwash is the main complaint: idle and hover Ms plus dyn idle.
- **7-10":**
  - motor frequencies of 60-150 Hz overlap the control band;
  - motor lag and delay are large, so crossovers are low (6-15 Hz): don't chase 5" numbers;
  - keep `rpm_filter_min_hz` low (50-60);
  - structural modes can sit close to crossover: check `resonance_*` and `noise` persistent peaks;
  - use gentler TPA, and I matters more (wind).
  - The chirp end is 120 Hz and the amplitude lower (`bftune:flight-plan`).

## Special setups
- **No RPM telemetry** (no bidirectional DShot or no eRPM in the log):
  - there is no motor model and no RPM filter; bftune uses physics defaults for τ(rpm);
  - motor noise must be handled by the lowpasses and dyn notch (2-3 notches), so expect less D;
  - `dyn_idle_min_rpm` cannot work;
  - recommend enabling bidirectional DShot if the ESC supports it: it is the single biggest filter
    improvement.
- **No chirp:** the model comes from stick inputs (gain ±40 %) and the gate becomes relative.
  Make diagnosis-driven, small changes and plan a chirp flight.
- **Chirps on only some axes:** the other axes have no model. Leave them unchanged unless there
  is a diagnosed problem and a relative argument.
- **Old firmware (not 2026.6.x):** the controller and filter model don't match; bftune refuses.
  Explain, and offer a diagnosis-only review (`bftune diagnose`) plus general advice.
- **Angle-mode pilots / cinematic:** a lower crossover is fine. Prioritize smoothness (FF and
  overshoot limits 3/8 %, `rc_smoothing_auto_factor` 60) and low heat.
