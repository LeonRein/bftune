---
name: bf-pid-optimize
description: Run and interpret bftune's robust PID/filter/TPA optimization - goals per flying style, operating cases (idle/hover/mid/full, battery, delay, D-max), margins (PM/GM/Ms/delay margin), feedforward rules, and when to override. Use when producing or reviewing a tune.
---

# Optimization — what it does and how to judge it

**Objective:** maximize disturbance rejection (mean log|S| from 3–60 Hz for freestyle, 5–80 Hz for
race) at hover, mid throttle and idle (propwash). Stick tracking is handled by feedforward.

**Constraints (every case):**
| | nominal | uncertainty variants |
|---|---|---|
| phase margin | ≥ 45° (race 42°) | ≥ 35° |
| gain margin | ≥ 6 dB | ≥ 4 dB |
| peak sensitivity Ms | ≤ 2.0 (race 2.1) | ≤ 2.4 |
| delay margin | ≥ 1 ms | — |
| motor noise | ≤ budget (`bf-filters`) | |

Cases: idle (natural idle or dyn_idle floor), hover (identification point), 50 %, 100 % throttle;
base D and D at d_max; K ±10 %, +0.3 ms delay, and the dynamic notch at its minimum frequency.

**Structure:** coordinate descent over global settings (TPA, thrust_linear, gyro/D-term LPFs,
dyn notch, RPM Q) from several seeds. For each candidate, P/D/d_max per axis come from differential
evolution; I follows P (`i_over_p`: freestyle 1.5, race 1.7), because I only matters for
attitude hold, which the linear model scores poorly.

**Rules applied afterwards (`optimize/rules.py`):**
- F per axis: least tracking lag on 300°/s-in-50 ms and 600°/s-in-30 ms stick moves with overshoot
  ≤ 7 % (freestyle) / 12 % (race). Physics reference: F_ideal = 1/(K·0.00013754).
- rc_smoothing_auto_factor 35 (freestyle), 25 (race); dyn_idle floor at ~90 % of natural idle rpm;
  iterm_relax_cutoff 15/20.

**Review checklist**
- `evaluation.*.worst` meets the limits; if not, say so and prefer the safer candidate.
- Full-throttle PM — high-throttle oscillation is the most common failure of aggressive tunes.
- D/P ratio vs. current and craft priors (large jumps deserve a sentence of justification).
- `noise_vs_budget` ≤ 1.0.
- Compare against other tunes the pilot flew (`bftune evaluate -o out other.txt`). The pilot's
  feedback (propwash, bounce-back) should agree with the model's idle/hover metrics.
