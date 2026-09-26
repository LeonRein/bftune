# Development

bftune has two layers:

| layer | where | role |
|---|---|---|
| **agent** | `skills/`, `agents/`, `.claude-plugin/` | the tuning engineer: interview, diagnosis, hypotheses, decisions, explanations, project memory |
| **instruments** | `src/bftune/` (Python, CLI `bftune`) | decoding, identification, noise model, margins, simulation, the safety gate, CLI output |

The instruments never decide. The agent never computes margins by hand. Keep that split when
contributing: new numerics go into the package as a command or API function, and new engineering
judgement goes into a skill.

## Repository layout
```
.claude-plugin/        plugin.json, marketplace.json
bin/bftune             launcher on the agent's PATH (uv run --project <plugin root>)
skills/
  tune/ feedback/ flight-plan/ review/          user-invocable entry points (/bftune:<name>)
  toolbox/ diagnosis/ evidence/ loop-shaping/   knowledge the agent loads on demand
  filters-noise/ craft-classes/ deliver/
agents/tuning-engineer.md   subagent for delegated, numerically heavy exploration
evals/                 plugin eval cases (claude plugin eval)
src/bftune/
  io/        blackbox decoder, dump/diff parser, settings DB
  flight.py  decoded flight (units, masks) and summary
  model/     firmware port: filters, controller, plant, parameters
  sysid/     chirp detection/reconstruction, IV FRFs, fits, motor model, validation, freestyle fallback
  noise/     alias-aware noise model and budgets
  analysis/  diagnose (problem finder), errspec, loop metrics, step simulation
  optimize/  cases, margins, objective, per-axis search, rules, optional global optimizer
  emit/      CLI block, revert block, diff table
  report/    Markdown/HTML report and plots
  synth/     synthetic quad twins with known truth
  project/   per-quad project folder helpers
  pipeline.py, workbench.py, cli.py
docs/        model.md (equations with firmware references), craft-priors.md, python-api.md
tests/
```

## Setup
```bash
uv sync
uv run pytest -m "not slow"     # fast unit tests
uv run pytest                   # includes synthetic end-to-end tests (a few minutes)
uv run ruff check src tests
```
Settings DB for a firmware version:
```bash
uv run python tools/gen_settings_db.py ../betaflight 2026.6 > src/bftune/data/settings_2026.6.json
```
Try the plugin from a clone without installing it:
```bash
claude --plugin-dir .
```

## CLI reference (the agent's instruments)
Normally only the agent calls these. `skills/toolbox/SKILL.md` is the agent-facing reference.
```bash
bftune project init P --name NAME             # per-quad folder: quad.md, history.md, logs/, analysis/, tunes/
bftune inspect  LOG --dump DUMP               # sessions, chirps, warnings
bftune diagnose LOG [LOG2 ...] -v             # problem finder (no chirp needed)
bftune errspec  LOG [LOG2 ...]                # tracking-error spectra (pilot cross-check)
bftune analyze  LOG --dump DUMP -o A [--safe-log X] [--safe-cli Y]   # once per log, 10-60 s
bftune brief    -o A                          # JSON situation report
bftune candidate -o A cand.txt                # editable tune file (the logged tune)
bftune assess   -o A cand.txt [more.txt] --with-current --with-safe  # verdict + margins + step + noise (~1 s)
bftune sweep    -o A cand.txt d_roll 20:50:5  # tradeoff table for one setting
bftune suggest  -o A cand.txt [v2.txt ...]    # per-axis P/I/D proposal; compare layouts at best gains
bftune ff       -o A cand.txt --axis roll     # feedforward: lag vs overshoot
bftune noise    -o A [cand.txt]               # noise per band, non-RPM peaks, candidate vs safe
bftune emit     -o A cand.txt --to DIR        # CLI + revert + report (exit 2 if the verdict FAILs)
bftune optimize -o A_COPY                     # optional automatic baseline (slow)
bftune synth 5inch -o twin.pkl --truth        # synthetic flight with known truth
```
`analysis.pkl` and synthetic `.pkl` files are Python pickles. Only load files you created yourself.

## How the instruments work
1. **Decode** with a pure-Python blackbox decoder (multiple sessions, corrupt-frame recovery).
2. **Identify** the rate dynamics per axis (pidSum → gyro), using the chirp excitation as an instrument
   variable, so there is no feedback bias.
   - Structure: motor lag + delay + integrator, plus the reaction-torque zero on yaw.
   - Motor lag vs rpm comes from eRPM telemetry.
   - Without chirps, a low-confidence fallback uses the stick input as the instrument (gain only,
     ±40 %). The gate then becomes relative to the flown tune.
3. **Validate:**
   - the exact filter chain against the log (gyroADC vs gyroUnfilt, the D term);
   - the closed-loop chirp response;
   - a replay of the freestyle sections.
4. **Noise model:** reconstructs the 8 kHz gyro-noise spectrum that explains gyroUnfilt, gyroADC and
   the D term at once from an aliased 1-2 kHz log. It predicts the motor noise for any filter/D
   combination. The budget is relative to tunes that flew with cool motors.
5. **Workbench:**
   - margins (PM, GM, Ms, delay margin) at idle, hover, mid and full throttle, including gain/delay
     variants, D-max and the dynamic notch at its minimum;
   - an objective of disturbance rejection plus tracking;
   - step simulation with FF, RC smoothing, TPA and D-max;
   - the PASS/FAIL gate.
6. **Diagnose:** heuristics on the flight data, independent of the model. Resonances in the
   tracking-error spectrum per throttle band, propwash stratified by manoeuvre, bounce-back,
   saturation, imbalance, desync and HF motor noise.
7. **Emit:** a range-checked CLI block (with the simplified-slider guard), a revert block, and a
   report with plots.

## Evals
`evals/` contains plugin eval cases. Each builds a synthetic quad twin with known truth in its
scaffold script, so no private logs are needed. Run them with:
```bash
claude plugin eval . --scaffold             # all cases (scaffold builds the twins)
claude plugin eval . --scaffold --case tune-5inch
```
The graders check that a CLI was delivered and passed the gate, that the agent based its decisions on
the evidence, used large-craft judgement on the 10" twin, behaved conservatively without a chirp,
and that the conversation explains its reasoning. To evaluate on real logs, put them outside
the repository and point `EVAL_LOG_DIR` at them (see `evals/README.md`).

## Releasing
- Bump the version in `pyproject.toml`, `src/bftune/__init__.py` and `.claude-plugin/plugin.json`.
- Update `CHANGELOG.md`.
- Tag the release.
