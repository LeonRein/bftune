# Changelog

## 0.8.3
From the first Windows session (Windows 11, Claude desktop app, Git Bash; a full tune of the 3.5" to PASS):
- The launcher didn't find a winget-installed uv (its `WinGet\Links` folder isn't on the agent's PATH). It now
  looks there too, in both `bin/bftune` and `bin/bftune.cmd`, and its "no uv" hint on Windows gives the Windows
  install commands.
- `analyze` crashed printing `≈` on the Windows console (cp1252). The launchers force UTF-8, `main` never fails on
  an unencodable character, and every text file is read and written as UTF-8 (reports, CLI blocks, candidates).
- The agent also hit a dangling uv Python junction (uv bug astral-sh/uv#19622) and worked around it with
  `UV_PYTHON`. That is uv's bug and left to uv.

## 0.8.2
Pre-release audit (before the public announcement). Gate holes, all reproduced on synthetic twins and now
regression tests (`tests/test_release_gate.py`); the decisions are in `docs/knowledge-split.md`:
- **No-chirp gate:** cases the flown tune doesn't have (D-max or the dynamic notch newly enabled) were skipped, so
  a D-max boost with 10° phase margin at hover passed. They are now compared with the flown tune's matching case,
  and the absolute limits stand when nothing matches.
- **Axis without a model** (no chirp on it, or a failed fit): changes to its settings passed unchecked. They now
  FAIL as `unchecked` (the agent can still ship a justified change as an `--experiment`).
- **Delay margin:** the 0.5 ms safety floor is enforced by the gate (it was only shown).
- **Invalid settings:** a typo, an out-of-range value or a battery-scope setting made no CLI line but still
  PASSed. The verdict now FAILs and `emit` refuses to write the block.
- **`diff all` gaps** are the firmware defaults (a diff omits them), not the log header's values: a setting reset
  to default after the logged flight is now seen, so the CLI and the revert block are right.
- `bin/bftune.cmd`: launcher for Windows shells without Git Bash (untested on real Windows).
- README rewritten for pilots: what it is and how to use it first, then install and details.

## 0.8.1
From a real review session (five tunes of the 3.5", "are they good, can they be optimized?"):
- `candidate --from-log LOG`: start from the tune another log flew, to compare flown tunes on one model (the
  agent had to write a Python converter from `tunes --json`).
- `emit --full`: when the tune on the quad is unknown, write every tuning setting (rates untouched), range-checked,
  with no revert block but the pilot's `diff all` (the agent had hand-built such a file next to the emitted one).
- Skills: several tunes in the logs means the fresh `diff all` is what says which one is on the quad, so ask for
  it even when the pilot doesn't know; never hand-edit an emitted CLI.
From the "test tune 2" session (a full tune of the 3.5"): `brief` showed a large measured-vs-model step gap
(3.5" tune D: 7.6 vs 3.8 ms, peak +11 vs +51 %). The logged FF term was only 0.48-0.65x of the modelled one:
Betaflight's FF jitter reduction (rc.c) scales FF down on slow stick changes, and the model took it as 1.
- The as-flown replay now applies the jitter attenuator per sample to the pilot's logged rcCommand (logged FF
  matches the model at 0.84-0.95x, correlation 0.91-0.97), with each candidate's own `feedforward_jitter_factor`.
  Result: 5" 6.6/8.4/3.6 ms, +25/+21/+19 % vs measured 6.8/7.5/4.6 ms, +20/+25/+23 % (was +37-42 %); 3.5" peaks
  +8/+4/+12 % vs +11/+15/+16 % (was +40-53 %). Re-run `analyze` to get it (the log's rcCommand is stored).
- `brief` shows measured vs model peak next to the 50 % time and says `peak_max` is on the model's scale.
- The synthetic twin flies with jitter factor 0 (it has no jitter reduction).

## 0.8.0
An audit of every number and rule in the skills and the judging code (about 460 items) against one question: does
it need Betaflight source knowledge or control theory (the author's), or can the agent derive it better from this
quad's logs, dump and pilot? The decisions and the rule are in `docs/knowledge-split.md`.
- **The log sets the scales.** `analyze` measures a flight profile (hover throttle and motor frequency, the control
  bandwidth from the setpoint -> gyro phase, throttle use, the pilot's typical and fast stick moves, the RC link rate). Diagnose bands (resonance, propwash, I-term,
  motor noise), throttle bands, the bounce-back trigger, the noise model's motion/noise boundary and the chirp
  quality warning scale with it instead of 5" numbers. The "mid" design case sits at the throttle the pilot uses.
- **Robust variants from the data:** the gain spread between chirp rounds (5": ±11 % over 8 rounds), never
  narrower than ±10 %; widen them with `targets`, never narrow them.
- **As-flown step response for every candidate:** the model replays the pilot's logged setpoint (recorded after
  RC smoothing) through the same estimator as the measurement, with the I-term active (on synthetic twins with
  known truth a relaxed-I replay predicted 0 % overshoot where the twin showed 25-40 %). `assess`, `sweep --steps`, `ff` and the report show
  it; the new target `peak_max` defaults to the flown tune's own peak. The typical/fast stick-move tests use the
  pilot's measured moves instead of a 300 deg/s-in-50 ms flick.
- **Data first in diagnose:** imbalance and motor noise are always reported; severities are labelled heuristics;
  saturation respects `motor_output_limit`; the motor-noise band no longer depends on the log rate; crash
  detection needs a pegged gyro.
- **Skills:** every number is a firmware fact, the safety floor, a derivation procedure, or a labelled starting
  point. Per-quad calibration lives in `quad.md` (flown tunes vs feel, noise vs motor temperature, which changes
  the pilot noticed) and drives the targets, the noise budget and the stop rule. Contradictions removed (D/P and
  I/P windows, reference-tune criterion, noise-budget rules, FF pitfall, Ms stop limit).
- Measured on the synthetic twins: the log's control bandwidth matches the model's crossover (whoop 23.4 vs 22.3 Hz,
  5" 16.0 vs 16.8, 10" 8.8 vs 10.6); hover motor frequency does not (a whoop's is 3.5x too high a scale). The
  twins now fly craft-appropriate stick moves with a human rise time and log rcCommand.
- From simulated sessions (a new 3.5" pilot, a returning 5" pilot): `emit --experiment`, `grid --steps`, `suggest`
  constrained by the D-max cases whenever a D-max driver is on and reporting the as-flown effect, a stale-analysis
  warning for analyses built by an older bftune, `logged`/`on quad` instead of `current`, motor imbalance on the
  thrust scale (thrust_linear undone), resonance findings far above the control band attributed to motor lines or
  vibration, `project next-tune-dir` ignoring date headings, a stored reason shown only next to its value.
- From simulated sessions on a synthetic whoop and 10" (cinematic and long-range pilots): idle margins count only
  above 1 / (how long idle stretches last in the log), and a flown tune without an idle crossover no longer imposes
  an absolute PM there (whoops: dyn idle and thrust_linear were blocked by a 1 Hz I-term artefact); motor noise is
  judged at the motors (all axes, as the mixer adds them), so a low-noise yaw axis no longer blocks a tune; model
  replays use exactly the windows the measurement accepted (the as-flown peak no longer jumps between candidates)
  and the peak is taken within the response's own rise.
- From a no-chirp beginner, a review and a flight-plan session: crashes are left out of `analyze`, `diagnose`,
  `errspec` and `logs` automatically (a tree crash had dropped a replay fit from 87 % to 41 %); an rpm drop after
  all three axes jolted is an `impact`, not a stall (it re-labels the 5"'s post-crash "stall" as a hit); `emit`
  never writes a `profile N` it does not know (a log header has no profile index); `candidate --apply FILE`;
  lint for `simplified_*` sliders left on; `sweep`/`grid` mark filtering beyond anything flown (`!`) and show
  the total motor-noise verdict; `brief` flags a GAP between measured and modelled step; `suggest` says when noise
  caps the gains; notes common to all assessed tunes print once; the flight-plan skill carries the firmware facts
  about CHIRP (amplitude in deg/s on the setpoint, exponential sweep, switch-off advances the axis even when
  aborted, blocked in failsafe/GPS rescue); the review skill sets the noise budget, uses diagnose, and says when
  to reject a tune outright.
- Fixes: `coherent_to_hz` is contiguous; `rules.py` uses the FF targets, no unchecked rpm-weight claim, dyn idle
  p20 within the firmware range; `motor_poles` missing from log and dump is reported.

## 0.7.0
Make the agent's judgement, not hidden constants, decide what a good tune is for this quad and pilot, and let it
see the flights.
- **Ask first.** `/bftune:tune`, `feedback` and `review` ask in their first reply, before any long analysis: a fresh
  `diff all`, what the new tune should **prioritise** (ranked: stick response, locked-in, propwash, smoothness, clean
  punch-outs, efficiency) instead of "what did you dislike", style, motor temperature on a 5-step scale, changes.
  New eval `ask-first`.
- **Safety floor vs design targets.** A fixed floor (PM 30 deg, GM 4 dB, Ms 2.6; robust variants 25 / 3 / 3.2; delay
  margin 0.5 ms; noise at most 2x a flown level) that no override can relax. Above it, design targets the agent
  derives from the evidence: frequency bands scale with the flown tune's hover crossover (no size classes), margins
  are calibrated on how the flown tunes measured and how the pilot rated them, priorities weigh them. Remaining
  defaults are labelled `convention` (style presets `<style> convention`). New `bftune targets -o A --set
  'key=value # reason'` stores overrides in `A/targets.json`; every output (assess, brief, emit report) lists each
  target's value and source; violations below the floor are marked as such.
- **Noise budget from motor temperature** (cold 1.25x, cool 1.1x, slightly warm 1.0x, warm 0.85x, hot 0.7x of the
  flown level; a heuristic, labelled so) replaces the silent 0.9. The noise limit in violations says what it is and
  why. `filters-noise` explains why a budget exists at all (HF ripple losses, saturation, resonance, desync) and that
  hot motors on a healthy build point at something else.
- **One report style.** A shared document model renders every report as self-contained HTML (light/dark, cards,
  tables, copyable CLI, embedded figures) plus the same content as Markdown for the agent: `report.html` (emit, the
  deliverable), `analysis.html` (analyze: model, fit, noise, spectrogram, measured response, findings), `logs.html`.
- **Look at the flights.** New `bftune logs LOG...` (one log = report, several = comparison with the settings that
  differ): noise-vs-throttle spectrograms of raw gyro, filtered gyro and D-term with the (aliased) motor harmonics,
  the step response measured from ordinary flying, tracking-error spectra, how hard each flight was flown. New
  `bftune plot LOG --window T0:T1` (sticks, gyro, D-term, motors, rpm) and `--spectrogram`. Propwash and bounce-back
  findings give `worst_at_s` times to look at.
- **Measured stick lag.** The deconvolved step response was biased (settled at ~0.6; a pure 5 ms delay read as 14 ms).
  It is now normalised per window with weaker regularisation (a known delay is recovered to ~1 ms) and carries a
  confidence. `analyze`/`brief` compare it with the model's stick lag for the flown tune (5": 7/8/5 ms measured vs
  7.0/8.8/4.2 ms predicted).
- **Model vs measured, like for like.** Betaflight logs the setpoint after RC smoothing (blackbox.c:
  pidGetPreviousSetpoint), so the model now replays the logged setpoint through FF and the loop, and the same
  windows and estimator turn it into a step. `analysis.html` overlays model and measurement; `brief` compares the
  50 % times (5": 6.8/7.5/4.6 ms measured vs 6.0/7.3/4.0 model) and the peaks (the model's FF peak is higher on
  that quad: 37-42 % vs 20-25 %, a calibration for FF targets). 50 % times are interpolated between samples;
  undershoot (the dip after the peak) is reported for measured and model steps.
- From a cold replay of the whole tune flow on the real 5" data: `--dump` accepts a log ("the quad still runs the
  tune from log X"); `--exclude T0:T1` on analyze/diagnose/errspec/logs/motors, and crash times printed by
  `motors` and `diagnose`; `emit --profile N`; `targets --set` prints the flown tune's verdict under the new
  targets; `suggest` judges feasibility with the delivery gate (D-max and robust cases), lists what fails and
  shows the stick-lag change; `sweep` no longer calls FF/RC settings "no effect" without `--steps`; violation
  messages print the real limit (2.45, not 2.5); diagnose evidence without numpy reprs; `project next-tune-dir`
  counts iterations in history.md; skill guidance for crash-explained stalls, known hardware issues and which
  knobs set stick lag.
- The plugin launcher runs `python -m bftune` (a missing package could make `uv run bftune` call the launcher
  itself 101 times) and says clearly when the plugin folder has no package.
- `optimize`/`all` use the stored targets; the craft-class plausibility table is
  relabelled as hints, not limits.

## 0.6.0
From reviewing two full real sessions (3.5" and 5", 2026-09-26, 214 tool calls): every custom script the agents had
to write became an instrument, and the gate no longer blocks quads it was wrong for.
- **Idle limits relative to the flown tune.** When the tune that flew already misses the idle design limits (large
  props, low idle, motor lag extrapolated at very low rpm), the idle cases require "no worse than the flown tune".
  The 5" was delivered twice with emit FAIL for this reason, and `suggest` chased the unreachable idle gate (P 22-24).
- **Gain margin fixed for conditionally stable loops.** At idle (I-term + large motor lag) the loop crosses -180
  deg at low frequency with |L| > 1; that crossing limits how far the gain may *fall*, but was reported as a negative
  GM (e.g. "-13 dB", read as "unstable") and penalised changes that add authority (more dyn idle, more D). GM is now
  the smaller of the upward and downward margins (both positive); `gm_up_db`/`gm_down_db` are reported too.
- The motor-noise limit shown in violations is the real one (the budget or the flown tune's level), and `suggest`
  optimises against it instead of cutting noise below what already flew.
- Freestyle nominal hover PM floor 42 deg (race 40): tunes pilots rated "feels good" sat at PM 42-47 with Ms <= 2.0.
- New `bftune motors`: rpm collapses classified as stall (commanded up), mixer (commanded down in hard moves) or
  crash; rpm per command per motor, telemetry jitter. `diagnose` reports `motor_stall` only for real stalls
  (the old `possible_desync` fired on normal mixer saturation).
- Low-throttle resonance findings say whether the peak sits at the idle motor frequency and whether a motor line
  survives into the filtered gyro (order tracking). `assess` lints `rpm_filter_min_hz` above the idle motor frequency
  (incl. the dyn idle floor), a TPA breakpoint below hover, and a D-max that can never engage.
- New `bftune grid` (two settings at once, with the reason for every failing cell); `sweep` flags settings that have
  no effect (inactive notch, static LPF under a dynamic one, ...); `sweep` takes several KEY VALUES pairs; `bftune applied` checks a dump
  after pasting (missing lines, wrong profile, settings reset by accident); `tunes --keys` shows settings per log;
  `errspec` reports flying intensity and `--by-throttle`; `project latest-tune-dir`.
- Firmware check: `analyze` refuses logs from releases older than the model (2026.6) unless `--any-firmware`;
  newer or unknown versions get a warning (trust the model only if the filter-chain checks pass). `inspect` shows it.
- The plugin launcher finds uv in ~/.local/bin and ~/.cargo/bin and prints the install command otherwise.
- Skills: storage-aware logging (SD gaps with high resolution, flash capacity), a noise-headroom flight protocol,
  a "know when to stop" rule with perceptibility thresholds, `bftune applied` after pasting, motor reviews,
  D-only TPA lesson, no parallel tool calls that wait on each other.

## 0.5.0
From reviewing a real session on six logs of one quad:
- **Dumps that belong to another tune.** When a dump contradicts the log header on tuning settings, the model now
  uses the log's own settings (what flew), so the chain checks and identification stay valid. The dump becomes
  "the tune on the quad now": candidates start from it, `assess --with-current` shows it as `on_quad`, and the delivered
  CLI and revert block are relative to it. `inspect`, `analyze` and `brief` warn about the mismatch.
- New `bftune tunes LOG... [--dump D]`: groups logs by the tune they flew (headers only, instant), lists the
  differences, and says whether a dump matches any log.
- `bftune candidate --set 'key=value # reason'` (repeatable) creates variants without shell editing; candidate files
  now include every flight-behaviour setting.
- Tune skill: look at the logs first (`tunes`, `inspect`), then **ask before the heavy work** whenever it is unclear
  which tune is on the quad, there is no matching dump, motor temperatures of a reference tune are unknown, or the
  pilot's priorities are unknown. Clear rules for the role of each log (model, noise reference, evidence).

## 0.4.0
- **Full feature coverage.** The agent must now consider every Betaflight setting group that shapes flight behaviour
  (RPM filter, notches, gyro/D-term filters, PIDs, D-max, feedforward and its shaping, RC smoothing, TPA incl. low-TPA,
  thrust_linear/output limit, throttle boost/sag compensation, dynamic idle, I-term, anti-gravity, PID-sum limits, rates):
  test each testable group on the model, check the flight data for the rest, and report what it left unchanged and why.
- New `bftune coverage -o A [cand.txt]`: every group with logged vs candidate values and what can test it
  (classification verified by perturbing each setting on a real analysis; wing-only settings excluded).
- New `diagnose` findings: `throttle_punch_dip` (anti-gravity evidence) and `pidsum_clipping_<axis>`.
- Model: `motor_output_limit` scales actuator authority and motor noise; D-max cases and the step simulation only use
  `d_max` when `d_max_gain` or `d_max_advance` can raise D.
- From the verification run: `sweep` rejects an empty value list; range checks only cover tuning settings (no more
  walls about debug_mode or motor_output_reordering, and an unchanged tune is no longer checked in full); identical
  proven-safe tunes are collapsed in `brief` and `assess --with-safe`; I-term relax/integrated yaw are listed as
  model-testable; skills clarify FF smoothing, the imbalance threshold and pre-bftune log naming.
- Tunes now carry every setting from the dump/log header, so any of them can be changed with a correct revert value
  (unknown old values are never guessed).

## 0.3.0
- **bftune is now an agent, not a pipeline.** The plugin is the product: `/bftune:tune` (or just "tune my quad")
  starts a conversational engineer loop: interview → diagnose → hypotheses → experiments on the model → decision →
  verified delivery → learning from the next flight. Users never run commands.
- New skills: `tune`, `feedback`, `flight-plan`, `review` (user-invocable) and the knowledge skills
  `toolbox`, `diagnosis`, `evidence`, `loop-shaping`, `filters-noise`, `craft-classes`, `deliver` (loaded on demand).
  Replaces the `bf-*` skills.
- `tuning-engineer` agent rewritten for delegated exploration, with the knowledge skills preloaded.
- Per-quad project memory: `bftune project init` (`quad.md`, `history.md`, `logs/`, `analysis/`, `tunes/NN-date/`).
- `bftune diagnose`: problem finder from flight data alone (resonances in the tracking error per throttle band,
  propwash stratified by manoeuvre, bounce-back, part-throttle saturation, motor imbalance, desync, HF motor noise,
  log quality). Diagnosis is also stored in `analysis.json`.
- `bftune brief`: one JSON situation report for the agent.
- Logs without chirps: low-confidence identification from stick inputs (gain ±40 %), widened robustness cases, and a
  **relative** safety gate (no worse than the flown tune).
- `emit --to DIR` writes the deliverables into the tune folder. The plugin ships `bin/bftune` (runs the bundled package via uv).
- `bftune synth`: `--no-chirp`, `--set KEY=VALUE`, `--frame-mode-hz`, `--repeats`, `--freestyle-s` (eval fixtures).
- Plugin evals (`evals/`) on synthetic twins: 5" with chirps, 3.5" without chirp, 10" long range.
- From cold-agent tests: the flown tune's own noise level always passes (the 0.9 margin applies above it;
  `--noise-budget` < 0.9 still forces a cut); no-dump deliveries no longer guess the slider state in the revert block
  and ask for the profile explicitly; report text is honest about the relative gate; `sweep` shows per-axis verdicts,
  full-throttle PM, Ms@Hz and the first violation; `suggest` is limited to ±15 % without a chirp and names the
  violated limit; `diagnose` reports the direction of a motor imbalance (CG/trim); logs are checked for time gaps;
  `brief` starts with a readable summary; reasons carry through the `*_type = OFF` shortcut.
- README rewritten for pilots; the CLI reference moved to `docs/DEVELOPMENT.md`; new `docs/python-api.md`.

## 0.2.0
- **Agent-driven workflow.** The global optimizer is no longer the decision maker. A fast workbench on a cached
  analysis (`candidate`, `assess`, `sweep`, `suggest`, `ff`, `noise`, `emit`, about 1 s per call) lets a person or agent
  iterate hypothesis → evaluate → adjust. `optimize` stays as an optional automatic baseline.
- Skills rewritten as a step-by-step engineering procedure (`bf-tune`) with a pilot-feedback cross-check.
- `analyze --safe-log/--safe-cli` and `bftune safe` store proven-safe tunes for the noise budget.
- New metrics: worst-case labels, stick→gyro (end-to-end) latency, persistent non-RPM peak detection.
- Candidate files carry reasons as inline comments; they flow into the report. `emit` exits 2 on FAIL.
- Self-contained HTML report next to report.md.
- Optimizer fixes from cold tests: anchored gain bounds, D/P window, tracking term, safety margins.

## 0.1.0
- Blackbox decoder, dump parser, settings DB generator (Betaflight 2026.6).
- Chirp instrument-variable identification, chirp reconstruction without debug fields, motor model.
- Filter-chain, closed-loop and replay validation.
- Alias-aware noise model with safe-tune noise budgets.
- Robust multi-start optimizer (PIDs, D-max, TPA, thrust_linear, gyro/D-term filters, dyn notch, RPM Q).
- Feedforward/RC-smoothing/dyn-idle rules, CLI emitter with revert block, Markdown report.
- Synthetic quad twins (whoop, 3.5", 5", 10") and end-to-end tests.
- Claude Code plugin: tuning-engineer agent and 7 skills.
