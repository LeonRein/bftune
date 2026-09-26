---
name: filters-noise
description: Gyro and D-term filter decisions for Betaflight with bftune - the alias-aware noise model, motor-noise budget and heat risk, RPM filter, dynamic notch, lowpass layouts, and how filters trade phase lag against allowable D.
user-invocable: false
---

# Filters and noise

**Who knows what.** The filter chain (types, cutoffs, RPM and dynamic notches, aliasing) is
firmware fact and lives in the model. How much noise *this* quad tolerates is evidence: its flown
tunes, their noise levels and how warm the motors got. Numbers marked *(convention)* or
*(seen on …)* are starting points, not rules.

## The noise model and the budget
A 1-2 kHz blackbox log can't show noise above its Nyquist frequency, and the log is decimated
without an anti-alias filter. bftune fits one gyro-noise spectrum at the gyro/loop rate that
explains gyroUnfilt (upper bound), gyroADC and the D term at the same time through their known
filter chains and aliasing. That spectrum predicts the noise *at the motors* (P+D path ×
thrust_linear slope) for any candidate. "Noise" is everything above this quad's control band
(3.7 × the control bandwidth measured in the log: 70 Hz on a 5", lower on a 10").

**Budget.** Per throttle band and axis, a candidate's predicted motor noise is compared with the
level of the **reference**: the highest level among the logged tune and every
`--safe-log`/`--safe-cli` tune of the same quad, in that band and axis. The allowed multiple, `noise_budget`, is a design target **you derive for this quad**:

1. **Build the quad's own calibration** in `quad.md` → "Constraints": for every tune that flew, its
   noise relative to the reference (`assess --with-safe` → `noise × safe`, and `diagnose` →
   `motor_hf_noise` per log) and how warm the motors were (the pilot's answer, same scale every
   time). After two or three tunes this table *is* the budget: the highest level that stayed cool,
   minus a step when the pilot wants cool motors, or a step up when every level so far ran cold.
2. **Only one data point so far?** Start from the heuristic below *(convention, from the physics
   below and two quads)* and replace it with the table as soon as there is more:

   | motors after a hard pack on the reference tune | noise_budget |
   |---|---|
   | cold (ambient, can't tell they ran) | 1.25: real headroom, go up one step |
   | cool | 1.1 |
   | slightly warm | 1.0: hold the level |
   | warm (can't hold a finger on it for 5 s) | 0.85: cut noise first |
   | hot | 0.7, and find the cause before tuning further |

   Adjust for what the pilot tells you about conditions: a cold day, short packs or a light
   cinematic style make "cold" weaker evidence than a hot day and hard packs.
3. Set it: `bftune targets --set 'noise_budget=… # evidence'`. The flown tune's own level always
   passes when the budget is ≥ 0.9 (it flew). Steps beyond 1.25× the reference (up to the floor,
   2×) need a supervised **noise-headroom flight** (below). Every output states the limit that
   applied.

**Why a budget at all, if the motors are cold?** Motor heat is mostly I²R from the average current
(thrust, weight, props, flying style). High-frequency noise in the motor command adds ripple current
whose losses grow with the *square* of the noise, so on a cold motor doubling the noise raises the
noise share of the heat about 4×, but from a small base: cold motors after hard flying mean real
headroom. Noise also costs things that don't show as temperature:
- lost control authority: D-term noise uses up mixer headroom, so more saturation in hard moves;
- a noisier gyro path that lets resonances through: a mid-throttle oscillation can heat motors
  within seconds;
- ESC stress, desync risk at the extremes, audible whine, and jello in the video (cinematic).

**Hot motors deserve a cause, not just a lower budget.** On the freestyle quads seen so far a sane
tune landed with cool motors; builds differ (brushed whoops, heavy long-range quads, hot climates),
so compare with this quad's own history. When motors run hotter than its history says they should,
look for:
- too much D, or filtering that is too light (noise);
- a resonance amplified by D (`noise` persistent peaks, `diagnose` resonance, the spectrogram);
- an oscillation (`diagnose`, `plot` at the moment);
- hardware: a bent prop, a dragging bearing, the wrong kV or props for the cell count, ESC
  timing/demag, or a motor stall (`bftune motors`).

`motor_hf_noise` (motor-command RMS above the control band, % of range) is comparable between logs
of *this* quad at the same log rate; across quads it means little.

`bftune noise -o A [cand.txt]` shows, per throttle band: the measured D-term and gyro RMS, the fit
error, non-RPM peaks (apparent frequency, **persistent** across bands or not), the reference level
and the candidate. The spectrograms (`analysis.html`, `bftune logs`) show the same things as a
picture: look at them.

## Noise-headroom flight (when the budget is what limits the quad)
The budget only knows tunes that have flown. If every flown tune ran **cold**, the real limit is
unknown and may be far higher (seen on a 3.5": 4-5× the D-term noise still flew cool). When the
budget is the binding limit and the pilot wants more:
1. Build a candidate with lighter filtering (dyn notch off or fewer notches, a D-term LPF opened up
   one step, RPM filter at a lower Q) and gains that pass on the model, at predicted noise one step
   above the reference (e.g. 1.3-1.6×, `--noise-budget` for that assess/emit only; the floor is
   2×). Say plainly that this is an experiment, and emit it with `--experiment "noise headroom: …"` so
   the CLI and the report say so too.
2. The flight protocol: hover 30 s, land, touch the motors. Then one minute of normal flying, land,
   touch them again. Stop if any motor is too hot to hold a finger on.
3. If they stay cool or only slightly warm, add that log as `--safe-log` and a row to the
   calibration table. The next iteration may use the headroom.
Never do this without the pilot's agreement or on a quad with a known hardware issue.

## Filters and D are coupled
A stronger D-term filter allows more D inside the budget. So compare layouts *at their
noise-limited best gains*: one candidate file per layout, then
`bftune suggest -o A v1.txt v2.txt v3.txt` (verdict and objective per file). A layout that looks
worse at equal D can win at its own best D.

## Layout (test on this quad; sweep ranges bracket the flown values)
1. **RPM filter first.**
   - `rpm_filter_harmonics` and `rpm_filter_weights`: the spectrogram of the raw gyro shows which
     motor harmonics exist and how strong they are on this quad (blade count, frame); a weak one
     can be down-weighted for less lag. Sweep the weights against that picture.
   - `rpm_filter_q`: higher Q = less lag, but needs clean telemetry (`motors` → telemetry jitter).
   - `rpm_filter_min_hz` below the idle motor frequency measured in the log (`brief`,
     `assess` lint), or the fundamental at idle is not notched.
2. **Dynamic notch** for **persistent** non-RPM peaks (a frame resonance: a vertical stripe in the
   spectrogram). Keep `dyn_notch_min_hz` above the crossover region and check the `dn@min` case.
   Without persistent peaks it costs phase for little: `sweep dyn_notch_count 0,1` shows the phase
   and noise trade on this quad. Peaks that move with throttle but sit off the RPM lines are usually
   aliases of higher harmonics or sidebands, not structure.
3. **Gyro LPF:** the least filtering whose noise stays in budget in every band. It scales with the
   motor frequency (a clean 5" often runs LPF1 off plus one PT1 LPF2 at 1.5-2.5 × the hover motor
   frequency *(convention)*). Don't go weaker at 1-3 kHz than a tune that has flown: `assess` notes
   it, because the noise model extrapolates there. The one exception is the supervised
   noise-headroom flight below, whose purpose is to fly exactly that.
4. **D-term LPF:** trades D phase against noise. PT2/PT3 at a lower cutoff can beat two PT1s. Sweep
   the cutoff around the flown value (e.g. ±40 %) to see noise and hover PM/Ms side by side.
5. Disable a lowpass by setting its cutoff to 0. In candidate files, `set gyro_lpf1_type = OFF`
   also works; it zeroes that filter's cutoffs. "BIQUAD" in 2026.6 is an SVF with Butterworth Q.

## Red flags
- Non-RPM peaks at the same frequency in every band: a frame resonance or loose part. Use the
  dynamic notch and check the hardware.
- gyroUnfilt vs gyroADC differences the model can't explain (large `fit_error`).
- Dual-gyro disagreement (debug `MULTI_GYRO_DIFF`).
- Motor noise far above that of the other logs of the same quad: a damaged prop or bent shaft.
