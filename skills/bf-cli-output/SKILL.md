---
name: bf-cli-output
description: Deliver a Betaflight tune safely - CLI block with simplified-tuning guard and save, per-change reasons, predicted improvements, revert block, first-flight checklist and next-log instructions. Use as the final step of any tuning task.
---

# Delivering the tune

1. `bftune emit -o OUT cand.txt` writes `tune_cli.txt`, `revert_cli.txt`, `report.md`, `tune.json` and
   plots. An exit code of 2 means the verdict is FAIL, so do not deliver. The `# reason` comments in the
   candidate file become the report's "why" column; `emit` lists changes that have no reason.
   `tune_cli.txt` already contains:
   - `set simplified_pids_mode/dterm_filter/gyro_filter = OFF` — otherwise the Configurator's
     sliders (MSP_SET_SIMPLIFIED_TUNING) overwrite explicit PID and filter values later.
   - master settings first, then `profile N` and the profile settings, then `save`.
   - Ranges validated against the firmware settings table (`tune.json.problems` must be empty).
2. Present an old → new table with a one-line *why* per setting (from `tune.json.changes`).
3. State the predicted effect: crossover, phase margin and Ms at hover/idle/full, tracking lag,
   overshoot, and noise vs budget (from `report.md`). Do not overclaim; it's a model.
4. Give the revert block (`revert_cli.txt`).
5. First flight: hover 20-30 s → land → motor temperature; then punch-outs (listen for oscillation
   at full throttle); then flips/rolls and dives (propwash, bounce-back). Abort at any oscillation.
6. Next iteration: fly the new tune with a chirp set and the same blackbox settings, then run
   `bftune analyze` again. Pass the previous tune as `--safe-log` if the motors stayed cool.
