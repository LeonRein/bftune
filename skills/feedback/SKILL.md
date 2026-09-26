---
name: feedback
description: Continue tuning a quad after a test flight - take the pilot's impressions (propwash, oscillation, motor temperature, feel) and any new blackbox log of the delivered tune, check the model's predictions against reality, and decide the next iteration. Use when the user reports back on a bftune tune they flew, or brings a new log of a quad that has a bftune project folder.
argument-hint: "[quad project folder] [new log]"
allowed-tools: Bash(bftune *), Read, Write, Edit, Glob, Grep
---

# After the flight: learn, then iterate

1. **Reload the context.** Read `quad.md`, `history.md` and the last `tunes/NN-*/` (worklog,
   `tune.json`, report). Know what was predicted and why each change was made.
2. **Collect the pilot's report,** in one message, only for what's missing:
   - motor temperature after a hard pack: cool, warm or hot. **Hot means stop and back off noise
     first, whatever else.**
   - propwash (dives, flips at low throttle), bounce-back after flips and rolls, oscillation on
     punch-outs, stick feel (locked-in, floaty, twitchy, laggy), anything new;
   - better or worse than the previous tune, and whether they changed props, battery or anything else.

   Quote them in `history.md`.
3. **New log of the flown tune?**
   - `bftune diagnose NEW_LOG PREVIOUS_LOG -v` and `bftune errspec NEW_LOG PREVIOUS_LOG` compare the
     flights stratum by stratum. Only large differences (> 3 dB, clearly different findings) count,
     because flying styles differ between flights.
   - `bftune analyze NEW_LOG --dump NEW_DUMP -o <dir>/analysis/NN --safe-log ...`: if the motors
     stayed cool, the flown tune becomes a new proven-safe reference (`--safe-log` for the next
     analysis, or `bftune safe -o A --log NEW_LOG`).
   - If it contains chirps, compare the identified plant with the previous one. Gains should agree
     within about 10 % after the thrust_linear correction. A larger change means something changed
     on the quad (props, motors, battery, weight): ask.
4. **Check the predictions.** For each change, compare what the worklog predicted with what the
   pilot and the data say (`bftune:evidence`):
   - They agree: the model is calibrated for this quad. You can move further in the same direction if
     the pilot wants more.
   - They disagree: find out why before changing anything else. Possible causes: a nonlinear effect
     (saturation, airmode, I-term), a metric that misses what the pilot feels, a changed quad, or a
     wrong noise or heat assumption. Note the lesson in `quad.md` under constraints, e.g. "Ms above
     2.4 at 40-50 Hz is felt as propwash on this quad".
5. **Decide the next step** with the `bftune:tune` loop (hypothesize → experiment → decide → verify).
   Change fewer things per iteration when the pilot is fine-tuning feel, and more when there is a clear
   diagnosed problem.
6. **Deliver** per `bftune:deliver` and append the iteration to `history.md`.

If the pilot is happy and the data shows nothing to fix, say so. The best next step can be none,
or a chirp flight that makes the model precise for later.
