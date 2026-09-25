---
name: bf-sysid
description: Interpret bftune's plant identification - instrument-variable chirp FRFs, plant model structures (motor lag, delay, yaw zero), filter-chain checks, closed-loop and replay validation, motor time-constant model. Use to decide whether an identified model can be trusted.
---

# Plant identification — how to read it

**Method.** The chirp is added to the rate setpoint (closed loop). With the logged excitation z
(or a reconstructed one) as the instrument, `G = S_z,gyro / S_z,pidSum` is unbiased by feedback
and noise. Cross-spectra are band-summed per 1/8 octave over all runs of an axis.

**Models** (pidSum → gyro [deg/s]):
- roll/pitch `integ_lag`: K·e^(−sT)/(s(τs+1)). K ≈ angular acceleration per pidSum unit, τ = motor
  lag at hover, T = pure delay (ESC, DShot, gyro LPF, SPI, one loop).
- yaw `integ_lag_zero`: K(t_z s+1)e^(−sT)/(s(τs+1)). The zero comes from rotor reaction torque, so
  yaw is proportional (not integrating) above ~1–2 Hz. `integ_lag_zero2` adds a second small lag
  τ2 (ESC/prop); with a wide coherent band, AIC may prefer it. τ2 and T then share the delay.
- `integ_lag2` / `pole_lag`: extra lag / aerodynamic damping; chosen only when AIC clearly prefers them.
- If the coherent band is short, motor lag and delay trade off. Judge them by the total phase in the
  band, not individually.
- The fit starts at 3 Hz: below it, large-angle attitude/translation coupling (ANGLE-mode chirps)
  bends the response. That is irrelevant for the rate loop.

**Gates (all must pass)**
1. `chain_check.passed` — the modelled gyro filter chain and D-term path equal what the log shows
   (gyroADC/gyroUnfilt and D/gyroADC ratios, < 1 dB / 8°). This validates the firmware port and
   the configuration, and estimates where the dynamic notch sits.
2. `validation.closed_loop` — model vs measured setpoint→gyro response < 1 dB / 8° rms.
3. `validation.replay` — logged setpoint + F-term drive the model through freestyle windows at
   their own throttle. Roll/pitch fit > 60 % is good (gyro noise limits the number).
4. Parameters within the craft priors (`docs/craft-priors.md`). τ from the axis fit and the
   `motor_model` (ARX on command → eRPM) should agree within ~30 %.

**Scheduling.** `motor_model` gives τ(rpm) and the authority ratio. Plants are rescaled to idle,
mid and full throttle, battery ±8 %, and thrust_linear changes (`thrust_linear_slope`). If several
logs of the same quad exist, identify each and compare: gains should agree within ~10 % after the
thrust_linear correction.
