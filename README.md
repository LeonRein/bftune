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
5. **Workbench** (`assess`, `sweep`, `suggest`, `ff`, `noise`, `emit`) scores any candidate in about
   a second, so a tuner or agent can iterate. It optimizes disturbance rejection (propwash, turbulence) under robust stability constraints:
   phase margin, gain margin, peak sensitivity and delay margin across idle, hover, mid and full
   throttle, ±10 % actuator gain (battery sag, motor wear), +0.3 ms delay and D-max. Setpoint tracking
   (attitude hold) is part of the objective. The noise budget comes from tunes proven to fly
   with cool motors. Filters, TPA, thrust_linear, RPM and dynamic-notch settings are searched from
   several seeds.
6. **Rules** for the parts a linear model can't score: feedforward from simulated stick flicks,
   RC smoothing, dynamic idle and I-term relax.
7. **Emit** a range-checked CLI block (with a guard against the Configurator's sliders), a revert
   block, a Markdown report and plots.

Everything is a port of Betaflight 2026.6 source code; see [docs/model.md](docs/model.md).

## Install
```bash
uv tool install git+https://github.com/LeonRein/bftune      # or: pipx install git+https://...
bftune --help
```
Claude Code plugin:
```
/plugin marketplace add LeonRein/bftune
/plugin install bftune@bftune
```

## Quick start
```bash
bftune inspect  LOG.BFL --dump dump.txt
bftune analyze  LOG.BFL --dump dump.txt -o out/ --safe-log OLD_TUNE.BFL   # once, ~10-20 s
bftune candidate -o out/ cand.txt            # editable tune file (starts as the logged tune)
bftune assess   -o out/ cand.txt --with-current --with-safe   # verdict + margins + step + noise (~1 s)
bftune noise    -o out/ cand.txt             # filter decisions: noise per band, non-RPM peaks
bftune sweep    -o out/ cand.txt d_roll 20:50:5              # tradeoff table for one setting
bftune suggest  -o out/ cand.txt             # per-axis P/I/D proposal (everything else fixed)
bftune ff       -o out/ cand.txt --axis roll # feedforward: lag vs overshoot
bftune emit     -o out/ cand.txt             # CLI + revert block + report (exit 2 if the verdict FAILs)
bftune optimize -o out_copy/                 # optional automatic baseline (slow)
bftune synth 5inch -o twin.pkl --truth       # synthetic test flight with known truth
```
The intended loop is **hypothesis → edit `cand.txt` → assess/sweep → adjust**, driven by a person or by
the `tuning-engineer` agent following the step-by-step procedure in
[skills/bf-tune/SKILL.md](skills/bf-tune/SKILL.md). The numerics are scripts; the decisions stay
explainable. Every emitted tune must pass the same deterministic **verdict** (PASS/FAIL of every
robustness constraint). `analysis.pkl` and synthetic `.pkl` files are Python pickles, so only load
files you created yourself.

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
