---
name: review
description: Review a Betaflight tune before it is flown - a CLI diff, preset, or someone else's suggested PID/filter values - against the quad's blackbox log - margins at idle/hover/full throttle, noise and motor-heat risk, feedforward overshoot, range validity and firmware pitfalls. Use when the user asks whether a tune is safe, why a tune behaves badly, or to compare several tunes.
argument-hint: "[tune/diff file] [log]"
allowed-tools: Bash(bftune *), Read, Write, Edit, Glob, Grep
---

# Review a proposed tune

0. If anything essential is missing, ask first, in one message: a log of this quad, a fresh
   `diff all`, what the pilot wants from the tune (priorities) and how warm the motors run (the
   5-step scale: cold, cool, slightly warm, warm, hot). When the logs hold several tunes
   (`bftune tunes`), the `diff all` is also what tells you which one is on the quad: ask for it
   even if the pilot "doesn't know"; it takes them a minute.
1. You need an analysis of a log of **this** quad. Use the one in the quad's project folder, or
   create the folder (`bftune project init`) and run `bftune analyze` into its `analysis/` (see
   `bftune:toolbox`). Also run `bftune diagnose` on the log: findings are independent evidence
   (e.g. a punch-out dip backs up a low full-throttle margin). Without any log, you can only check
   ranges and firmware pitfalls: say so.
2. **Set the targets from what the pilot told you** (`bftune:loop-shaping` → "Design targets"),
   at least the noise budget from the motor temperature (`bftune:filters-noise`):
   `bftune targets -o A --set 'noise_budget=… # motors cool on the current tune'`. Without it the
   temperature answer does nothing.
3. Turn the proposal into a candidate: `bftune candidate -o A cand.txt --apply proposal.txt` (its
   `set` lines on top of the tune on the quad; later lines win; add `--set` for your own variants).
   To judge **tunes that flew** (several logs, several tunes), take each from its log:
   `bftune candidate -o A tune_B.txt --from-log LOG_B`, and assess them all on the best model
   (the cleanest chirp log).
4. `bftune assess -o A cand.txt --with-current --with-safe`, and `bftune noise -o A cand.txt`.
5. Report, per axis:
   - **stability**: worst PM/GM/Ms and which case sets it (idle, hover, full throttle, gain or delay
     variants);
   - **feel**: hover crossover and Ms frequency (propwash), idle crossover (dives), the as-flown
     step (50 % time, peak, dip) against the flown tune's;
   - **heat**: motor noise at the motors vs the reference;
   - **validity**: range problems, and filtering at 1-3 kHz lighter than anything flown (the noise
     prediction extrapolates there, so the real number may be worse).

   Compare with the current tune in plain words: "more responsive at hover, but full-throttle PM
   drops from 44° to 31°, close to the safety floor: less damping on punch-outs".
6. Check the pitfalls (`assess` notes catch most):
   - `simplified_*` sliders left ON while the proposal sets explicit values: the Configurator
     recomputes them when it saves;
   - `d_max < d`;
   - RPM filter on without bidirectional DShot;
   - dynamic notch removed while `bftune noise` or the spectrogram shows persistent non-RPM peaks;
   - gyro filtering at 1-3 kHz weaker than any flown tune;
   - TPA breakpoint below hover;
   - FF so high that the as-flown peak is well above what the pilot flew with (`peak_max`).
7. **Decide:**
   - It passes: say what will feel different and what to watch on the first flight.
   - It fails on something one knob fixes: propose the smallest change that passes (`sweep` the
     offending knob) and explain why.
   - It is incompatible with this quad (far over the noise budget, or its gains only work with its
     own filters or plant): say plainly not to fly it, and why. Gains are coupled to the filters and
     the plant of the quad they came from: moving PIDs without the filters, or filters without the
     PIDs, usually makes it worse (check it with the model before saying so). Offer a tune built for
     this quad (`bftune:tune`), carrying over what the proposal gets right.

**Tunes that already flew.** With the `bftune:evidence` rules, a FAIL on the robust variants of a
tune that flew well means "less margin than the design target", not "will oscillate". If the
pilot's own flown tune misses a target, even below the safety floor on a nominal case (e.g. the
full-throttle PM), say it as "less damping there than the design target; it flies, and the log
shows [finding or none]". A proposal that improves that case but still misses the target can be
judged against an evidence-based target (`bftune targets --set` with the flown value and the log
as the reason); the safety floor still binds anything you recommend.
