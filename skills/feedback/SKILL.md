---
name: feedback
description: Continue tuning a quad after a test flight - take the pilot's impressions (propwash, oscillation, motor temperature, feel) and any new blackbox log of the delivered tune, check the model's predictions against reality, and decide the next iteration. Use when the user reports back on a bftune tune they flew, or brings a new log of a quad that has a bftune project folder.
argument-hint: "[quad project folder] [new log]"
allowed-tools: Bash(bftune *), Read, Write, Edit, Glob, Grep
---

# After the flight: learn, then iterate

1. **Reload the context.** Read `quad.md`, `history.md` and the last `tunes/NN-*/` (worklog,
   `tune.json`, report). Know what was predicted and why each change was made.
2. **Collect the pilot's report right away,** in your first reply and in one message. Ask only
   for what's missing, and don't run heavy analysis before asking:
   - motor temperature after a hard pack: cold, cool, slightly warm, warm or hot (the same scale as
     before, so the calibration table stays comparable). **Hot means: find the cause first**
     (`diagnose`, `motors`, the spectrogram; `bftune:filters-noise`), and back off noise before
     anything else.
   - did it get better on the priorities in `quad.md`? What stood out: stick feel, propwash,
     punch-outs, bounce-back, anything new?
   - **what the next iteration should prioritise** (the same ranking as before, or a new one), or
     whether they are happy to stop;
   - whether they changed props, battery or anything else, and a fresh `diff all` if they changed
     settings themselves.

   Quote them in `history.md` and update the priorities in `quad.md`.
3. **New log of the flown tune?** First run `bftune tunes NEW_LOG [OLDER_LOGS] --dump NEW_DUMP` to make sure the log really
   flew the delivered tune (pilots tweak things between flights).
   - `bftune logs PREVIOUS_LOG NEW_LOG -o <dir>/tunes/NN-date/compare` compares the flights in one
     report (`logs.html`): settings that differ, findings, measured step response (stick lag and
     overshoot as flown), tracking-error spectrum, spectrograms and how hard each flight was flown.
     Open the PNGs and look. A difference counts only if it is larger than the spread between logs
     of the *same* tune (fly two packs, or compare the measured step's confidence) and the flights
     were flown alike ("how hard it was flown"); `bftune errspec --by-throttle` splits by throttle
     when they differ. Crashes: `--exclude`.
   - The measured step of the new log is the direct check of the as-flown prediction in the worklog
     (`analyze` puts both in `analysis.html` and `brief`).
   - `bftune analyze NEW_LOG --dump NEW_DUMP -o <dir>/analysis/NN --safe-log ...`: if the motors
     stayed cold, cool or slightly warm, the flown tune becomes a new noise reference (`--safe-log`
     for the next analysis, or `bftune safe -o A --log NEW_LOG`). Add a row to the noise-vs-temperature
     table in `quad.md` either way.
   - If it contains chirps, compare the identified plant with the previous one after the
     thrust_linear correction. The gain spread between chirp rounds (`uncertainty.source`) is the
     yardstick: a change well beyond it means something changed on the quad (props, motors, battery,
     weight): ask.
4. **Check the predictions.** For each change, compare what the worklog predicted with what the
   pilot and the data say (`bftune:evidence`):
   - They agree: the model is calibrated for this quad. You can move further in the same direction if
     the pilot wants more.
   - They disagree: find out why before changing anything else. Possible causes: a nonlinear effect
     (saturation, airmode, I-term), a metric that misses what the pilot feels, a changed quad, or a
     wrong noise or heat assumption. Note the lesson in `quad.md` (calibration), e.g. "Ms above
     2.4 at 40-50 Hz is felt as propwash on this quad", and whether the pilot noticed each change:
     that is their perceptibility threshold for the stop rule.
   **No new log** (the pilot is happy and wants more, or reports only feel): work from the last
   analysis (re-run `analyze` on the last log if `brief` says STALE ANALYSIS) and the pilot's words.
   If the tune sits at the noise budget with cold motors, the next step is the noise-headroom flight
   (`bftune:filters-noise`); otherwise the `bftune:tune` loop on the last model. Ask for a log of the
   next flight either way: it is the check of whatever you deliver.
5. **Decide the next step** with the `bftune:tune` loop (hypothesize → experiment → decide → verify).
   Change fewer things per iteration when the pilot is fine-tuning feel, and more when there is a clear
   diagnosed problem.
6. **Deliver** per `bftune:deliver` and append the iteration to `history.md`.

Apply the stop rule from `bftune:tune` ("Know when to stop"): changes below the thresholds the
pilot can feel are not worth another flight.

If the pilot asks about motor or hardware health, run `bftune motors` over all logs. It separates
real stalls from mixer dips and crashes, and compares the motors with each other.

If the pilot is happy and the data shows nothing to fix, say so. The best next step can be none,
or a chirp flight that makes the model precise for later.
