---
name: loop-shaping
description: Engineering knowledge for shaping a Betaflight rate loop with bftune - reading margins (PM, GM, Ms, crossover per throttle case), choosing P, I, D, D-max, TPA, thrust_linear, dynamic idle and feedforward, and the judgement settings (RC smoothing, iterm relax, anti-gravity).
user-invocable: false
---

# Loop shaping

**Who knows what.** This skill holds what needs Betaflight source knowledge or control theory: what
each setting does in the firmware and how to read the model. What is *good* for this quad and pilot
comes from your evidence: the logs, the model's numbers for the tunes that flew, and the pilot.
Numbers below marked *(convention)* or *(seen on …)* are starting points or single observations,
never rules. Replace them with this quad's evidence.

## Design targets: derive them from the evidence, within a fixed safety floor
`bftune targets -o A` shows every target with its source:
- `from the flown tune` / `from the log` / `chirp rounds`: derived from this quad's data. The
  performance and tracking bands scale with the flown tune's hover crossover; the "mid" design case
  sits at the throttle the pilot actually uses (p90); the robust variants come from the gain spread
  between chirp rounds (never narrower than ±10 %); `peak_max` is the flown tune's as-flown step peak.
- `convention`: a neutral starting point from control practice or FPV habit. **Not a
  measurement.** Confirm it or replace it with a value you can justify.
- `override`: yours, with the reason you gave.

**How to derive them:**
1. **Calibrate against the tunes that flew.** `bftune assess -o A --with-current --with-safe` gives
   every flown tune's margins, noise and as-flown step on this model, and the pilot told you how
   each felt. "Tune E felt locked-in at hover Ms 1.9" makes about 1.9 acceptable to this pilot;
   "the hot tune wobbled at Ms 3.3" means stay well below. The flown tunes' D/P, I/P, as-flown peak
   and noise are the same kind of evidence. Record them in `quad.md` so the next session starts
   from them.
2. **Apply the ranked priorities.** Tighten what the top priority needs and relax what it doesn't:
   - stick response / latency: allow a higher `peak_max` (more FF), accept a smaller Ms margin;
   - locked-in: more tracking weight and I (`tracking_weight`, `i_over_p`), hover Ms where the pilot
     felt it hold;
   - propwash: raise `idle_weight`, keep hover/idle Ms where the pilot felt no wobble;
   - smoothness: lower `ms_max` and `peak_max`;
   - clean punch-outs: margin on the `full/*` and `mid/*` cases (TPA), anti-gravity;
   - efficiency / cool motors: lower `noise_budget`.

   Pilots also name things outside this list; translate them into the model's terms and say how.
   Which knobs move stick lag and overshoot on *this* quad is a model question: `sweep --steps` over
   `feedforward_smooth_factor`, `rc_smoothing_auto_factor`, `f_*`, `d_max_*`, P and D shows it (seen
   on one 5": FF smoothing cut both lag and overshoot, while more P/D and a D-max boost *added* lag).
3. **Use the physics you measured.** A quad with a long delay (big props, 4 kHz loop, slow ESC)
   can't reach the phase margin of a light 3" at the same crossover. If a convention is unreachable
   without giving up what the pilot wants, change the target and say so.
4. **Set and record them before the experiments,** each with its evidence:
   ```
   bftune targets -o A --set 'ms_max=2.0 # tune E (Ms 1.9) felt locked-in; the hot tune (3.3) wobbled' \
                       --set 'noise_budget=1.25 # motors cold after hard packs'
   ```
   Copy them into `quad.md` → "Design targets". Every later `assess`/`sweep`/`grid`/`emit` uses them.

**The safety floor is fixed:** nominal PM ≥ 30°, GM ≥ 4 dB, Ms ≤ 2.6; robust variants PM ≥ 25°,
GM ≥ 3 dB, Ms ≤ 3.2; delay margin ≥ 0.5 ms; noise ≤ 2× a flown level. These are loose control
conventions, but no override can cross them: a loop beyond them is lightly damped whatever the pilot
wants. A violation there reads "BELOW THE SAFETY FLOOR". The robust variants can be widened
(`gain_uncertainty`, `delay_uncertainty_ms`), never narrowed below the chirp rounds' spread.

## Reading `assess`
- **Cases:** idle, hover (the identification point), mid (`mid_throttle`), full (100 %), each at
  base D (`/d`) and at D-max (`/dmax`), plus robust variants `hiK+delay`, `loK` (sizes from
  `targets`) and `dn@min` (dynamic notch at its lowest frequency).
- **Idle limits relative to the flown tune.** At very low rpm the motor lag is extrapolated, and some
  quads (large props, low idle) can't reach the idle design limits at all. When the tune that flew
  already misses them, the idle cases only require "no worse than the flown tune" (`assess` notes
  this). Judge idle by improvement, and never chase the absolute idle limits with P/I cuts.
- **The worst-case label points to the knob:** `full/…` → TPA; `idle/…` → D, thrust_linear, dyn idle;
  `hover/…` → P/D balance and filter lag; `…/dmax` → d_max; `dn@min` → `dyn_notch_min_hz`.
- `obj` (lower is better) is the mean dB |S| over `perf_band` at idle, hover and mid, plus a tracking
  term. Compare it only between candidates on the same analysis.
- **Stick response:** `as flown` is the model replaying this pilot's logged stick inputs, turned into
  a step by the same estimator as the log: the most realistic lag and overshoot prediction, and for
  the flown tune the direct check against the measurement (`bftune:evidence`). The `flick`/`snap`
  lines use a typical and a fast stick move measured in the log (ramps; they understate overshoot
  because real moves start more sharply).

## P and D (per axis)
- `bftune suggest -o A cand.txt [--axis roll]` proposes P/I/D/d_max with everything else fixed. It
  optimises disturbance rejection, not stick lag, and sets I from `i_over_p`. It is a starting point,
  not a decision: understand the local tradeoff with `sweep`/`grid`.
- **What to aim for:** hover Ms within `ms_max` and a flat |S| around this quad's Ms frequency (the
  propwash band: `assess` `ms_hz`, the `errspec` bump and resonance findings show where it is on
  this quad); the highest hover and idle crossover that respects that; noise below the budget with
  slack the pilot's temperature report allows.
- **D/P and I/P:** the flown tunes' ratios are the reference; the `d_over_p` window is a guardrail
  against degenerate solutions, not an optimum. Leaving the flown range needs a model reason (the
  sweep) and a word to the pilot. Roll and pitch differ with inertia (identified K): don't force
  them equal.
- **Yaw:** usually D = 0 because the reaction-torque plant needs little damping and D adds noise; if
  yaw is the limiting axis, test D with the model like any other knob. Its crossover is limited by
  the plant and by noise via `yaw_lowpass_hz`.
- **I:** the linear model scores I poorly: it matters for attitude hold through flips, wind and
  turns. Start from the flown I/P; raise it when `errspec` shows tracking error in the low bands or
  the pilot reports drift; lower it for bounce-back only together with `iterm_relax`.

## D-max
The `/dmax` cases are evaluated at `d_max` whenever `d_max > d`. In 2026.6 D rises toward `d_max`
through the gyro-driven boost (`d_max_gain`) *and* the setpoint-driven advance (`d_max_advance`),
so `d_max_gain = 0` alone does not keep D at base; only `d_max = d` does (pid.c).

D-max pays off when base D is noise-limited well below the damping optimum. If P/D already sit at
the Ms limit, extra D raises the sensitivity peak (the crossover moves into the delay region); then
keep d_max = D. Sweep it from D upward; the `/dmax` cases must pass.

## High and low throttle
- **TPA:** the plant gain grows with rpm, so full throttle has the least margin. `tpa_mode PD`
  attenuates P and D, `D` only D. Which helps depends on where the phase comes from: sweep
  `tpa_mode` with `tpa_rate` on this quad's `full/*` cases (seen on a 5": D-only TPA made full
  throttle *worse*, because D supplied the phase lead there). The full-throttle authority can
  differ between logs of the same quad, so keep slack on the full cases. Keep the breakpoint above
  hover (firmware: TPA acts above it).
- **`thrust_linear`:** raises low-throttle authority (propwash) and lowers high-throttle gain; it
  also raises motor noise at low throttle, so check `noise`. Sweep around the flown value (the
  firmware range is 0-150).
- **`dyn_idle_min_rpm`** (hundreds of rpm): a floor under the motor speed in dives, after chops and
  in prop wash. The model's idle case uses max(natural idle, floor), so settings below natural idle
  (`brief` → `flight.natural_idle_rpm_p20`) show no effect. The benefit is in the low tail of dives,
  which the model underestimates: look at dives with `bftune plot` (rpm vs the floor) and at the
  pilot's "floaty descents" feedback, the cost. It needs RPM telemetry.

## Feedforward
`bftune ff -o A cand.txt --axis roll --values LO:HI:STEP` (bracket the flown F). The `as flown`
column is the prediction to judge; `peak_max` (default: the flown tune's as-flown peak, i.e. "no
more overshoot than what flew") marks values above it. Move `peak_max` with the pilot's priorities
and the measured peaks they liked. Where model and measurement disagree on the flown tune's peak
(`analysis.html`), trust the model's *changes* more than its absolute peak. On yaw,
`feedforward_yaw_hold_*` also acts.

`feedforward_boost`, `feedforward_smooth_factor`, `feedforward_averaging` and
`rc_smoothing_auto_factor` are in the step simulation: sweep them with `--steps`. The simulation
can't see RC-link jitter: less smoothing or averaging raises the risk of a jittery centre stick,
more so on a slow or noisy link (`brief` → link rate). Say so and name the fallback value.
Conventions some pilots start from: `rc_smoothing_auto_factor` 35 freestyle, 25 race, 60 cinematic
*(convention)*.

**Mixed styles** (e.g. "freestyle and some racing"): keep the margins of the style where propwash
matters and take stick-response targets from the other. Say so.

## Judgement settings (not modelled; change only with evidence, and check them in the next log)
- **Anti-gravity** (`anti_gravity_gain`, `_cutoff_hz`, `_p_gain`): `diagnose` → `throttle_punch_dip`
  (attitude error while throttle rises fast, as a ratio to calm flight). Size the step by how far
  the ratio is from calm flight on this quad's other logs; more I also helps.
- **I-term relax** (`iterm_relax_cutoff`, `iterm_windup`, `iterm_rotation`): `bounce_back` reports
  the rebound as % of the move; lower the relax cutoff when it is large and the pilot feels it.
  Conventions: 15 freestyle, 20 race, 10 cinematic *(convention)*. `iterm_rotation` suits pilots
  who hold long turns or funnels (ask).
- **Throttle feel** (`throttle_boost`, `_cutoff`, `vbat_sag_compensation`): pilot report, and vbat
  sag in the log for sag compensation.
- **Feedforward on the RC link** (`feedforward_jitter_factor`, `_max_rate_limit`, `_transition`):
  raise jitter_factor on low-rate or noisy links; a transition softens the centre stick (cinematic).
- **PID-sum limits:** `diagnose` → `pidsum_clipping_*` (firmware limits `pidsum_limit`, `_yaw`).
- **Idle controller** (`dyn_idle_p/i/d_gain`, `dyn_idle_max_increase`, `motor_idle`): leave them
  unless `plot` shows the rpm dropping below the floor in dives.
- Always turn `simplified_pids_mode`, `simplified_dterm_filter` and `simplified_gyro_filter` OFF
  when you set explicit values: the Configurator's sliders would overwrite them. `emit` does this.
