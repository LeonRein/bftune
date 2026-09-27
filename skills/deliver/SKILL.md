---
name: deliver
description: Deliver a Betaflight tune from bftune safely - emit with the PASS gate, the CLI block with the simplified-tuning guard, old-to-new table with reasons, predicted changes, revert block, first-flight plan, next log, and updating the quad's project memory.
user-invocable: false
---

# Delivering a tune

1. **Emit into the tune folder:**
   ```
   bftune emit -o <analysis dir> <dir>/tunes/NN-date/cand.txt --to <dir>/tunes/NN-date
   ```
   This writes `tune_cli.txt`, `revert_cli.txt`, `report.html` (the deliverable: verdict, changes,
   copyable CLI, predicted behaviour with plots, design targets, model), `report.md` (the same as
   text, for you), `tune.json` and the plots.
   - Exit code 2 = **FAIL**: do not deliver it.
   - `emit` lists changes without a `# reason`. Add the reasons, because they become the report's
     "why" column.
   - `tune_cli.txt` already contains:
     - the `simplified_*` = OFF guard (otherwise the Configurator's sliders overwrite explicit values);
     - master settings, then `profile N`, then `save`;
     - ranges validated against the firmware table (`tune.json.problems` must be empty).
   - **No dump:** the profile index is unknown; `tune_cli.txt` then contains a `profile` placeholder
     line the pilot must edit, unless you pass `--profile N` (from the project's older dumps,
     confirmed by the pilot). The slider state comes from the log header when there is one; the
     pilot's `diff all` backup stays the real way back. Say so explicitly.
   - **Tune on the quad unknown** (no fresh `diff all`, several tunes in the logs): ask for the
     `diff all` first. If it really can't be had, `emit --full` writes every tuning setting (rates
     untouched), so the result is right whichever tune is loaded; there is no revert block then,
     only the pilot's backup. Never hand-edit the emitted CLI: it is range-checked as written.
   - Range problems (`tune.json.problems`, printed by `emit`) must be empty for the tune. If only the
     revert block has them (values the firmware would reject), tell the pilot to use their `diff all`
     backup instead of the revert block.
2. **Tell the pilot, in this order and briefly:**
   1. The CLI block to paste into the Configurator's CLI tab, ending with `save`. Tell them to back
      up first with `diff all`.
   2. A table: setting, old → new, why (one line each). Group it: PIDs, FF, filters, TPA/idle.
   3. What should feel different, and the predicted numbers (hover/idle crossover, Ms, stick lag,
      noise vs the safe level). Call them predictions.
   4. The revert block (`revert_cli.txt`).
   5. The first flight:
      - hover 20-30 s, land, feel the motors;
      - punch-outs, listening for oscillation;
      - flips, rolls and dives for propwash and bounce-back;
      - abort at any oscillation or hot motors.
   6. What to log next: the same blackbox settings plus a chirp set if the model was freestyle-only.
      Check that the settings fit the logging device (`bftune:flight-plan`: a flash chip fills in a
      few minutes, and a slow SD card drops frames with high resolution). Then come back with the
      impressions (`bftune:feedback`).
   7. After pasting: ask for a fresh `diff all`, then check it with `bftune applied`.
3. **Update the project memory:**
   - Append to `history.md`:
     ```
     ## NN - <date> - <one-line summary>
     - Based on: <logs>, model source <chirp|freestyle>, safe references <...>
     - Diagnosed: <main limitations>
     - Changed: <key changes and why>
     - Predicted: <what should feel different>
     - Pilot report: (pending)
     ```
   - Update `quad.md` if you learned a lasting fact (hardware, preference, a calibration lesson).
   - The worklog stays in the tune folder.

Never paste the full report into the chat. Link `report.html` (and `analysis.html`) and summarize.
All bftune reports share one style: `analysis.html` (analyze), `logs.html` (logs), `report.html`
(emit).
