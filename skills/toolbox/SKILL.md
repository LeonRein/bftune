---
name: toolbox
description: Reference for the bftune command-line instruments used while tuning a Betaflight quad - inspect, diagnose, analyze, brief, candidate files, assess, sweep, suggest, ff, noise, errspec, safe, emit, project - with their inputs, outputs, run times and the Python API for custom investigations.
user-invocable: false
---

# bftune toolbox

`bftune` is on PATH while the plugin is enabled (it runs the bundled package through `uv`). All
commands print human-readable text; most accept `--json`. Paths below:
- `A` is an analysis directory (one per analyzed log);
- `P` is the quad's project folder;
- `cand.txt` is a candidate file.

## Setup and data
| command | time | what it gives you |
|---|---|---|
| `bftune project init P --name NAME` | instant | `quad.md`, `history.md`, `logs/`, `analysis/`, `tunes/`; never overwrites |
| `bftune project next-tune-dir P` | instant | creates and prints `P/tunes/NN-date` for the next iteration |
| `bftune tunes LOG [LOG2 ...] [--dump D] [--json]` | instant | groups logs by the tune they flew (headers only), the settings that differ between the groups, and whether the dump matches a log's tune or none (= the quad runs an unlogged tune). Run it first. |
| `bftune inspect LOG [--dump D]` | 2-10 s | sessions, duration, log rate, debug mode, chirp runs per axis (reconstructed or not), warnings |
| `bftune diagnose LOG [LOG2 ...] [--dump D] [-v] [--json]` | 3-10 s/log | findings (id, severity, evidence, causes, knobs); works without chirps; compare several logs of one quad |
| `bftune errspec LOG [LOG2 ...]` | 3-10 s/log | free-flight tracking-error spectrum per band and axis; a 30-60 Hz bump is the sensitivity peak |
| `bftune analyze LOG [--dump D] -o A [--index N] [--safe-log X]... [--safe-cli Y]...` | 10-60 s | identification (chirp, or a freestyle fallback), validation, noise model, diagnosis → `A/analysis.json`, `A/analysis.pkl`, `plant_bode.png`, `motor_model.png`. If the dump differs from the log's tune, the model uses the log's settings, and the dump becomes the **tune on the quad** (`analysis.json` → `dump_mismatch`) |
| `bftune safe -o A [--log X] [--cli Y]` | 5 s | lists or adds proven-safe tunes (noise references) to an analysis |
| `bftune brief -o A [--style S]` | 5-15 s | **the situation report** (JSON, also `A/brief.json`): identification source, uncertainty and plant per axis; flight facts; current tune; its assessment; proven-safe tunes with their diffs and assessments; noise bands and persistent peaks; findings; warnings |

## The fast workbench (about 1 s per call, needs `A`)
Global flags: `--style freestyle|race|cinematic` and `--noise-budget 0.9`.

| command | answers |
|---|---|
| `bftune candidate -o A cand.txt [--base other.txt] [--set 'key=value # reason']...` | writes a candidate file: the tune on the quad (or `--base`) plus the `--set` changes. Use it for every variant instead of editing files in the shell. It covers all flight-behaviour settings. |
| `bftune assess -o A f1.txt [f2.txt ...] [--with-current] [--with-safe] [--fast] [--json]` (`--with-current` adds `current` = the logged tune and, if the dump differs, `on_quad`) | verdict (PASS/FAIL, `gate` absolute or relative), per axis hover/idle/full crossover, PM, GM, Ms (+ frequency), worst case and label, noise vs safe, stick-flick lag and overshoot, violations, notes |
| `bftune sweep -o A cand.txt KEY VALUES` | one row per value and axis: axis verdict, hover crossover/PM/Ms, full PM, worst PM/Ms, noise, objective, first violation. VALUES are `20:50:5` or `a,b,c`; lists like `100,100,0;100,50,0` for array settings |
| `bftune suggest -o A f1.txt [f2.txt ...] [--axis roll]` | P/I/D/d_max proposal per axis with everything else fixed (limited to ±15 % of the flown gains when there is no chirp; `feasible: false` = nothing meets every limit, the least-violating point is shown); with several files, the verdict and objective of each at its best gains (to compare filter layouts) |
| `bftune ff -o A cand.txt --axis roll --values 60:180:10` | FF value → stick lag, flick and snap overshoot, settle time |
| `bftune noise -o A [cand.txt]` | per throttle band: measured D and gyro noise, fit error, non-RPM peaks (persistent?), safe level, candidate |
| `bftune coverage -o A [cand.txt] [--json]` | every flight-behaviour feature group: logged vs candidate values, `changed`, and what can test it (`model`, `step`, `idle case`, `none` = judgement from data) with a how-to hint |
| `bftune emit -o A cand.txt [--to DIR]` | deliverables relative to the tune on the quad (`tune_cli.txt`, `revert_cli.txt`, `report.md/html`, `tune.json`, plots); **exit 2 = FAIL** |
| `bftune optimize -o A_COPY` | slow automatic baseline (10-60 min). Use it only as a second opinion, in a copy of `A` |

Shell: give a sweep all its values in one call (`20:50:5`, `a,b,c`, `x;y` for arrays) instead of a
shell loop. The Bash tool may not be the user's login shell, so write plain POSIX/bash.

## Candidate files
```
set p_roll = 42          # hover Ms 2.0 at 44 Hz; noise 0.86x safe
set dterm_lpf2_type = PT3
set gyro_lpf1_type = OFF # shortcut: zeroes that filter's cutoffs
profile 0                # ignored; values apply to the tuned profile
```
Later lines override earlier ones. Unknown or out-of-range settings are reported by
`assess`/`emit`. Every changed line should carry a `# reason`, because `emit` shows reasons in
the report.

## Python API (custom investigations)
When no command answers your question, write a short script (run it with `uv run --project
"${CLAUDE_PLUGIN_ROOT}" python script.py`). See `docs/python-api.md` in the plugin for details.
```python
from bftune.flight import load_flight            # Flight: t, gyro, gyro_unfilt, setpoint, pidsum, P/I/D/F, motor, motor_hz, throttle, vbat, debug
from bftune.analysis.diagnose import diagnose    # list of findings
from bftune.workbench import Workbench, apply_setting
wb = Workbench("A", style="freestyle")           # loads analysis.pkl
t = apply_setting(wb.logged.copy(), "d_roll", 30)
wb.assess(t)                                     # same dict as `assess --json`
wb.an.idn.axes[0].plant.fr(f)                    # plant frequency response; wb.an.nm = noise model
```
