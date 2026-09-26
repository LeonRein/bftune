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

**Budget.** Per throttle band and axis, a candidate's predicted motor noise is compared with the
level of a **proven-safe** tune: the logged tune, plus every `--safe-log`/`--safe-cli` tune of the
same quad. The allowed multiple, `noise_budget`, is a design target that **you set from the
pilot's motor temperature** (`bftune targets --set 'noise_budget=… # why'`). There is no fixed
number:

These steps are a heuristic starting point (from the physics below and two quads), not a measured
law. Adjust them with the pilot's feedback on this quad:

| motors after a hard pack on the reference tune | noise_budget | meaning |
|---|---|---|
| cold (ambient, can't tell they ran) | 1.25 | real headroom; go up in one step |
| cool | 1.1 | some headroom |
| slightly warm | 1.0 | hold the level; no forced cut |
| warm (can't hold a finger on it for 5 s) | 0.85 | cut noise first |
| hot | 0.7, and find the cause before tuning further | |

- The flown tune's own level always passes when the budget is ≥ 0.9: it flew, so no forced cut.
- Steps beyond 1.25× (up to the safety floor, 2×) need a supervised **noise-headroom flight**
  (below).
- Every output states the limit that applied ("limit 1.25x: budget" or "the flown tune's level").

**Why a budget at all, if the motors are cold?** Motor heat is mostly I²R from the average current
(thrust, weight, props, flying style). High-frequency noise in the motor command adds ripple current
whose losses grow with the *square* of the noise. So on a cold motor, doubling the noise raises the
noise share of the heat about 4×, but from a small base. Cold motors after hard flying mean real
headroom. That's why "cold" gets a bigger step, and the headroom flight is the way to find the true
limit. Noise costs more than heat, though, and these costs don't show up as temperature:
- lost control authority: the D-term noise uses up mixer headroom, so more saturation in hard moves;
- a noisier gyro path that lets resonances through: a mid-throttle oscillation can heat motors
  within seconds;
- ESC stress, desync risk at the extremes, audible whine and jello in the video (cinematic).

**Hot motors on a healthy build mean something else is wrong.** A sane tune on a sound quad lands
with cool motors. Hot motors after a normal pack point to:
- too much D, or filtering that is too light (noise);
- a resonance amplified by D (`noise` persistent peaks, `diagnose` resonance);
- an oscillation (`diagnose`);
- hardware: a bent prop, a dragging bearing, the wrong kV or props for the cell count, ESC
  timing/demag, or a motor stall (`bftune motors`).

Find the cause before you cut D.

**Absolute context (not limits).** `diagnose` reports `motor_hf_noise` (HF motor-command RMS, % of
range). Seen so far:
- 3.5" 4S at 1 kHz logging: 1.2 % cool, 1.7 % slightly warm, 2.2 % cool to slightly warm;
- 5" 6S: 1.0-1.5 % cold.

Compare within one quad and log rate; across quads it is only a rough hint.

`bftune noise -o A [cand.txt]` shows, per throttle band: the measured D-term and gyro RMS, the fit
error, non-RPM peaks (apparent frequency, **persistent** across bands or not), the safe level and the
candidate.

## Noise-headroom flight (when the budget is what limits the quad)
The budget only knows tunes that have flown. If every flown tune ran **cold**, the real limit is
unknown and may be far higher. On the reference 3.5" a tune with 4-5× the D-term noise still flew
cool. When the budget is the binding limit, and the pilot wants more:
1. Build a candidate with lighter filtering: dyn notch off or fewer notches, a D-term LPF opened up
   one step, RPM filter at a lower Q. Keep the gains that pass on the model, at predicted noise of
   about 1.3-1.6× the reference (`--noise-budget 1.6` for that assess/emit only; the floor is 2×).
   Say plainly that this is an experiment.
2. The flight protocol: hover 30 s, land, touch the motors. Then one minute of normal flying, land,
   touch them again. Stop if any motor is too hot to hold a finger on.
3. If they stay cool or only slightly warm, add that log as `--safe-log`. It is now a proven
   reference, and the next iteration may use the headroom (more D, thrust_linear, a lighter layout).
Never do this without the pilot's agreement or on a quad with a known hardware issue.

## Filters and D are coupled
A stronger D-term filter allows more D inside the budget. So compare layouts *at their
noise-limited best gains*: one candidate file per layout, then `bftune suggest -o A v1.txt v2.txt v3.txt`
(verdict and objective per file). A layout that looks worse at equal D can win at its own best D.

## Layout guidance
1. **RPM filter first.** Always test it:
   - `sweep … rpm_filter_q 500:900:100`: higher Q means less lag, but needs clean telemetry;
   - `sweep … rpm_filter_weights "100,100,100;100,50,100;90,30,90"`: a weak harmonic can be
     down-weighted for less lag;
   - `rpm_filter_harmonics` 2 or 3;
   - `rpm_filter_min_hz` below the idle motor frequency (7-10": 50-60).
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
