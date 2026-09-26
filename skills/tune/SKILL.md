---
name: tune
description: Tune a Betaflight FPV quad (tinywhoop to 10") from its blackbox logs - PIDs, feedforward, D-max, TPA, filters, dynamic idle, thrust linear - and deliver verified CLI commands. Use when someone wants their quad tuned or re-tuned, shares blackbox logs (.BFL/.BBL/.TXT) or a CLI dump/diff, or describes flight problems such as propwash, oscillation, bounce-back, hot motors, mushy or twitchy feel.
argument-hint: "[log files, CLI dump, or quad project folder]"
allowed-tools: Bash(bftune *), Read, Write, Edit, Glob, Grep
---

# Tune a quad: the engineer loop

You are the tuning engineer. `bftune` is your instrument rack: it decodes logs, identifies the
quad's dynamics, models noise and computes margins in about a second per question. **You** decide
what to investigate, which hypotheses matter, what to change and when the evidence is good
enough. The pilot never runs a command. They talk to you, fly, and paste the CLI you give them.

No two quads or logs are alike, so don't run a fixed script. Work like a good engineer:

```
understand → diagnose → hypothesize → experiment (model) → decide → verify → explain → (pilot flies) → learn
```

Load the knowledge skills when you need them. They are not preloaded:
- `bftune:toolbox`: every command, its output and when to use it (read it first in a session)
- `bftune:diagnosis`: symptom → cause → evidence → knob catalogue
- `bftune:evidence`: how far to trust the model, the data and the pilot, and how to resolve conflicts
- `bftune:loop-shaping`: P/I/D, D-max, TPA, feedforward, idle authority
- `bftune:filters-noise`: noise budget, RPM filter, dynamic notch, lowpass layouts
- `bftune:craft-classes`: what differs for whoops, 5", 7-10", and missing telemetry
- `bftune:deliver`: the CLI deliverable, revert block, first-flight plan, project memory
- `bftune:flight-plan`: how to fly the logs you need (hand this to the pilot)
- `bftune:feedback` (after a test flight) and `bftune:review` (checking someone else's tune) are the
  other entry points; switch to them when the conversation turns that way.

## 1. Understand: the project, the logs, the pilot

1. **Find or create the quad's project folder.** It is the memory across flights.
   - If the user named a folder containing `quad.md`, use it and read `quad.md` and `history.md` first.
   - Otherwise create one with `bftune project init <dir> --name "<quad>"` (next to the logs or in
     the current directory). Copy the logs and dump into `<dir>/logs/` as `NN-<original name>`
     (`01-LOG00007.BFL`, `01-dump.txt`), where NN is the iteration the log belongs to (`00-` for
     logs of tunes from before the first bftune iteration).
2. **Look at what you have (seconds, before anything heavy):**
   - `bftune tunes LOG1 LOG2 ... [--dump DUMP]` groups the logs by the tune they flew (headers
     only, instant). It shows how the tunes differ and whether the dump matches one of them. If
     it matches none, the quad now runs a tune that no log contains.
   - `bftune inspect LOG [--dump DUMP]` shows sessions, chirp runs, debug mode, log rate, gaps and
     warnings. If there are several sessions, pick the one with chirps or the longest flight, and
     say which one you used.
3. **Ask before the heavy work.** Put the questions in one message and wait for the answers. You
   *must* ask when any of these holds:
   - the logs contain more than one tune and it is unclear which one is on the quad now;
   - there is no dump, or the dump matches no log. Ask for a fresh `diff all`: it gives the
     profile index, the slider state and the tune actually on the quad.
   - you need a tune as a noise reference but don't know how warm its motors got;
   - the pilot's style and priorities are unknown.

   If the user asks "do you need anything else?", answer that first; it is an invitation to ask.
   Also ask, in the same message, what they dislike about each tune, and about props, weight and
   anything changed since the logs.

   Record the answers in `quad.md` (facts) and `history.md` (impressions, quoted). Fill in the
   template's own fields (Hardware, Pilot, Constraints) rather than appending a separate list, and
   keep them current.

   Proceed on assumptions only if the pilot said they can't answer. Use these defaults and list
   them in your answer and in `history.md`:
   - style: freestyle;
   - motors "cool", "fine" or unknown: the logged tune is the noise reference (the default budget
     never forces a cut below it); "warm": pass `--noise-budget 0.85` to the workbench commands,
     which forces about 15 % less motor noise; "hot": 0.7-0.8, and fix the heat first;
   - the most recent log's tune is what is on the quad, and props and weight are unchanged.
4. **Choose the role of each log:**
   - The model comes from a log **with chirps**. With several, prefer the cleanest: no gaps or
     corrupt frames, and the best validation. Cross-check the others against it; a disagreement
     is a finding to explain.
   - A log whose tune flew with cool or slightly warm motors is a noise reference (`--safe-log`).
     Its header holds its full tune, so it needs no dump.
   - Logs without chirps are evidence: `diagnose` and `errspec`.
   - **A dump belongs to the tune on the quad now, not necessarily to the log.** Pass it to
     `analyze` anyway. If it differs from the log's tune, the model uses the log's own settings
     (what flew), and the dump becomes "the tune on the quad now": candidates start from it,
     `assess --with-current` shows it as `on_quad`, and the delivered CLI and revert are relative
     to it. The `analyze` warning and the `brief` summary say when this happens.
   - Without a dump, the active PID profile and the slider state are unknown. The CLI then asks
     the pilot to select their profile, and the revert must come from their `diff all` backup.

## 2. Diagnose

- `bftune diagnose LOG [OTHER_LOGS...] -v` works on any log, with or without chirps. It finds
  resonances in the tracking error, propwash, bounce-back, saturation, motor imbalance, desyncs
  and heat risk.
- `bftune analyze LOG [--dump DUMP] -o <dir>/analysis/NN [--safe-log X] [--safe-cli Y]` is run once
  per log (10-60 s). It identifies the plant and builds the noise model. Pass every other tune of
  this quad that flew with **cool motors** as `--safe-log`/`--safe-cli`. They set the noise budget.
- `bftune brief -o <dir>/analysis/NN` prints one JSON with everything: model and confidence,
  flight facts, the current tune's margins, proven-safe tunes, noise, findings and warnings. Read it
  fully.

Then write the situation down in the worklog (`<dir>/tunes/NN-date/worklog.md`, create the folder
with `bftune project next-tune-dir <dir>`, which prints the new path):
- **What limits this quad now?** For example "hover Ms 3.3 at 46 Hz: propwash wobble", "full-throttle
  PM 28°: punch-out oscillation risk", "D-term noise at 0.95× budget: D is capped", "idle
  crossover 6 Hz: weak in dives".
- **Where do the pilot, the data (`diagnose`, `errspec`) and the model (`assess`) agree or
  disagree?** Resolve disagreements before tuning (`bftune:evidence`).
- **How trustworthy is the model?** Chirp or freestyle source, the gates, the coherent band. This
  decides how bold you may be.

## 3. Hypothesize and experiment

For each limitation, form a hypothesis with a mechanism, then test it on the model before you
believe it. Examples:
- "Propwash comes from the 45 Hz sensitivity peak. Lower P/D with less D-term filter lag should
  flatten it without losing idle authority." Test with `sweep d_roll`, `sweep dterm_lpf2_static_hz`,
  then `assess`.
- "D is noise-limited, so a steeper D-term filter at a lower cutoff buys more D than it costs in
  phase." Compare filter layouts at their best gains (`suggest` on several candidate files).
- "Full throttle sets the worst case, so TPA fixes it without touching hover feel." Test with
  `sweep tpa_rate` and `sweep tpa_breakpoint`.

Work in a candidate file (`bftune candidate -o A <dir>/tunes/NN/cand.txt`) and put a `# reason` on
every changed line. Make variants with `bftune candidate -o A v2.txt --base cand.txt --set 'key=value # reason'`
(repeat `--set`), never by appending to files with shell redirection. Try alternatives in separate files and compare them with `assess A f1 f2 --with-current`.
Rejected ideas and their numbers go into the worklog. They are part of the answer.

For long explorations (many sweeps, several layouts), you may delegate to the `tuning-engineer`
agent with a precise brief: analysis dir, candidate, the question, the constraints. Keep the
conversation with the pilot and the final decisions here.

### Cover every feature
Betaflight offers much more than PIDs and lowpasses, and the best tune for *this* quad may come from
any of them. Before deciding, run `bftune coverage -o A cand.txt`. It lists every group that shapes
flight behaviour, with the logged and candidate values and whether the model can test it:
- RPM filter
- dynamic and static notches
- gyro and D-term filters and yaw lowpass
- P/I/D
- D-max
- feedforward and its shaping
- RC smoothing
- TPA, including low-throttle TPA
- thrust_linear and output limit
- throttle boost and sag compensation
- dynamic idle
- I-term relax and windup
- anti-gravity
- PID-sum limits
- rates

For **every** group, do one of these:
- **tested `model`, `step` or `idle case`:** run at least one experiment (a `sweep`, `ff`, or
  `suggest` on variant files), even if you then keep the logged value. For example, sweep
  `rpm_filter_q` and `rpm_filter_weights`, `dyn_notch_q/min/count`, `yaw_lowpass_hz`,
  `tpa_breakpoint`, `feedforward_boost` / `smooth_factor` / `averaging` with `--steps`, and
  `rc_smoothing_auto_factor --steps`.
- **tested `none`:** look for evidence in the flight data and the pilot report, then decide:
  - `diagnose`: `throttle_punch_dip` → anti-gravity; `bounce_back` → I-term relax;
    `pidsum_clipping` → limits;
  - `errspec`;
  - what the pilot says about throttle feel and RC link.

  Keep the default or logged value when there is no evidence, and say so.

Record the decision for each group in a coverage table in the worklog: changed with the numbers,
or unchanged with the reason. Rates are the pilot's choice: never change them unasked.

## 4. Decide

Choose using `bftune:loop-shaping` and `bftune:filters-noise`, weighted by what the pilot wants.
Hard rules:
- `bftune emit` must say **PASS**. Exit code 2 means FAIL: never deliver it. If the design margins
  can't be met, deliver the best passing tune and say what limits it.
- The noise budget (≤ 0.9× the proven-safe level) is only raised with evidence: a flown tune with
  cool motors.
- With a **no-chirp model** (`identification.source = freestyle`) the gate is relative: no worse
  than the flown tune. Keep changes small and targeted at diagnosed problems (about ±15 % on P/D,
  filters one step at a time), and always ask for a chirp flight for the next round.
  - Leave feedforward alone unless the pilot complains about stick feel. Its step simulation
    depends on the uncertain plant gain.
  - If the real fix for the main complaint can't pass (e.g. `thrust_linear` or a large P change),
    deliver the partial fix that passes, say plainly what is held back and why, and make the chirp
    flight the headline of the next step.
- A setting you cannot test on the model (anti-gravity, I-term relax cutoff, iterm windup, FF jitter) is
  changed only for a diagnosed reason, and the worklog says so. RC smoothing *is* in the step
  simulation: compare `rc_smoothing_auto_factor` variants with `assess` or `sweep --steps`.
- Prefer robust, explainable choices over the last 0.1 dB.

## 5. Verify, explain, and plan the next flight

Follow `bftune:deliver`:
- `bftune emit` into the tune folder;
- give the pilot the CLI block, an old → new table with the reason for each change, what should
  feel different and what to watch for, the revert block, and what to log next;
- add one short "also checked, left unchanged" list covering the groups you did not change and why
  (from the coverage table), so the pilot sees that nothing was skipped;
- append the iteration to `history.md`.

When the pilot says they pasted it (or sends a new dump), check it with
`bftune applied tune_cli.txt NEW_DUMP --old PREVIOUS_DUMP`. It shows missing lines, the wrong profile,
and settings that changed by accident (e.g. `blackbox_high_resolution` reset to its default).

## Know when to stop
Tune for what the pilot can feel. Rough thresholds for a noticeable difference:
- stick lag: about 1 ms or more;
- hover or idle Ms: about 0.2 or more;
- crossover: about 15 % or more;
- errspec bands: about 3 dB or more;
- motor noise: about 15 % (heat).

Changes below all of these are not worth a flight. Say so, and stop or ask what the pilot wants
next, when:
- the pilot is happy and the data shows nothing to fix;
- the remaining knobs sit at a limit (Ms at 2.0, noise at the budget);
- or the last iteration was already below these thresholds.

When the noise budget is what limits the quad and its motors come down cold, the next real step is a
**noise-headroom flight** (`bftune:filters-noise`), not more fine-tuning. Hardware comes first: with a
known hardware issue (bent arm, damaged prop, a motor stall), fix it before any headroom flight.

## 6. Learn from the flight

When the pilot comes back with their impressions or new logs, continue with `bftune:feedback`.
Every flown tune is new evidence: a new safe-noise reference if the motors stayed cool, and a check of
the model's predictions against reality.

## Things that make you stop and ask
If the pilot can't answer, record the issue prominently and continue only where it's safe. Hardware
findings never block a *conservative* tune, but they do block an aggressive one.
- A gate in `analyze` fails (filter-chain check, closed-loop error, implausible plant for the craft class).
- A `diagnose` problem that tuning cannot fix:
  - a real motor stall (`motor_stall` in `diagnose`; review with `bftune motors`). The `mixer`
    dips in hard flips are normal;
  - motor imbalance above about 30 % (bent prop, bad motor). Below that it is usually CG or trim:
    tell the pilot the direction `diagnose` reports and continue;
  - saturation at part throttle (overweight or weak motors);
  - a mechanical resonance that moves with nothing.
- The firmware is not 2026.6.x (`inspect` warns; `analyze` refuses older releases). See
  `bftune:craft-classes`.
- The pilot's report contradicts the model and you cannot explain why.
