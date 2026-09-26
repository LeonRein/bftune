---
name: filters-noise
description: Gyro and D-term filter decisions for Betaflight with bftune - the alias-aware noise model, motor-noise budget and heat risk, RPM filter, dynamic notch, lowpass layouts, and how filters trade phase lag against allowable D.
user-invocable: false
---

# Filters and noise

## The noise model and the budget
A 1-2 kHz blackbox log can't show noise above its Nyquist frequency, and the log is decimated
without an anti-alias filter. bftune fits one 8 kHz gyro-noise spectrum that explains gyroUnfilt
(upper bound), gyroADC and the D term at the same time through their known filter chains and
aliasing. That spectrum predicts the noise *at the motors* (P+D path × thrust_linear slope) for any
candidate.

**Budget:** per throttle band and axis, a candidate's predicted motor noise must stay ≤ 0.9 × the
level of a **proven-safe** tune:
- by default, the logged tune (ask how warm the motors were);
- `--safe-log` / `--safe-cli`: other tunes of the same quad that flew with cool motors. This raises
  the reference, and it is the most valuable extra input.
- If the motors were **hot** with the logged tune, use `--noise-budget 0.7-0.8`, then back off.
- Raise the budget above 0.9 only with evidence.

`bftune noise -o A [cand.txt]` shows, per throttle band: the measured D-term and gyro RMS, the fit
error, non-RPM peaks (apparent frequency, **persistent** across bands or not), the safe level and the
candidate.

## Filters and D are coupled
A stronger D-term filter allows more D inside the budget. So compare layouts *at their
noise-limited best gains*: one candidate file per layout, then `bftune suggest -o A v1.txt v2.txt v3.txt`
(verdict and objective per file). A layout that looks worse at equal D can win at its own best D.

## Layout guidance
1. **RPM filter first.** Harmonics that are visible in gyroUnfilt at weight 100; Q 500-800 (higher Q
   means less lag, but needs clean telemetry). `rpm_filter_min_hz` below the idle motor frequency
   (7-10": 50-60).
2. **Dynamic notch** for **persistent** non-RPM peaks (a frame resonance). Keep `dyn_notch_min_hz`
   above the crossover region, and check the `dn@min` case.
   - Without persistent peaks, removing it saves 2-3° of phase at crossover but also removes noise
     (often mostly on yaw). Remove it only if every axis stays in budget; use `yaw_lowpass_hz` for yaw.
   - Peaks that move with throttle but are not on RPM lines are usually aliases of higher motor
     harmonics or sidebands, not structure.
3. **Gyro LPF:** the least filtering whose noise stays in budget in every band. On a clean build
   this is often LPF1 off plus one PT1 LPF2 at 1.5-2.5 × the hover motor frequency. Never go
   weaker at 1-3 kHz than a tune that has flown: `assess` warns about this, because the noise model
   extrapolates there.
4. **D-term LPF:** trades D phase against noise. PT2/PT3 at a lower cutoff can beat two PT1s. Use
   `sweep dterm_lpf2_static_hz 90:180:15` to see noise and hover PM/Ms side by side.
5. Disable a lowpass by setting its cutoff to 0. In candidate files, `set gyro_lpf1_type = OFF`
   also works; it zeroes that filter's cutoffs. "BIQUAD" in 2026.6 is an SVF with Butterworth Q.

## Red flags
- Non-RPM peaks at the same frequency in every band: a frame resonance or loose part. Use the
  dynamic notch and check the hardware.
- gyroUnfilt vs gyroADC differences the model can't explain (large `fit_error`).
- Dual-gyro disagreement (debug `MULTI_GYRO_DIFF`).
- Motor noise far above that of the other logs of the same quad: a damaged prop or bent shaft.
