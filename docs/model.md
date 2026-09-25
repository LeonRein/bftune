# The bftune model of Betaflight 2026.6

Every equation below is a port of the firmware. Paths are relative to `betaflight/src/main/`.
bftune checks the port against every log it analyses (the filter-chain gate).

## Loop and timing
- The PID loop runs at the gyro sample rate / `pid_process_denom` (the header `looptime` is the gyro
  sample period; e.g. 8 kHz for ICM426xx/MPU6000, 3.2 kHz for BMI270). The real rate follows the **gyro clock**:
  in the reference log it was 8130 Hz (+1.6 %). All filter coefficients use the nominal dt
  (`gyro_init.c`, `pid_init.c`), so responses are evaluated at `f_real * (nominal/actual)`.
  The RPM filter scales its dt by `schedulerGetCycleTimeMultiplier()` = nominal/measured gyro period
  (`rpm_filter.c`, `scheduler.c`). By the formula this would move the notches away from the motor lines
  under clock skew, but the logs do not show that: at Q=8 the measured attenuation of the motor
  fundamental matches notches placed exactly on the line. bftune models exact placement.
- Order per loop (`fc/core.c`): gyro read + filters → RC processing → `pidController` →
  `mixTable` → blackbox. TPA, anti-gravity and dynamic-LPF cutoffs are computed in the mixer,
  i.e. one loop late.

## Gyro chain (`sensors/gyro.c`, `gyro_filter_impl.c`)
`gyroUnfilt` (average of enabled gyros, after calibration/alignment) → **lpf2** (at the gyro rate; when
lpf2 is off and pid_process_denom > 1 the gyro samples of one PID period are averaged)
→ **RPM notches** (every motor × harmonic, shared by the 3 axes) → static notch 1, 2 → **lpf1**
(static or dynamic) → **dynamic notch** (SDFT, analyses post-lpf1) = `gyroADCf`.

## Filters (`common/filter.c`)
- PT1: `k = ω/(ω+1)`, ω = 2π·fc·dt; `y += k(x − y)`, so H = k/(1 − (1−k)z⁻¹).
- PT2/PT3: cascaded PT1 with cutoff × 1.553773974 / 1.961459177. (The PT1 is not prewarped, so
  the attenuation at the nominal cutoff is −3.2/−3.3/−3.4 dB at 8 kHz, 100 Hz.)
- "BIQUAD" = TPT state-variable filter since 2026.6 (#15056): g = tan(π fc dt), Butterworth Q,
  equivalent to the bilinear transform prewarped at fc. A static SVF is disabled above 0.95·Nyquist;
  dynamic cutoffs are clamped to 0.475/dt.
- Notch: `y = x − k·BP`, k = 1/Q; RPM notch: `y = x − w·k·BP`. Q of the RPM filter is
  `rpm_filter_q/100`, and the weight fades from 0 at `min_hz` to 1 at `min_hz + fade_range_hz`.
- Dynamic LPF (`flight/pid.c dynLpfCutoffFreq`): fc = (max−min)·(t(1−t)·expo/10 + t) + min, with
  throttle t quantized to 0.01 and updates every ≥5 ms.

## PID law (`flight/pid.c`, `pid_init.c`, `pid.h`)
- Kp = 0.032029·P, Ki = 0.244381·I (×2.5 on yaw), Kd = 0.000529·D, Kf = 0.013754·F/100.
- err = setpoint − gyroADCf; P = Kp·err (yaw: PT1 `yaw_lowpass_hz` on the P output; TPA only in
  PD mode).
- I += Ki·dt·err·relax, where relax = max(0, 1 − |HPF(setpoint)|/40) (setpoint type, `iterm_relax_cutoff`;
  roll/pitch only for RP modes; the threshold is 8 in ANGLE mode).
- D = −Kd·dmax·TPA·(Fd·gyroADCf)[n] − [n−1])/dt: **on measurement**, through dterm notch, lpf1,
  lpf2. dmax ∈ [1, d_max/d], driven by feedforward (`d_max_advance`) or gyro (`d_max_gain`).
- TPA: factor = 1 − rate/(1−bp)·max(thr−bp, 0) (+ low-throttle TPA, latched after the first crossing).
- pidSum/1000 → mixer (LEGACY, airmode), pidsum_limit 500/400.
- thrust_linear (`pidApplyThrustLinearization`): f(x) = x(1 + e(1−x)(1 + e(1−2x))) per motor, with
  throttle pre-compensated. bftune scales the plant gain by f′ at the operating motor command.

## Feedforward (`fc/rc.c calculateFeedforward`)
Per RX frame: speed = Δsetpoint·rxRate → PT1 (τ = 0.004·s/(1−s), normalised to 250 Hz) →
roll/pitch: + boost = PT1(Δspeed)·rxRate·0.001·boost, × jitter attenuator, max-rate limit;
yaw: × jitter attenuator, + yaw hold G·(sp − PT1_τ(sp)) (no boost) → moving average (averaging + 1
frames) → held, then the RC-smoothing PT3 at the auto cutoff `max(15, rx·1.5/(1+factor/10))`.
bftune uses the linear part (jitter = 1, no max-rate clipping — stick tests stay below 60 % of max rate).

## Chirp (`common/chirp.c`, `pid.c`)
f(t) = f0·β^t, β = (f1/f0)^(1/T); phase = k0·f − k1 with k0 = 2π/ln β; exc = cos(phase).
The excitation passes a lead-lag (pole `chirp_lag_freq_hz`, zero `chirp_lead_freq_hz`), is
scaled by `chirp_amplitude_*` and is **added to the rate setpoint** of one axis. Debug fields:
[0] phase·5000, [1] axis (−1 off), [2] f·10, [3] exc·1000.

## Plant and identification
- Instrument-variable FRF: G = S_zy/S_zu with z = excitation, u = pidSum, y = gyroUnfilt.
- Parametric fit on log-FRF residuals weighted by coherence; structure chosen by AIC.
- Motor model: ARX(1) of eRPM vs motor command per speed bin → τ(ω) = 1/(c0 + c1ω) and the
  authority ω·dω/dcmd; used to reschedule K and τ to other throttle points.

## Noise model
See `noise/model.py`. Decimation preserves variance. A smooth 8 kHz gyro-noise spectrum is fitted so
that its folded images through the known chains match the gyroADC and D-term spectra, and do not
exceed gyroUnfilt. Motor lines are excluded because the RPM filter tracks them. It was validated
out of sample: a model from a 1 kHz log of tune A predicted the D-term noise of a very different
tune B (from a 2 kHz log) within ~15 %.
