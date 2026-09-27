---
name: craft-classes
description: How Betaflight tuning with bftune differs by craft class and setup - tinywhoops, 2.5-3.5", 5", 7-10" long range, plus missing RPM telemetry, no chirp, 4 kHz loops and frame resonances - with plausible plant parameter ranges to sanity-check an identification.
user-invocable: false
---

# Craft classes and special setups

**Who knows what.** Everything below is a starting expectation *(convention; the 3.5" and 5" rows
are measured, the others estimated)*, useful before the first log and to notice a surprising
identification. Once this quad has logs, its own numbers win.

## Rough plausibility hints for identified parameters
These ranges are rough, from general knowledge plus a few measured quads. They are **not limits**.
A result far outside them is a reason to look closer: a damaged prop, a loose FC, desyncs, a wrong
`motor_poles`, or simply an unusual build. Trust the identification gates (chain check, validation)
over this table.

| class | loop | hover motor Hz | K roll [°/s² per pidSum] | motor τ at hover | delay T | typical crossover |
|---|---|---|---|---|---|---|
| 1S tinywhoop 65-75 mm | 4-8 kHz | 500-900 | 150-400 | 20-40 ms | 1.2-2.5 ms | 10-25 Hz |
| 2.5-3.5" (4S) | 8 kHz | 180-300 | 80-200 | 12-25 ms | 1.0-2.0 ms | 15-35 Hz |
| 5" (6S) | 8 kHz | 130-220 | 60-150 | 18-35 ms | 1.0-2.0 ms | 15-35 Hz |
| 7-10" | 2-8 kHz | 60-130 | 20-60 | 40-90 ms | 1.5-3 ms | 6-15 Hz |

The yaw reaction-torque zero is 1/(2π t_z): whoop 3-6 Hz, 3.5-5" 1-2 Hz, 7-10" 0.4-1 Hz.

**Reference (3.5" 4S, 1960 kV):** roll K 130, pitch K 96 (thrust_linear 0), τ 19 ms at 230 Hz,
T 1.3-1.7 ms, yaw K 11 with t_z 0.11 s. Noise is dominated by the motor fundamental, which the RPM
filter removes.

## Class playbooks
- **Tinywhoop:**
  - high motor frequency, a noisy flexible frame, often brushed or low-pole motors;
  - often a 4 kHz loop: the delay is larger, so expect lower crossovers;
  - RPM telemetry needs ESC firmware with bidirectional DShot (Bluejay, AM32, ...); brushed
    whoops have none (see below). Check for eRPM in the log;
  - authority saturates easily, so watch `motor_saturation`;
  - thrust_linear and dyn idle can help low-throttle authority: test them with the model (the idle
    case at a whoop's short idle stretches);
  - motors on 1S whoops run warm on any tune (average current, not noise): calibrate the noise
    budget on this quad's history ("warm like always" is its normal), not on the temperature table.
- **2.5-5":**
  - the standard case;
  - frame resonances are common on freestyle frames: the spectrogram and `noise` persistent peaks
    show whether this frame has one, and the dynamic notch is worth it only then;
  - propwash is a frequent complaint: idle and hover Ms plus dyn idle (ask; don't assume).
- **7-10":**
  - motor frequencies of 60-150 Hz reach down into the region where filter lag costs phase at
    crossover, so filtering is expensive;
  - motor lag and delay are large, so crossovers are low: don't chase 5" numbers;
  - `rpm_filter_min_hz` must sit below the idle motor frequency measured in the log;
  - structural modes can sit close to crossover: check `resonance_*` and `noise` persistent peaks;
  - let the model pick TPA mode and rate (on a 10" twin D-only TPA kept D's phase lead at full
    throttle); I matters more in wind, which the model scores weakly (see loop-shaping);
  - `motor-model tau ... vs axis fit` notes are common: the axis fit lumps other lags into τ; only
    the motor model's trend with speed is used, so it is a note, not a failure.
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
- **Chirps on only some axes:** the other axes have no model, so the gate can't check them: a
  change to their own settings FAILs (`assess` lists it under `unchecked`) and shared settings
  (filters, TPA, idle) are not checked on them. Usually leave them as flown and ask for a chirp on
  them. If a diagnosed problem and a relative argument (the logs of this quad) justify a change
  anyway, that is your call: deliver it as an experiment (`emit --experiment`) and tell the pilot
  that the model could not check that axis.
- **Firmware other than 2026.6.x:** the model is a port of Betaflight 2026.6.
  - **Older** releases (4.x, 2025.x) differ in filters (biquad, not SVF), D-min vs D-max, TPA and
    chirp, so `analyze` refuses. Offer a diagnosis-only review (`diagnose`, `motors`, `errspec`,
    `tunes`) plus general advice, or ask the pilot to update to 2026.6.
  - **Newer** releases get a warning. Trust the model only if every filter-chain check passes;
    otherwise stop.
- **Angle-mode pilots / cinematic:** a lower crossover is usually fine. Ask what "smooth" means to
  them, then set the targets for it (`bftune targets`: lower `peak_max` and `ms_max`, a lower
  `noise_budget` for cool motors and no jello; the `cinematic` style preset is a starting point).
