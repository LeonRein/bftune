# bftune

**Model-based Betaflight tuning.** Fly a short system-identification flight (Betaflight's
CHIRP mode), hand bftune the blackbox log and your CLI `dump`, and get a PID / feedforward /
filter / TPA tune. The tune is optimized on a model of *your* quad and validated against the log,
with every change explained. It works from 1S tinywhoops to 10" long-range.

It is also a Claude Code plugin: a *tuning-engineer* agent and skills run the toolkit, interpret
the results and write the CLI commands for you.

## How it works
1. **Decode** the blackbox log with a pure-Python decoder (any number of sessions, corrupt-frame recovery).
2. **Identify** the rate dynamics of each axis (pidSum → gyro) with the chirp excitation as an
   instrument variable, so there is no feedback bias. The model structure is motor lag + delay +
   integrator, with a reaction-torque zero on yaw. Motor dynamics vs. RPM come from eRPM telemetry.
3. **Validate** in three ways. The exact Betaflight filter chain is checked against the log (gyroADC
   vs gyroUnfilt, D term). The predicted closed-loop chirp response is compared with the measurement.
   Freestyle sections are replayed through the model.
4. **Noise model.** From an aliased 1-2 kHz log, reconstruct the 8 kHz gyro-noise spectrum that
   explains gyroUnfilt, gyroADC and the D term at once. This predicts motor noise for any filter/D
   combination.
5. **Optimize** disturbance rejection (propwash, turbulence) under robust stability constraints:
   phase margin, gain margin, peak sensitivity and delay margin across idle, hover, mid and full
   throttle, battery sag, extra delay and D-max. The noise budget comes from tunes proven to fly
   with cool motors. Filters, TPA, thrust_linear, RPM and dynamic-notch settings are searched from
   several seeds.
6. **Rules** for the parts a linear model can't score: feedforward from simulated stick flicks,
   RC smoothing, dynamic idle and I-term relax.
7. **Emit** a range-checked CLI block (with a guard against the Configurator's sliders), a revert
   block, a Markdown report and plots.

Everything is a port of Betaflight 2026.6 source code; see [docs/model.md](docs/model.md).

## Install
```bash
uv tool install git+https://github.com/<owner>/bftune      # or: pipx install git+https://...
bftune --help
```
Claude Code plugin:
```
/plugin marketplace add <owner>/bftune
/plugin install bftune@bftune
```

## Quick start
```bash
bftune inspect  LOG00001.BFL --dump dump.txt
bftune analyze  LOG00001.BFL --dump dump.txt -o out/
bftune optimize -o out/ --style freestyle --safe-log OLD_TUNE.BFL   # optional: a tune that flew with cool motors
# -> out/tune_cli.txt, out/revert_cli.txt, out/report.md, plots
bftune evaluate -o out/ my_hand_edits.txt                         # score any CLI changes on the model
```

## Recording the flight (summary)
Firmware 2026.6+ with `USE_CHIRP`, a CHIRP mode switch and `set debug_mode = CHIRP`. Hover in
ANGLE mode and run one 20 s chirp per axis (switch on, wait, off; the axis advances roll → pitch → yaw),
ideally 2-3 rounds. Then fly 1-2 minutes of normal acro including punch-outs. A 1 kHz blackbox rate
is enough; faster is better. See [skills/bf-flight-protocol/SKILL.md](skills/bf-flight-protocol/SKILL.md).

## Status and limits
- Supports Betaflight 2026.6 (SVF filters, chirp, `d_max` semantics). Older firmware needs model
  changes (biquad filters, d_min).
- The model is linear around each operating point. Saturation, airmode transitions and anti-gravity
  are not optimized; they are only guarded by margins.
- Resonances above the log's Nyquist are inferred, not observed. The noise budget and flight
  checks cover that.
- Tested on real logs of a 3.5" 4S quad (two independent tunes, cross-validated) and on synthetic
  twins of a 65 mm whoop, 3.5", 5" and 10".

## Development
```bash
uv sync && uv run pytest            # add -m "not slow" to skip synthetic end-to-end tests
uv run python tools/gen_settings_db.py ../betaflight 2026.6 > src/bftune/data/settings_2026.6.json
```

License: GPL-3.0-or-later (it ports Betaflight's GPL-3.0 algorithms).
