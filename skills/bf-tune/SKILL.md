---
name: bf-tune
description: End-to-end Betaflight tuning workflow with the bftune toolkit - from blackbox chirp log and CLI dump to validated, explained CLI commands. Use whenever someone wants to tune a Betaflight quad (PIDs, feedforward, filters, TPA, dynamic idle) from a log, for any size from tinywhoop to 10 inch.
---

# Betaflight tuning with bftune

bftune identifies the quad's rate dynamics from **chirp** excitation logged in flight, checks the
model against the log, builds an alias-aware gyro-noise model, then searches PIDs/filters/TPA for the
best disturbance rejection under robust stability margins and a motor-noise budget.

## Requirements
- Betaflight **2026.6+** built with `USE_CHIRP` (`get chirp` in the CLI lists `chirp_*` settings).
- Bidirectional DShot (RPM telemetry) strongly recommended.
- A blackbox log with chirp runs on roll, pitch and yaw (see `bf-flight-protocol`), plus the CLI
  `dump` (or `diff all`) taken with the same settings.
- Install: `uv tool install git+https://github.com/<owner>/bftune` (or `pipx install ...`); in a
  checkout: `uv run bftune ...`.

## Workflow
```bash
bftune inspect LOG.BFL --dump dump.txt            # sessions, chirps, warnings
bftune analyze LOG.BFL --dump dump.txt -o out/     # plant + validation + noise model
bftune optimize -o out/ --style freestyle [--safe-log OTHER.BFL]   # tune search (5-30 min)
bftune evaluate -o out/ my_edits.txt              # check hand edits against the model
```
Outputs in `out/`: `analysis.json`, `plant_bode.png`, `motor_model.png`, `tune.json`,
`report.md`, `loop_compare.png`, `step_compare.png`, `noise_compare.png`, `tune_cli.txt`,
`revert_cli.txt`.

## Decision checklist (do not skip)
1. **Data quality** (`bf-log-analysis`): all three axes chirped, coherent band ≥ 40 Hz on roll/pitch,
   no motor saturation during chirps, and every warning read.
2. **Model validity** (`bf-sysid`): filter-chain checks pass; closed-loop error < 1 dB / 8°;
   plant parameters plausible for the craft class.
3. **Noise budget** (`bf-filters`): by default the current tune's noise is the limit. Only raise it
   with evidence: `--safe-log` from another tune of the *same* quad that flew with cool motors.
4. **Result review** (`bf-pid-optimize`): margins in every case, especially full throttle and idle;
   the D/P ratio is sensible; FF overshoot ≤ the style target.
5. **Delivery** (`bf-cli-output`): CLI with guard, reasons, predicted gains, revert block, and the
   first-flight checklist.

## When not to use the result
- A failed chain check (firmware or model mismatch), or a missing axis.
- The plant is wildly off the craft priors (bent prop, loose FC, desync).
- The log shows mechanical problems: oscillation even with low gains, resonance peaks that don't track
  RPM. Fix the hardware first.
