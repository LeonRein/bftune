---
name: bf-log-analysis
description: Decode and sanity-check Betaflight blackbox logs with bftune (inspect/analyze), read analysis.json, spot data-quality problems (saturation, missing chirps, aliasing, quantization, corrupt frames). Use before trusting any identification or tune.
---

# Log analysis and sanity checks

`bftune inspect LOG --dump dump.txt` lists sessions, chirp runs (axis, time, frequency range,
throttle, ANGLE mode, reconstructed or not) and warnings. `bftune analyze LOG --dump DUMP -o OUT
[--safe-log OTHER.BFL]` writes `analysis.json` plus a cached `analysis.pkl` that the fast workbench
uses. Add proven-safe tunes later with `bftune safe -o OUT --log X.BFL` or `--cli diff.txt`.

## What to check
| item | where | good | action if bad |
|---|---|---|---|
| corrupt frames | inspect | 0 (a few are OK) | many: SD card or logging rate too high |
| chirps per axis | inspect / `chirp_runs` | ≥1 each, ideally 2-3 | refly per `bf-flight-protocol` |
| sweep complete | `f` reaches end frequency | yes | switch was turned off early |
| throttle during chirps | `chirp_runs[].throttle` | steady hover | large drift adds bias |
| log rate | `log_rate_hz` | ≥1 kHz | below: HF estimates weak |
| high resolution | warnings | ON | OFF limits coherence above ~100 Hz |
| motor saturation | warnings | < 2% | lower amplitude / check craft |
| loop clock skew | notes | any | handled automatically (gyro clock ≠ MCU clock) |

## Units in the decoded Flight
- gyro/gyro_unfilt/setpoint: deg/s. gyro_unfilt is before every software filter.
- P/I/D/F: pidSum units (1000 = full mixer authority).
- motor: 0..1 over the DShot output range; `motor_hz` = eRPM·100/60/(poles/2).
- Blackbox logging is every-Nth-sample with **no anti-alias filter**. Spectra above Nyquist fold
  down. Never read frequencies of noise peaks above `log_rate/2` directly.
