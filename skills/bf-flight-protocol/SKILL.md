---
name: bf-flight-protocol
description: How to record a Betaflight blackbox log that bftune can identify - chirp mode setup, blackbox settings, flying procedure, per craft class (tinywhoop, 3-5 inch, 7-10 inch). Use when a user has no suitable log yet or asks how to fly the test.
---

# Recording a system-identification flight

## 1. Firmware and modes
- Betaflight 2026.6+ with **chirp support** (`USE_CHIRP`; in the cloud build add the custom define
  `USE_CHIRP` if the CHIRP mode is missing from the Modes tab / `get chirp` returns nothing).
- In the Modes tab, assign **CHIRP** to a switch. Each time the switch goes on→off the axis advances
  (roll → pitch → yaw → roll). One sweep runs per switch-on and stops by itself.
- An ANGLE-mode switch helps a lot: fly the chirps in ANGLE at a steady hover.

## 2. CLI settings (paste, then `save`)
```
set debug_mode = CHIRP
set blackbox_sample_rate = 1/2          # highest rate your logging device handles without gaps
set blackbox_high_resolution = ON       # 0.1 deg/s gyro resolution (skip if it causes gaps)
set blackbox_disable_debug = OFF
set chirp_frequency_start_deci_hz = 10  # 1 Hz
set chirp_time_seconds = 20
```
Per craft class:

| class | chirp_frequency_end_deci_hz | chirp_amplitude_roll/pitch | chirp_amplitude_yaw | notes |
|---|---|---|---|---|
| tinywhoop (1S, 65-75 mm) | 3000 | 150 | 120 | 4 kHz loop is typical; avoid indoor walls |
| 2.5-3.5" | 2000 | 230 | 180 | defaults work well |
| 5" | 2000 | 230 | 180 | |
| 7-10" | 1200 | 150 | 120 | larger craft: keep angles moderate, more altitude |

A 1 kHz log (1/8 at 8 kHz) is fully supported: the noise model handles aliasing. Faster logging
(2 kHz+) improves noise and high-frequency estimates. If the SD card drops frames, go back to a slower rate.

## 3. Flying
1. Take off, hover in ANGLE with steady throttle, preferably in calm air.
2. Flip CHIRP on and hold the hover (small corrections only) until the sweep ends (20 s), then off.
   Repeat for pitch and yaw. **Two or three rounds** of all axes greatly improve the estimate.
3. Then fly 1–2 minutes of normal freestyle in ACRO: flips, rolls, punch-outs to full throttle,
   dives. This covers the throttle range for the motor model and noise model.
4. Land, check motor temperature, and take a CLI `dump` without changing anything.

## 4. Also useful
- Logs of *other* tunes of the same quad, with how warm the motors got and how they flew. They
  provide a proven-safe noise level (`--safe-log`) and out-of-sample validation.
- If `debug_mode` was not CHIRP, bftune can still **reconstruct** the chirps from the CHIRP
  mode switch and the header settings.
