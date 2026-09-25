# Changelog

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
