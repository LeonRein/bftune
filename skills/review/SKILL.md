---
name: review
description: Review a Betaflight tune before it is flown - a CLI diff, preset, or someone else's suggested PID/filter values - against the quad's blackbox log - margins at idle/hover/full throttle, noise and motor-heat risk, feedforward overshoot, range validity and firmware pitfalls. Use when the user asks whether a tune is safe, why a tune behaves badly, or to compare several tunes.
argument-hint: "[tune/diff file] [log]"
allowed-tools: Bash(bftune *), Read, Write, Edit, Glob, Grep
---

# Review a proposed tune

0. If anything essential is missing, ask first, in one message: a log of this quad, a fresh
   `diff all`, and what the pilot wants from the tune (priorities) and how warm the motors run.
1. You need an analysis of a log of **this** quad. Use the one in the project folder, or run
   `bftune analyze` (see `bftune:toolbox`). Without any log, you can only check ranges and firmware
   pitfalls: say so.
2. Turn the proposal into a candidate:
   - `bftune candidate -o A cand.txt` writes the logged tune;
   - apply the proposal's `set` lines (paste them in; later lines override earlier ones);
   - `profile N` lines and comments are fine.
3. `bftune assess -o A cand.txt --with-current --with-safe`, and `bftune noise -o A cand.txt`.
4. Report, per axis:
   - **stability**: worst PM/GM/Ms and which case sets it (idle, hover, full throttle, gain or delay
     variants);
   - **feel**: hover crossover and Ms frequency (propwash), idle crossover (dives), stick lag and
     overshoot;
   - **heat**: motor noise vs the proven-safe level;
   - **validity**: range problems.

   Compare with the current tune in plain words: "more responsive at hover, but full-throttle PM
   drops from 44° to 31°, close to the safety floor: less damping on punch-outs".
5. Check the pitfalls:
   - `simplified_*` sliders left ON, which overwrite explicit values the next time the Configurator
     saves;
   - `d_max < d`;
   - RPM filter on without bidirectional DShot;
   - dynamic notch removed while `bftune noise` shows persistent non-RPM peaks;
   - gyro filtering at 1-3 kHz weaker than any flown tune;
   - TPA breakpoint below hover;
   - FF so high that the as-flown step peak (`assess`, `ff`) is well above what the pilot flew with
     (`peak_max`).
6. If it FAILs, propose the smallest change that passes (`sweep` the offending knob) and explain why.

With the `bftune:evidence` rules, a FAIL on the robust variants of a tune that already flew well
means "less margin than the design target", not "will oscillate". Say it that way.
