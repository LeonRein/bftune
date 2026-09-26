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
| `bftune project next-tune-dir P` / `latest-tune-dir P` | instant | creates and prints `P/tunes/NN-date` for the next iteration / prints the newest one (shell variables don't survive between tool calls: call this instead of storing the path in a file) |
| `bftune tunes LOG [LOG2 ...] [--dump D] [--keys k1,k2] [--json]` | instant | groups logs by the tune they flew (headers only), the settings that differ between the groups, and whether the dump matches a log's tune or none (= the quad runs an unlogged tune). `--keys` prints those settings per log (which tune did this log fly?). Run it first. |
| `bftune applied TUNE_CLI NEW_DUMP [--old OLD_DUMP]` | instant | after the pilot pasted a tune: are all delivered settings on the quad, is the right profile active, and what else changed between the dumps (settings reset or changed by hand). Exit 1 if something is missing. |
| `bftune motors LOG [LOG2 ...]` | 3-10 s/log | motor health: every rpm collapse classified as `stall` (commanded up, rpm fell: ESC/prop/bearing problem), `mixer` (commanded down in hard moves: normal) or `crash`; rpm per command per motor (weak motor), telemetry jitter, hover command share |
| `bftune inspect LOG [--dump D]` | 2-10 s | sessions, duration, log rate, debug mode, chirp runs per axis (reconstructed or not), warnings |
| `bftune diagnose LOG [LOG2 ...] [--dump D] [-v] [--json]` | 3-10 s/log | findings (id, severity, evidence, causes, knobs); works without chirps; compare several logs of one quad |
| `bftune errspec LOG [LOG2 ...] [--by-throttle 0.35]` | 3-10 s/log | free-flight tracking-error spectrum per band and axis, plus how hard each flight was flown (stick RMS, throttle percentiles, motor saturation); a 30-60 Hz bump is the sensitivity peak. A flat shift in every band with more stick activity is flying style. |
| `bftune logs LOG [LOG2 ...] -o DIR [--window T0:T1]... [--no-spectrogram] [--json]` | 10-20 s/log | **look at flights**: `DIR/logs.html` + PNGs (open them with Read): noise-vs-throttle spectrograms (raw gyro, filtered gyro, D-term; motor harmonics drawn in, aliased), the step response measured from the flight (50 % time = stick lag as flown, ±1 ms; overshoot within ~5 % is estimator artefact; `confidence` good/fair/low from the spread over windows: with `low`, fly more stick moves before reading it), tracking-error spectrum, findings with `worst_at_s` times, how hard it was flown; several logs = a comparison with the settings that differ. Also `DIR/logs.json`. |
| `bftune plot LOG --window T0:T1 [-o fig.png]` / `--spectrogram` | 3-10 s | one figure to look at: setpoint vs gyro (filtered and raw), D-term, motors, throttle, rpm vs `rpm_filter_min_hz` in a time window (use the findings' `worst_at_s`), or the spectrogram |
| `bftune analyze LOG [--dump D] -o A [--index N] [--safe-log X]... [--safe-cli Y]...` | 10-60 s | identification (chirp, or a freestyle fallback), validation, noise model, diagnosis → `A/analysis.json`, `A/analysis.pkl`, `A/analysis.html` (report in the common style), `plant_bode.png`, `motor_model.png`, `spectrogram.png`, `log_steps.png`. If the dump differs from the log's tune, the model uses the log's settings, and the dump becomes the **tune on the quad** (`analysis.json` → `dump_mismatch`) |
| `bftune safe -o A [--log X] [--cli Y]` | 5 s | lists or adds proven-safe tunes (noise references) to an analysis |
| `bftune brief -o A [--style S]` | 5-15 s | **the situation report** (JSON, also `A/brief.json`): identification source, uncertainty and plant per axis; flight facts; current tune; its assessment; proven-safe tunes with their diffs and assessments; noise bands and persistent peaks; findings; warnings |

Options shared by the log tools: `--exclude T0:T1` (repeatable) on `analyze`, `diagnose`, `errspec`,
`logs` and `motors` treats a window as disarmed (a crash: `motors` prints crash times). `--dump`
accepts a CLI dump/diff **or a log** whose header is the tune on the quad now.

## The fast workbench (about 1 s per call, needs `A`)
Global flags: `--style freestyle|race|cinematic|longrange` and `--noise-budget X` override the stored
targets for one call; normally set them once with `bftune targets` (below).

| command | answers |
|---|---|
| `bftune targets -o A [--set 'key=value # reason']... [--unset key] [--reset]` | the design targets this analysis uses (Ms, PM, GM, noise budget, FF overshoot, D/P window, performance band, ...), with their source (`from the flown tune's crossover` = derived from the data, `convention` / `<style> convention` = a starting point, `override` = yours), the reason, and the fixed safety floor. Overrides are stored in `A/targets.json` and apply to every later command. Values beyond the floor are rejected. |
| `bftune candidate -o A cand.txt [--base other.txt] [--set 'key=value # reason']...` | writes a candidate file: the tune on the quad (or `--base`) plus the `--set` changes. Use it for every variant instead of editing files in the shell. It covers all flight-behaviour settings. |
| `bftune assess -o A f1.txt [f2.txt ...] [--with-current] [--with-safe] [--fast] [--json]` (`--with-current` adds `current` = the logged tune and, if the dump differs, `on_quad`) | verdict (PASS/FAIL, `gate` absolute or relative), per axis hover/idle/full crossover, PM, GM, Ms (+ frequency), worst case and label, noise vs safe, stick-flick lag and overshoot, violations, notes |
| `bftune sweep -o A cand.txt KEY VALUES [KEY2 VALUES2 ...] [--steps]` | one table per setting: one row per value and axis with the axis verdict, hover crossover/PM/Ms@Hz, full PM, worst PM/Ms, noise, objective, first violation. VALUES are `20:50:5` or `a,b,c`; `100,100,0;100,50,0` for array settings. Several pairs = several independent sweeps in one call (no shell loops). |
| `bftune grid -o A cand.txt KEY1 VALUES1 KEY2 VALUES2` | two settings at once (e.g. `p_pitch 38,40,42 d_pitch 35,37,39`): per axis a matrix of ok/NO, hover Ms, worst PM, noise, objective. Use it for P x D windows instead of scripts. |
| `bftune suggest -o A f1.txt [f2.txt ...] [--axis roll]` | P/I/D/d_max proposal per axis with everything else fixed (limited to ±15 % of the flown gains when there is no chirp; `feasible: false` = nothing meets every limit, the least-violating point is shown); with several files, the verdict and objective of each at its best gains (to compare filter layouts) |
| `bftune ff -o A cand.txt --axis roll --values 60:180:10` | FF value → stick lag, flick and snap overshoot, settle time |
| `bftune noise -o A [cand.txt]` | per throttle band: measured D and gyro noise, fit error, non-RPM peaks (persistent?), safe level, candidate |
| `bftune coverage -o A [cand.txt] [--json]` | every flight-behaviour feature group: logged vs candidate values, `changed`, and what can test it (`model`, `step`, `idle case`, `none` = judgement from data) with a how-to hint |
| `bftune emit -o A cand.txt [--to DIR] [--profile N]` | deliverables relative to the tune on the quad (`tune_cli.txt`, `revert_cli.txt`, `report.html` = the deliverable, `report.md` = the same as text, `tune.json`, plots); **exit 2 = FAIL**. `--profile N` fills the PID profile when no dump gives it (confirm with the pilot). |
| `bftune optimize -o A_COPY` | slow automatic baseline (10-60 min). Use it only as a second opinion, in a copy of `A` |

Shell:
- Give sweeps all their values and keys in one call instead of shell loops. The Bash tool may not
  be the user's login shell (zsh does not word-split `$var`), so write plain POSIX/bash.
- Run dependent steps in one command or one after the other, never as parallel tool calls that
  wait for each other's files.
- Before writing a Python script, check whether a command above already answers the question.

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
