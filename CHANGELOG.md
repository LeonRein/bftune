# Changelog

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
