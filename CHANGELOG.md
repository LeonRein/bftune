# Changelog

## 0.6.0
From reviewing two full real sessions (3.5" and 5", 2026-09-26, 214 tool calls): every custom script the agents had
to write became an instrument, and the gate no longer blocks quads it was wrong for.
- **Idle limits relative to the flown tune.** When the tune that flew already misses the idle design limits (large
  props, low idle, motor lag extrapolated at very low rpm), the idle cases require "no worse than the flown tune".
  The 5" was delivered twice with emit FAIL for this reason, and `suggest` chased the unreachable idle gate (P 22-24).
- Freestyle nominal hover PM floor 42 deg (race 40): tunes pilots rated "feels good" sat at PM 42-47 with Ms <= 2.0.
- New `bftune motors`: rpm collapses classified as stall (commanded up), mixer (commanded down in hard moves) or
  crash; rpm per command per motor, telemetry jitter. `diagnose` reports `motor_stall` only for real stalls
  (the old `possible_desync` fired on normal mixer saturation).
- Low-throttle resonance findings say whether the peak sits at the idle motor frequency and whether a motor line
  survives into the filtered gyro (order tracking). `assess` lints `rpm_filter_min_hz` above the idle motor frequency
  (incl. the dyn idle floor), a TPA breakpoint below hover, and a D-max that can never engage.
- New `bftune grid` (two settings at once); `sweep` takes several KEY VALUES pairs; `bftune applied` checks a dump
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
