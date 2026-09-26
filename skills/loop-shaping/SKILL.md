---
name: loop-shaping
description: Engineering knowledge for shaping a Betaflight rate loop with bftune - reading margins (PM, GM, Ms, crossover per throttle case), choosing P, I, D, D-max, TPA, thrust_linear, dynamic idle and feedforward, and the judgement settings (RC smoothing, iterm relax, anti-gravity).
user-invocable: false
---

# Loop shaping

## Design targets: derive them from the evidence, within a fixed safety floor
bftune has no table of "correct" limits for a whoop or a 10", or for a racer or a filmer. You set
the targets for **this** quad and pilot from what you know. `bftune targets -o A` shows every
target with its source:
- `from the flown tune`: derived from the data. The performance and tracking bands scale with the
  flown tune's hover crossover, so they fit a whoop and a 10" alike.
- `convention`: a neutral starting point from general control practice or FPV habit (Ms 2.0, PM
  42°, GM 6 dB, FF overshoot 10/15 %, I/P 1.5, D/P 0.4-1.2, noise budget 1.0). **Not a
  measurement.** Confirm it or replace it with a value you can justify.
- `override`: yours, with the reason you gave.

**How to derive them:**
1. **Calibrate against the tunes that flew.** `bftune assess -o A --with-current --with-safe` gives
   the margins of every flown tune on this model, and the pilot has told you how each felt. For
   example: "tune E felt really good, hover Ms 1.9 at 44 Hz" means about 1.9 is acceptable to this
   pilot; "the hot tune wobbled, Ms 3.3 at 46 Hz" means stay well below 3.3. The flown tune's
   D/P, I/P and FF overshoot are the same kind of evidence.
2. **Apply the ranked priorities.** Tighten what the top priority needs and relax what it doesn't:
   - response / latency: allow more FF overshoot;
   - propwash: raise `idle_weight` and keep hover Ms where the pilot felt no wobble;
   - smoothness: lower `ms_max` and the FF overshoot;
   - efficiency: lower `noise_budget`.
   Which knobs actually move stick lag and overshoot on this quad is a model question, not a rule:
   `sweep --steps` over `feedforward_smooth_factor`, `rc_smoothing_auto_factor`, `f_*`, `d_max_*`,
   P and D shows it (on one 5" FF smoothing cut both lag and overshoot, while more P/D and a D-max
   boost *added* lag). Check the measured step response (`analysis.html`) before and after.
3. **Use the physics you measured.** A quad with a long delay (big props, 4 kHz loop, slow ESC)
   can't reach the phase margin of a light 3" at the same crossover. If a convention is
   unreachable without giving up what the pilot wants, change the target, and say so.
4. **Set and record them before the experiments,** each with its evidence:
   ```
   bftune targets -o A --set 'ms_max=2.0 # tune E (Ms 1.9) felt locked-in; the hot tune (3.3) wobbled' \
                       --set 'noise_budget=1.25 # motors cold after hard packs'
   ```
   Copy them into `quad.md` → "Design targets". Every later `assess`/`sweep`/`grid`/`emit` uses them.

**The safety floor is fixed:**
- nominal cases: PM ≥ 30°, GM ≥ 4 dB, Ms ≤ 2.6;
- uncertainty variants: PM ≥ 25°, GM ≥ 3 dB, Ms ≤ 3.2;
- noise ≤ 2× a proven-safe level.

These are loose conventions too, but they are the guardrail no override can cross: a loop beyond
them is lightly damped whatever the pilot wants. A violation there reads "BELOW THE SAFETY FLOOR".
Idle cases are judged relative to the flown tune (below).

**Which knowledge lives where:** the Betaflight-specific knowledge (filter chain, PID/TPA/D-max/FF
math, setting ranges) is in the model that every tool applies. Choosing targets needs only the
evidence you have: the logs, the model's numbers for flown tunes, and the pilot.

## Reading `assess`
- **Cases:** idle, hover (the identification point), mid (50 %), full (100 %). Each is evaluated
  at base D (`/d`) and at D-max (`/dmax`), plus variants: `hiK+delay` (more gain and delay),
  `loK` (less gain), and `dn@min` (dynamic notch at its lowest frequency).
  - With a chirp model the variants are +10 % gain / +0.3 ms and −12 %.
  - With a freestyle model they are +40 % / +0.8 ms and −30 %.
- **Design limits** (the conventional starting values; the ones that apply are shown by `bftune targets`
  and in `brief.json` → `targets`):

  | | nominal | robust variants |
  |---|---|---|
  | PM | ≥ 42° (race 40°, cinematic 50°) | ≥ 35° |
  | GM | ≥ 6 dB | ≥ 4 dB |
  | Ms | ≤ 2.0 | ≤ 2.4 |
  | motor noise | ≤ noise_budget × proven-safe (set from motor temperature, `bftune:filters-noise`) | |

- **Idle limits relative to the flown tune.** At very low rpm the motor lag is extrapolated, and some
  quads (large props, low idle) can't reach the idle design limits at all. When the tune that flew
  already misses them, the idle cases only require "no worse than the flown tune" (`assess` notes
  this). Judge idle by improvement then, and never chase the absolute idle limits with P/I cuts.
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
The `/dmax` cases are evaluated at `d_max` whenever `d_max > d`. In 2026.6 D rises toward `d_max`
through the gyro-driven boost (`d_max_gain`) *and* the setpoint-driven advance
(`d_max_advance`), so `d_max_gain = 0` alone does not keep D at base. Only `d_max = d` does.

Sweep `d_max_<axis>` from D to 1.4 × D; the `/dmax` cases must pass. D-max pays off when base D is
noise-limited well below the damping optimum. If P/D already sit at the Ms limit, extra D raises the
sensitivity peak (the crossover moves into the delay region). Then keep d_max = D.

## High and low throttle
- **TPA:** the plant gain grows with rpm, so full throttle has the least margin. Prefer
  `tpa_mode PD`: on a 5", D-only TPA made full throttle *worse*, because D supplies the phase lead
  there. The full-throttle authority ratio can differ between logs of the same quad (1.3-1.7 seen),
  so keep a few degrees of slack on the full cases. Choose `tpa_rate`
  and `tpa_breakpoint` so that `full/*` no longer sets the worst case. Use `tpa_mode PD` when P also
  needs cutting there. Keep the breakpoint above hover.
- **`thrust_linear`:** raises low-throttle authority (propwash) and lowers high-throttle gain. It
  also raises motor noise at low throttle, so check `noise`. Sweep 0 / 20 / 40.
- **`dyn_idle_min_rpm`** (in hundreds of rpm): about 100-115 % of `flight.natural_idle_rpm_p20`.
  - It holds the motors up when rpm would sag below that floor: in dives, after chops, with prop
    wash unloading. The model's idle case uses max(natural idle, floor), so settings below natural
    idle show no effect there. The real benefit is in the low tail of dives, which the model
    underestimates.
  - Higher gives more authority at zero throttle, but more idle thrust (floaty descents).
  - It needs RPM telemetry.

## Feedforward
`bftune ff -o A cand.txt --axis roll --values 60:180:10`. Pick the lowest stick lag with flick
overshoot ≤ 10 % and snap overshoot ≤ 15 % (race 12/20 %, cinematic 3/8 %). These are targets, not
part of the gate. If the flown tune already overshoots more and the pilot likes it, don't make it worse. On yaw,
`feedforward_yaw_hold_*` also acts.

`feedforward_boost`, `feedforward_smooth_factor` and `feedforward_averaging` are in the step
simulation: sweep them with `--steps` and change them on model evidence (lag and overshoot). The
simulation can't see RC-link jitter, so lower smoothing or averaging raises the risk of a jittery
centre stick. Say so, and name the fallback. `settle_5pct_ms` is secondary: a longer settle from a
small undershoot is fine if lag and overshoot improve.

**Mixed styles** (e.g. "freestyle and some racing"): keep the freestyle margin limits (propwash
matters in both) and use the race limits only for FF overshoot and RC smoothing, where delay is
what the pilot feels. Say so.

`rc_smoothing_auto_factor` (35 freestyle, 25 race, 60 cinematic) is part of the step simulation.
Compare values with `sweep … rc_smoothing_auto_factor 25,35,50 --steps`. Lower means less lag but
more RC jitter, which the model can't see.

## Judgement settings (not modelled; change only with evidence)
Check each against the flight data before leaving it alone:
- **Anti-gravity** (`anti_gravity_gain`, `_cutoff_hz`, `_p_gain`): `diagnose` → `throttle_punch_dip`
  (attitude error while throttle rises fast). Raise the gain in steps of about 20-40 when it is
  flagged. More I also helps.
- **I-term** (`iterm_relax_cutoff`, `iterm_windup`, `iterm_rotation`): a `bounce_back` finding
  calls for a lower relax cutoff. `iterm_rotation` suits pilots who hold long turns or funnels.
- **Throttle feel** (`throttle_boost`, `_cutoff`, `vbat_sag_compensation`): pilot report only.
  Sag compensation (about 70-100) keeps the feel constant through the pack.
- **Feedforward on the RC link** (`feedforward_jitter_factor`, `_max_rate_limit`, `_transition`):
  - raise jitter_factor on low-rate or noisy links;
  - transition 20-40 for a softer centre stick (cinematic).
- **PID-sum limits:** `diagnose` → `pidsum_clipping_*`.
- **Idle controller** (`dyn_idle_p/i/d_gain`, `dyn_idle_max_increase`, `motor_idle`): defaults
  unless the log shows the idle rpm dropping below the floor in dives.

- `iterm_relax_cutoff`: 15 freestyle, 20 race, 10 cinematic. Lower it against bounce-back.
- Always turn `simplified_pids_mode`, `simplified_dterm_filter` and `simplified_gyro_filter` OFF
  when you set explicit values. `emit` does this.
