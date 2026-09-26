---
name: flight-plan
description: Tell a pilot exactly how to record Betaflight blackbox logs that bftune can tune from - chirp (system identification) setup, blackbox settings and the flying procedure, tailored to their craft class and logging hardware, with a paste-ready CLI block. Use when a user has no usable log yet, asks how to fly the test, or a tune needs a chirp flight.
argument-hint: "[craft class or quad project folder]"
allowed-tools: Bash(bftune *), Read, Write, Edit, Glob
---

# Flight plan for a tuning log

Tailor the plan to the craft. Ask what the plan depends on, unless `quad.md` or a dump already says:
size and cells, blackbox on flash or SD, the PID loop rate (the sample-rate fraction depends on it),
the ESC firmware and whether bidirectional DShot is on, and whether the quad has flown yet. Record the
answers in the quad's project folder (`bftune project init`), so the tuning session starts from them.
Then give the pilot:
1. a CLI setup block;
2. the mode setup;
3. the flying procedure;
4. what to send back.

Keep it short and concrete.

## 1. Firmware and modes
- Betaflight 2026.6+ with **chirp support**. Cloud builds may need the custom define `USE_CHIRP`
  if the CHIRP mode is missing from the Modes tab.
- **Modes tab:**
  - Assign **CHIRP** to a switch. One sweep runs per switch-on and stops by itself; every switch-off
    advances the axis (roll → pitch → yaw → roll), **also after a sweep aborted early** (pid.c). To
    redo an axis, fly the others or a full extra round. CHIRP is blocked in failsafe and GPS rescue.
  - An **ANGLE** switch makes the chirps much easier: fly them in ANGLE at a steady hover. Keep GPS
    hold modes (altitude/position hold) off during the sweeps: they add their own corrections.
- What a chirp is, for the pilot: the flight controller adds a sine wave to the rate setpoint on one
  axis, slow at first and exponentially faster up to the end frequency (a wobble that turns into a
  buzz); it is normal and stops by itself.
- Bidirectional DShot (RPM telemetry) must be on. Without it there is no motor model and no RPM
  filter; see `bftune:craft-classes`.

## 2. CLI block (adapt the rate to their logging device)
```
set debug_mode = CHIRP
set blackbox_sample_rate = <see below>  # fastest rate the device logs WITHOUT gaps; 1 kHz is fine
set blackbox_high_resolution = <see below>
set blackbox_disable_debug = OFF
set chirp_frequency_start_deci_hz = 10
set chirp_time_seconds = 20
set chirp_frequency_end_deci_hz = <class>
set chirp_amplitude_roll = <class>
set chirp_amplitude_pitch = <class>
set chirp_amplitude_yaw = <class>
save
```
`chirp_amplitude_*` is in **deg/s added to the rate setpoint** (0-500; firmware default 230/230/180);
the sweep is exponential from start to end over `chirp_time_seconds`, and below 1 Hz the amplitude
is scaled down so the angle stays bounded (common/chirp.c). Heavier, slower quads need a smaller
amplitude to stay controllable and more time at the low end.

**First chirp flight of a quad:** start from the class row *(convention: the 3.5" and 5" rows
worked on real quads; the whoop and 7-10" rows are estimates, and 30 s suits heavy 7-10" builds)*:

| class | end (deci-Hz) | roll/pitch amp | yaw amp | notes |
|---|---|---|---|---|
| tinywhoop 65-75 mm | 3000 | 150 | 120 | often a 4 kHz loop; mind the walls |
| 2.5-3.5" | 2000 | 230 | 180 | |
| 5" | 2000 | 230 | 180 | |
| 7-10" | 1200 | 150 | 120 | more altitude, calm air |

**Later chirp flights: derive the settings from the last analysis** of this quad (`brief` →
`identification`, `warnings`):
- coherent band too short for the control band (`analyze` warns, scaled to this quad) or a low
  replay fit → more amplitude, calmer air, or more rounds;
- motors or PID sum saturating during the sweep (`diagnose` saturation, frf validity) or the pilot
  struggling to hold the hover → less amplitude on that axis;
- structural modes near the end of the band (spectrogram stripes, `noise` persistent peaks) → a
  higher end frequency; a large gain spread between rounds (`uncertainty.source`) → calmer air,
  more rounds;
- `chirp_time_seconds` longer for heavy, slow quads (more cycles at the low end).

bftune handles a 1 kHz log (aliasing is modelled). Faster logs improve the noise estimate, but
never at the cost of gaps.

### Rate, resolution and storage
Choose the settings for the logging device, and check the result with `bftune inspect`, which
reports gaps and corrupt frames:
- **SD card:** gaps come from a slow card.
  - Start at 1 kHz (`1/8` at 8 kHz, `1/4` at 4 kHz) with `blackbox_high_resolution = OFF`.
  - Try 2 kHz or high resolution only if a test log shows no gaps.
  - Seen on a 3.5": 1 kHz with high resolution = 684 gaps (49 s lost); without it, 0 gaps.
- **Onboard flash:** nothing drops, but the flash fills up. Work out what fits from a log this quad
  already recorded: its size divided by its duration (`inspect`) gives bytes per second at those
  settings; compare with the flash size (ask, or from the FC's spec). Seen on a 5": 16 MB held
  about 280 s at 2 kHz with high resolution, one pack; erase before each flight.
  - For a chirp flight, high resolution matters more than the rate: 1 kHz + high resolution.
  - For long freestyle logs, a higher rate or high resolution, not both, if the flash is small.
- Ask which device they have if the dump does not show it (`blackbox_device`).
- Remind them that `blackbox_high_resolution` falls back to its default if a `diff all` restore or a
  reflash drops it.

### Noise-headroom and verification flights
One pack per tune, both logged (the long-log settings for the device; erase flash in between if it
is small). The motor checks are part of the protocol (`bftune:filters-noise`): hover 30 s, land,
touch the motors; one minute of normal flying, land, touch again; then the rest of the pack. The
pilot reports each check on the same temperature scale as before.

## 3. Flying
1. Take off, and hover in ANGLE with steady throttle, preferably in calm air and away from the ground.
2. CHIRP on and hold the hover with small corrections until the sweep ends (20 s), then off. Do it
   for roll, pitch and yaw. **Two or three rounds** of all axes are much better than one.
3. Then 1-2 minutes of their normal flying, covering as much of the throttle range as they are
   comfortable with: for a freestyle pilot flips, rolls, punch-outs, dives and throttle chops; for a
   cinematic or angle-mode pilot their usual lines plus a few climbs and descents; for a long-range
   pilot cruise plus a few seconds each of a steep climb and a descent at low throttle (the idle and
   full-throttle design cases come from there). Quick stick moves on every axis feed the measured
   step response, chops show propwash.
4. Land, feel the motors (cold, cool, slightly warm, warm = can't hold a finger on it for 5 s, or hot:
   the same scale every time), and take a CLI `dump` (or `diff all`) without changing anything.

A **new build that has not flown**: first a maiden hover of 30 s on the stock tune, land and touch the
motors; stop and report if any is hot or you hear oscillation. Take a `diff all` before pasting
anything, as the way back.

## 4. What to send back
- the log file(s) and the dump;
- motor temperature;
- how the current tune feels (what they like and dislike);
- logs of other tunes of this quad, with how warm the motors got and how they felt. These are free
  calibration data.

No chirp is possible (old firmware, no switch)? Normal flying still gives a diagnosis and a
low-confidence model; bftune then only makes small, relative changes.
