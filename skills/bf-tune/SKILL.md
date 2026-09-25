---
name: bf-tune
description: Step-by-step engineering procedure to tune a Betaflight quad (PIDs, D-max, feedforward, filters, TPA, thrust_linear, dynamic idle) from a blackbox chirp log and CLI dump with the bftune workbench - any size from tinywhoop to 10 inch. Use whenever someone wants a tune, a log analysed, or a proposed tune checked.
---

# Tuning procedure (bftune workbench)

The scripts do the numerics: decoding, plant identification, the alias-aware noise model, margins,
and step simulation. **You make the decisions** by iterating *hypothesis → edit candidate →
`assess`/`sweep` → adjust*. Each call answers in about a second. Whatever you deliver must pass the
deterministic verdict gate of `bftune emit`.

Keep a short worklog (`OUT/work/worklog.md`): each decision, the numbers that justified it, and
what you rejected. Put a reason comment on every changed line of the candidate file, e.g.
`set d_roll = 34   # limited by noise at 35 % throttle (0.88x safe)`.

## 0. Intake
- Blackbox log(s) with chirps on roll/pitch/yaw and the CLI `dump`. If there are no chirps, stop and
  send `bf-flight-protocol`.
- Ask for: flying style (freestyle/race/cinematic), craft class and props, **how warm the motors got**,
  and other logs or diffs of the *same* quad with the pilot's impressions (these become proven-safe
  noise references, and they are the best calibration of the model against reality).

## 1. Analyze once (about 10-20 s)
```bash
bftune inspect LOG --dump DUMP
bftune analyze LOG --dump DUMP -o OUT [--safe-log OTHER.BFL] [--safe-cli other_diff.txt]
bftune candidate -o OUT OUT/work/cand.txt        # editable file = the logged tune
```
Read `OUT/analysis.json` and `plant_bode.png`, and apply the `bf-sysid` gates (chain checks,
closed-loop error, replay, priors). **If a gate fails, stop.**

## 2. Baseline picture
```bash
bftune assess -o OUT --with-current --with-safe
```
- For the current and safe tunes, note hover/idle crossover, Ms, full-throttle PM, the **worst
  case and its label**, noise vs the safe level, and stick-flick lag and overshoot.
- Write down what limits each tune (e.g. "full/d/hiK+delay PM 23°: TPA cuts D at full throttle").
- **Cross-check with the pilot.** Does the model explain what they felt? High hover Ms means
  wobble and propwash; low idle crossover means propwash; low full-throttle PM means oscillation on
  punch-outs.
  - If the model and the pilot disagree, find out why before you continue: a different metric,
    missing data, or a model limitation.
  - Record the conclusion in the worklog.

## 3. Filters (`bf-filters`)
```bash
bftune noise -o OUT                               # bands, non-RPM peaks (persistent?), safe levels
bftune sweep -o OUT cand.txt gyro_lpf2_static_hz 300,400,500,650,800
bftune sweep -o OUT cand.txt dterm_lpf2_static_hz 90:180:15
```
1. **RPM filter first.** Use all harmonics at weight 100 when they are visible in gyroUnfilt, and Q 500-800.
2. **Dynamic notch.** Keep one only if `noise` shows **persistent** non-RPM peaks, or the unfiltered
   spectrum has a stationary line. Otherwise set `dyn_notch_count = 0`, which saves phase at crossover.
3. **Gyro LPF.** Use the least filtering whose motor noise stays ≤ the budget at every band. On a
   clean build that is usually LPF1 off plus one PT1 LPF2 at 1.5-2.5× the hover motor frequency.
   Don't go weaker at 1-3 kHz than a tune that has actually flown: `assess` prints a note when you do.
4. **D-term LPF.** It trades D phase against noise. `sweep` shows noise and hover PM/Ms side by
   side. PT2/PT3 at a lower cutoff can beat PT1s.

## 4. High and low throttle
```bash
bftune sweep -o OUT cand.txt tpa_rate 0,20,35,50,65
bftune sweep -o OUT cand.txt tpa_breakpoint 1300,1400,1500
bftune sweep -o OUT cand.txt thrust_linear 0,20,40
```
- **TPA:** choose it so that the full-throttle cases no longer set the worst PM/Ms. Use `tpa_mode PD`
  when P also needs reducing at full throttle.
- **thrust_linear:** raises idle authority (propwash) but also raises motor noise at low throttle.
  Check the idle crossover and noise.

## 5. P, I, D per axis
```bash
bftune suggest -o OUT cand.txt                    # a proposal per axis, all else fixed
bftune sweep -o OUT cand.txt d_roll 20:50:5
bftune sweep -o OUT cand.txt p_roll 30:60:5
```
- Start from `suggest`, then use sweeps to understand the local tradeoff. Choose values where:
  - hover Ms ≤ 2.0 (race ≤ 2.1),
  - worst-case PM/GM/Ms pass,
  - noise ≤ budget with some slack,
  - the objective improves.
- Keep D/P between 0.5 and 1.0 unless the plant clearly needs otherwise.
- Keep I ≈ 1.5×P (freestyle) or 1.7×P (race). I is for attitude hold, which the linear model scores poorly.
- Round to integers and re-`assess`.

## 6. D-max
`d_max_<axis>` ≈ 1.2-1.4× D gives more damping during stick moves. d_max is only active during
moves (`d_max_advance`), so noise is judged at base D. The d_max cases must pass as well.

## 7. Feedforward
```bash
bftune ff -o OUT cand.txt --axis roll --values 60:180:10
```
Pick the lowest lag with flick overshoot ≤ 10 % and snap overshoot ≤ 15 % (race 12/20 %,
cinematic 3/8 %). For yaw, remember `feedforward_yaw_hold_*` also acts.

## 8. Judgement settings
- `rc_smoothing_auto_factor`: 35 freestyle, 25 race, 60 cinematic (latency vs RC jitter).
- `dyn_idle_min_rpm`: about 90 % of the natural idle rpm (`analysis.json`), to keep authority in dives.
- `iterm_relax_cutoff`: 15 freestyle, 20 race, 10 cinematic.
- Leave anti-gravity and FF smoothing/jitter at their defaults unless the log shows a problem.

## 9. Verify and deliver
```bash
bftune assess -o OUT cand.txt --with-current --with-safe    # final comparison
bftune emit   -o OUT cand.txt                              # exit code 2 = FAIL: do not deliver
```
- Optionally, `bftune optimize -o OUT_COPY` is an automatic second opinion (10-60 min). Compare it
  with your tune; if it is clearly better, understand why before adopting anything.
- Deliver per `bf-cli-output`: the CLI, a table of old → new with reasons, predicted changes, the
  revert block, first-flight checks, and the next log to record.

## Limits to state honestly
- Crossovers near the end of the coherent band rely on the model (`assess` notes this).
- Noise above the log Nyquist is inferred.
- The model is linear around each operating point: saturation, airmode and anti-gravity are
  not modelled.
