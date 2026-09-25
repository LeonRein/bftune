---
name: bf-filters
description: Gyro/D-term filter and noise decisions with bftune - alias-aware noise model, motor-noise budget, RPM filter, dynamic notch, lowpass layouts, safe-log calibration. Use when choosing filters or judging motor heat risk.
---

# Filters and noise

**Why a model:** 1-2 kHz logs cannot show noise above Nyquist directly. The noise model fits
one smooth 8 kHz gyro-noise spectrum that explains gyroUnfilt (upper bound), gyroADC and the D term
*simultaneously* through their known filter chains and decimation. Motor lines are excluded because
the RPM filter tracks them. Check `noise_bands[].fit_error` (< ~1 is fine) and whether predicted D
RMS matches `measured_dterm_rms`.

**Budget:** predicted high-frequency (>70 Hz) noise at the motors (P+D path × thrust_linear slope)
per throttle band and axis must stay ≤ the level of a **proven-safe** tune:
- default: the logged tune (motors must have been cool; ask);
- `--safe-log OTHER.BFL`: another tune of the same quad that flew cool raises the budget to its
  level. This is the single most useful extra input.
- `--noise-budget 0.8` for cinematic or hot motors, >1 only with evidence.

**Tools:** `bftune noise -o OUT [cand.txt]` lists per throttle band the measured D and gyro noise,
the fit error, non-RPM peaks (with **persistent** = same frequency in several bands → a structural
resonance, a job for the dyn notch) and the candidate's predicted motor noise vs the proven-safe
level. `bftune sweep ... gyro_lpf2_static_hz ...` / `dterm_lpf2_static_hz` / `rpm_filter_q` show the
phase-vs-noise tradeoff directly.

**Layout guidance:**
- RPM filter first: 3 harmonics; Q 500-800 (higher Q = less lag, needs accurate telemetry).
- Gyro: with a clean build, LPF1 off and one PT1 LPF2 at ~1.5-2.5× the hover motor frequency.
  Keep LPF1 (dynamic) if the noise model shows broadband energy near crossover.
- Dynamic notch: 1 notch for frame resonances; min_hz above the crossover region. Removing it
  often costs more noise than it saves in lag.
- D-term: PT1 dynamic lpf1 + PT1 lpf2, or lpf1 off + PT2/PT3 lpf2 (steeper, less low-f lag).
- Every filter's phase lag at crossover costs margin; the optimizer trades that against
  noise-limited D.

**Red flags:** noise peaks that do not track RPM (frame resonance or loose parts), large
gyroUnfilt-vs-gyroADC differences that the model can't explain, and dual-gyro disagreement
(debug MULTI_GYRO_DIFF).
