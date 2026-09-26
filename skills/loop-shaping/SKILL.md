---
name: loop-shaping
description: Engineering knowledge for shaping a Betaflight rate loop with bftune - reading margins (PM, GM, Ms, crossover per throttle case), choosing P, I, D, D-max, TPA, thrust_linear, dynamic idle and feedforward, and the judgement settings (RC smoothing, iterm relax, anti-gravity).
user-invocable: false
---

# Loop shaping

## Reading `assess`
- **Cases:** idle, hover (the identification point), mid (50 %), full (100 %). Each is evaluated
  at base D (`/d`) and at D-max (`/dmax`), plus variants: `hiK+delay` (more gain and delay),
  `loK` (less gain), and `dn@min` (dynamic notch at its lowest frequency).
  - With a chirp model the variants are +10 % gain / +0.3 ms and −12 %.
  - With a freestyle model they are +40 % / +0.8 ms and −30 %.
- **Design limits** (freestyle; race is slightly looser; see `brief.json` → `goals`):

  | | nominal | robust variants |
  |---|---|---|
  | PM | ≥ 45° | ≥ 35° |
  | GM | ≥ 6 dB | ≥ 4 dB |
  | Ms | ≤ 2.0 | ≤ 2.4 |
  | motor noise | ≤ 0.9 × proven-safe | |

- **The worst-case label points to the knob:**
  - `full/…` → TPA;
  - `idle/…` → D, thrust_linear, dyn idle;
  - `hover/…` → P/D balance and filter lag;
  - `…/dmax` → d_max;
  - `dn@min` → `dyn_notch_min_hz`.
- `obj` (lower is better) is the mean dB |S| over 3-60 Hz at idle, hover and mid, plus a tracking
  term. Compare it only between candidates on the same analysis.
- `step` gives the stick→gyro lag and overshoot of a flick and a snap move, with FF, RC smoothing
  and the full controller.

## P and D (per axis)
- `bftune suggest -o A cand.txt [--axis roll]` proposes P/I/D/d_max with everything else fixed.
  It is a starting point, not a decision. Understand the local tradeoff with `sweep p_roll …` and
  `sweep d_roll …`.
- **What to aim for:**
  - hover Ms ≤ 2.0 (race ≤ 2.1) and a flat |S| over 30-60 Hz, which is what the pilot feels as
    propwash;
  - the highest hover and idle crossover that respects that;
  - noise below the budget with some slack.
- D/P (Betaflight units) usually sits between 0.5 and 1.0 on roll/pitch. Leaving that range needs a reason.
- Roll and pitch differ because of inertia (pitch is usually heavier). Don't force them equal.
- Yaw has D = 0 as a rule. Its crossover is limited by the reaction-torque plant and by noise via
  `yaw_lowpass_hz`.
- **I:** about 1.5 × P (freestyle) or 1.7 × P (race). The linear model scores I poorly: it matters
  for attitude hold through flips and wind. Raise it for drift, lower it for bounce-back only
  together with `iterm_relax`.

## D-max
Sweep `d_max_<axis>` from D to 1.4 × D; the `/dmax` cases must pass. D-max pays off when base D is
noise-limited well below the damping optimum. If P/D already sit at the Ms limit, extra D raises the
sensitivity peak (the crossover moves into the delay region). Then keep d_max = D.

## High and low throttle
- **TPA:** the plant gain grows with rpm, so full throttle has the least margin. Choose `tpa_rate`
  and `tpa_breakpoint` so that `full/*` no longer sets the worst case. Use `tpa_mode PD` when P also
  needs cutting there. Keep the breakpoint above hover.
- **`thrust_linear`:** raises low-throttle authority (propwash) and lowers high-throttle gain. It
  also raises motor noise at low throttle, so check `noise`. Sweep 0 / 20 / 40.
- **`dyn_idle_min_rpm`** (in hundreds of rpm): about 90-110 % of `flight.natural_idle_rpm_p20`.
  Higher gives more authority at zero throttle but more idle thrust (floaty descents). It needs
  RPM telemetry.

## Feedforward
`bftune ff -o A cand.txt --axis roll --values 60:180:10`. Pick the lowest stick lag with flick
overshoot ≤ 10 % and snap overshoot ≤ 15 % (race 12/20 %, cinematic 3/8 %). On yaw,
`feedforward_yaw_hold_*` also acts.

## Judgement settings (not modelled; change only with a reason)
- `rc_smoothing_auto_factor`: 35 freestyle, 25 race, 60 cinematic (latency vs RC jitter).
- `iterm_relax_cutoff`: 15 freestyle, 20 race, 10 cinematic. Lower it against bounce-back.
- `anti_gravity_gain`, FF jitter/smoothing/averaging: defaults unless the log shows a problem.
- Always turn `simplified_pids_mode`, `simplified_dterm_filter` and `simplified_gyro_filter` OFF
  when you set explicit values. `emit` does this.
