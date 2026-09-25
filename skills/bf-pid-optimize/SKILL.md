---
name: bf-pid-optimize
description: Run and interpret bftune's robust PID/filter/TPA optimization - goals per flying style, operating cases (idle/hover/mid/full, battery, delay, D-max), margins (PM/GM/Ms/delay margin), feedforward rules, and when to override. Use when producing or reviewing a tune.
---

# Optimization — what it does and how to judge it

**Objective:** maximize disturbance rejection (mean log|S| from 3–60 Hz for freestyle, 5–80 Hz for
race) at hover, mid throttle and idle (propwash), plus 0.5× the feedback-only setpoint-tracking
error from 1–15 Hz (attitude hold; it stops D-only loops from scoring well). P and D are bounded to
0.5–2.5× the logged tune, and D/P (Betaflight units) is kept within 0.4–1.2 on roll/pitch.

**Constraints (every case):**
| | nominal | uncertainty variants |
|---|---|---|
| phase margin | ≥ 45° (race 42°) | ≥ 35° |
| gain margin | ≥ 6 dB | ≥ 4 dB |
| peak sensitivity Ms | ≤ 2.0 (race 2.1) | ≤ 2.4 |
| delay margin | ≥ 1 ms | — |
| motor noise | ≤ 0.9 × proven-safe level (`--noise-budget`) | |

Cases: idle (natural idle or dyn_idle floor), hover (identification point), 50 %, 100 % throttle;
base D and D at d_max; gain +10 % with +0.3 ms delay and −12 % (battery, motor wear), and the dynamic
notch at its minimum frequency. Internally the optimizer keeps 1.5° / 0.3 dB / 0.04 of safety margin
so that integer rounding never breaks a limit.

**Structure:** coordinate descent over global settings (TPA, thrust_linear, gyro/D-term LPFs,
dyn notch, RPM Q) from several seeds. For each candidate, P/D/d_max per axis come from differential
evolution; I follows P (`i_over_p`: freestyle 1.5, race 1.7), because I only matters for
attitude hold, which the linear model scores poorly.

**Rules applied afterwards (`optimize/rules.py`):**
- F per axis: least tracking lag on a 300°/s-in-50 ms and a fast (≤ 60 % of max rate, 30 ms) stick move,
  with overshoot ≤ 10 % (freestyle) / 12 % (race) / 3 % (cinematic).
  F_ideal = 1/(K·0.00013754) is the FF that *alone* would produce the requested acceleration.
  With P already tracking, the best F is usually below F_ideal; it can be above when the loop is slow.
- rc_smoothing_auto_factor 35 (freestyle), 25 (race); dyn_idle floor at ~90 % of natural idle rpm;
  iterm_relax_cutoff 15/20.

**Reading the verdict for existing tunes.** A tune that has flown well can still FAIL the
constraints: the limits are deliberately conservative, and the robust variants add +10 % gain and
+0.3 ms delay. Treat the verdict as "margin below the design target", not "will oscillate". Compare
tunes on the same model. If the model's ranking contradicts the pilot (e.g. predicts better propwash
for a tune that felt worse), say so and weight the pilot's report.

**Review checklist**
- `evaluation.*.worst` meets the limits; if not, say so and prefer the safer candidate.
- Full-throttle PM — high-throttle oscillation is the most common failure of aggressive tunes.
- D/P ratio vs. current and craft priors (large jumps deserve a sentence of justification).
- `noise_vs_budget` ≤ 1.0.
- Compare against other tunes the pilot flew (`bftune evaluate -o out other.txt`). The pilot's
  feedback (propwash, bounce-back) should agree with the model's idle/hover metrics.
