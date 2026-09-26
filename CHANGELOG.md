# Changelog

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
  RC smoothing) through the same estimator as the measurement. `assess`, `sweep --steps`, `ff` and the report show
  it; the new target `peak_max` defaults to the flown tune's own peak. The typical/fast stick-move tests use the
  pilot's measured moves instead of a 300 deg/s-in-50 ms flick.
- **Data first in diagnose:** imbalance and motor noise are always reported; severities are labelled heuristics;
  saturation respects `motor_output_limit`; the motor-noise band no longer depends on the log rate; crash
  detection needs a pegged gyro.
- **Skills:** every number is a firmware fact, the safety floor, a derivation procedure, or a labelled starting
  point. Per-quad calibration lives in `quad.md` (flown tunes vs feel, noise vs motor temperature, which changes
  the pilot noticed) and drives the targets, the noise budget and the stop rule. Contradictions removed (D/P and
  I/P windows, reference-tune criterion, noise-budget rules, FF pitfall, Ms stop limit).
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
