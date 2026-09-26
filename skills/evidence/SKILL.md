---
name: evidence
description: How much to trust bftune's model, the flight data and the pilot's impressions when tuning a Betaflight quad - identification gates, chirp vs no-chirp confidence, coherent band, noise-model fit, and how to resolve disagreements between model predictions and what the pilot felt.
user-invocable: false
---

# Weighing the evidence

There are three sources. Each is wrong in its own way:

| source | strong at | weak at |
|---|---|---|
| **model** (`assess`, `sweep`, `ff`, `noise`) | linear margins, what a knob will do, comparing candidates on equal terms | nonlinear effects (saturation, airmode, I-term, anti-gravity), anything outside the identified band or throttle range |
| **flight data** (`diagnose`, `errspec`, `logs`, `plot`) | what actually happened, resonances, hardware problems, the stick lag as flown (measured step response) | confounded by how the pilot flew, so compare like with like |
| **pilot** | what matters, heat, overall feel | small differences, attributing causes |

**Cross-check the model with the measured step response.** `analyze` measures the step response of
the flown tune from the log (logged setpoint → gyro; Betaflight logs the setpoint after RC
smoothing, so feedforward shows up as lead) and runs the model through the *same* windows and
estimator (`analysis.html`, `brief` → `measured_step_as_flown` / `model_step_flown_tune`; the plot
overlays both). Estimator bias cancels, so:
- **timing** (50 % time) should agree within the measurement's spread (a few tenths of a ms to
  ~1 ms with `good` confidence). A clear gap means the model misses delay, motor lag or RC/FF
  behaviour: find out before trusting predicted lag changes;
- **peak and dip** are where the model is weakest (FF jitter reduction and rate limits, I-term relax
  and other nonlinearities are simplified). If the model's peak is higher than measured (seen on a
  5": 37-42 % vs 20-25 %), treat model FF overshoot as conservative on this quad and calibrate the
  FF overshoot targets on the *measured* peaks the pilot liked; if lower, the opposite;
- after a new tune flies, compare measured vs predicted again: that is the model's report card.

## Is the model trustworthy? (read `brief.json` → `identification`, `warnings`)
1. `source`:
   - `chirp`: precise. Gain within about ±10 %; the gate is absolute (design margins).
   - `freestyle`: gain within about ±40 %, delay from priors. The gate is **relative**: no worse
     than the flown tune. Make only small, diagnosis-driven changes and plan a chirp flight.
2. `chain_check_passed` on every axis (`null` = could not be checked, as in freestyle mode with
   too narrow a stick band; that is not a failure, but it is also no confirmation). It shows the firmware model matches this firmware and
   config. If it fails, stop.
3. `validation.closed_loop` < 1 dB / 8° rms (chirp only), and `validation.replay` roll/pitch fit
   > 60 % (yaw is lower).
   - A replay fit of 50-60 % is a caution: note it and keep more margin.
   - Below 50 %, or a closed-loop error above 2 dB, means stop and look for the cause: saturation,
     wrong session, or angle mode.
4. Parameters within the craft class (`bftune:craft-classes`).
5. `coherent_to_hz`: the band where the plant was measured. If the crossover or the Ms peak lies
   beyond it, `assess` says so, and those numbers rely on the model's extrapolation. Be more
   conservative there.
6. Noise model `fit_error` < ~1 per band. Where it is larger, noise predictions are uncertain;
   keep more slack.

## Calibrating the model against the pilot
Every tune the pilot flew *and* described is a calibration point. Assess it on the same model
(`bftune assess -o A flown.txt --with-current`, or `--safe-log`/`--safe-cli` at analysis time) and
line up its metrics with what they felt. What we learned on the reference 3.5":
- A hover Ms of 3.3-3.6 at 46-48 Hz felt worse for propwash than Ms 1.7, even though that tune had a
  *higher* idle crossover. Weight the hover/mid Ms and its frequency more than raw crossover.
- A `resonance_*` finding at the same frequency as the model's Ms peak confirms the model (two
  independent sources).
- Motor temperature tracks the predicted D-term noise at the motors. The model predicted the hot
  tune's D noise within about 15 %.

When the model and the pilot disagree:
1. Check that it's the same metric. The pilot may feel idle behaviour while you look at hover.
2. Check the conditions: props, battery, weather, a crash since the log.
3. Look for nonlinear effects: saturation in `diagnose`, I-term (bounce-back), airmode at zero throttle.
4. If still unexplained, trust the pilot for *direction*, the model for *safety*, and write the
   lesson into `quad.md` so later iterations use it.

## Honesty
- Say what the model can't see: noise above the log Nyquist is inferred, saturation isn't
  modelled, and I-term and anti-gravity are judgement calls.
- Predicted improvements are model numbers, not promises. Phrase them as such.
