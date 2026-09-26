---
name: flight-plan
description: Tell a pilot exactly how to record Betaflight blackbox logs that bftune can tune from - chirp (system identification) setup, blackbox settings and the flying procedure, tailored to their craft class and logging hardware, with a paste-ready CLI block. Use when a user has no usable log yet, asks how to fly the test, or a tune needs a chirp flight.
argument-hint: "[craft class or quad project folder]"
allowed-tools: Bash(bftune *), Read, Write, Edit, Glob
---

# Flight plan for a tuning log

Tailor the plan to the craft: ask for the size, cells, and whether blackbox goes to flash or SD,
unless `quad.md` or a dump already says. Then give the pilot:
1. a CLI setup block;
2. the mode setup;
3. the flying procedure;
4. what to send back.

Keep it short and concrete.

## 1. Firmware and modes
- Betaflight 2026.6+ with **chirp support**. Cloud builds may need the custom define `USE_CHIRP`
  if the CHIRP mode is missing from the Modes tab.
- **Modes tab:**
  - Assign **CHIRP** to a switch. Each on→off advances the axis (roll → pitch → yaw → roll); one sweep
    runs per switch-on and stops by itself.
  - An **ANGLE** switch makes the chirps much easier: fly them in ANGLE at a steady hover.
- Bidirectional DShot (RPM telemetry) must be on. Without it there is no motor model and no RPM
  filter; see `bftune:craft-classes`.

## 2. CLI block (adapt the rate to their logging device)
```
set debug_mode = CHIRP
set blackbox_sample_rate = 1/2          # fastest rate their device logs WITHOUT gaps; 1 kHz is fine
set blackbox_high_resolution = ON       # 0.1 deg/s; turn OFF if it causes gaps
set blackbox_disable_debug = OFF
set chirp_frequency_start_deci_hz = 10
set chirp_time_seconds = 20
set chirp_frequency_end_deci_hz = <class>
set chirp_amplitude_roll = <class>
set chirp_amplitude_pitch = <class>
set chirp_amplitude_yaw = <class>
save
```
| class | end (deci-Hz) | roll/pitch amp | yaw amp | notes |
|---|---|---|---|---|
| tinywhoop 65-75 mm | 3000 | 150 | 120 | often a 4 kHz loop; mind the walls |
| 2.5-3.5" | 2000 | 230 | 180 | |
| 5" | 2000 | 230 | 180 | |
| 7-10" | 1200 | 150 | 120 | more altitude, calm air |

bftune handles a 1 kHz log (aliasing is modelled). Faster logs improve the noise estimate, but
never at the cost of gaps.

## 3. Flying
1. Take off, and hover in ANGLE with steady throttle, preferably in calm air and away from the ground.
2. CHIRP on and hold the hover with small corrections until the sweep ends (20 s), then off. Do it
   for roll, pitch and yaw. **Two or three rounds** of all axes are much better than one.
3. Then 1-2 minutes of normal ACRO flying: flips, rolls, punch-outs to full throttle, dives and
   throttle chops. This covers the throttle range for the motor and noise models and shows propwash.
4. Land, feel the motors (cool, warm or hot), and take a CLI `dump` (or `diff all`) without changing
   anything.

## 4. What to send back
- the log file(s) and the dump;
- motor temperature;
- how the current tune feels (what they like and dislike);
- logs of other tunes of this quad, with how warm the motors got and how they felt. These are free
  calibration data.

No chirp is possible (old firmware, no switch)? Normal flying still gives a diagnosis and a
low-confidence model; bftune then only makes small, relative changes.
