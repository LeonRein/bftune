# Who knows what: the design rule behind bftune's skills and tools

bftune has two authors of decisions. The **plugin author** (skills and code, written once) knows the
Betaflight source, control theory, and a handful of real tuning sessions. The **executing agent**
(Claude running the skills, every session) knows *this* quad: its logs, its `diff all`, the model's
numbers for its flown tunes, and a pilot it can ask. A value belongs where the knowledge is.

| kind of knowledge | who has it | where it lives |
|---|---|---|
| Firmware behaviour (filters, controller, TPA, D-max, FF, RC smoothing, setting ranges, what the blackbox logs) | author (Betaflight source) | the model in `src/bftune`, and one-line facts in the skills |
| Control theory and estimator limits (margins, aliasing, coherence, deconvolution bias) | author | code, with the limits stated in the output |
| A safety guardrail no pilot preference may cross | author (convention, deliberately loose) | `SAFETY_FLOOR` only |
| Scales of this quad (hover throttle and motor frequency, control bandwidth, throttle use, stick moves, link rate, identification repeatability) | the log | measured by the tools (`flight_profile`, chirp-round spread) and used instead of 5" numbers |
| What is good for this pilot (margins, overshoot, noise budget, when to stop) | the pilot, via the agent | design targets the agent derives and records (`bftune targets`, `quad.md`) |
| Starting points when there is no evidence yet | author (FPV habit, one or two quads) | labelled *(convention)* / *(seen on …)* in the skills, `convention` in tool output |

Rules that follow:
1. **No unlabelled number.** A number in a skill is a firmware fact (with its source), a guardrail,
   a derivation procedure, or a labelled starting point.
2. **Measure before assuming.** Where the log can tell (hover, bandwidth, stick speed, link rate,
   repeatability), the tools measure it and the output says where each value came from.
3. **Data in, verdicts labelled.** Heuristic verdicts (diagnose severities) only rank findings for
   attention; the evidence numbers are always reported, and the agent compares them with the other
   logs of the same quad.
4. **The quad's own history beats any table.** The skills ask the agent to keep per-quad
   calibration in `quad.md`: flown tunes vs how they felt, noise vs motor temperature, which changes
   the pilot noticed. Those become the targets, the noise budget and the stop rule.
5. **Hidden-but-decisive values are shown.** Throttle points of the design cases, robust-variant
   sizes, the stick moves of the step tests and the noise boundary are printed with their source.

## Decisions from the 0.8 audit (about 260 skill items and 200 code items reviewed)

| item | before | now |
|---|---|---|
| Frequency bands in `diagnose` (resonance search 15-200 Hz, propwash 15-80 Hz, I-term band 10 Hz, motor-noise band = 15-sample moving average) | 5" numbers; the motor-noise band even depended on the log rate | scale with the hover motor frequency measured in the log (ratios reproduce the 5" bands); fixed-Hz high-pass for motor noise |
| Throttle bands (0.2 / 0.5, chop < 0.25, calm 0.25-0.5, part throttle < 0.75) | fixed | relative to the hover throttle measured in the log |
| Bounce-back trigger (300 deg/s) and severity (40/100 deg/s) | fixed | the pilot's own fast moves; rebound as % of the move |
| Motor saturation (raw DShot ≥ 2046) | wrong with `motor_output_limit` < 100 or non-DShot | the motorOutput range × `motor_output_limit` (mixer.c) |
| Motor imbalance and HF motor noise | reported only above 10 % / warned above 3 % | always reported; imbalance severity kept as a hint, motor noise without severity (compare within the quad) |
| Noise model's motion/noise boundary (70 Hz) | fixed: counts a whoop's control motion as noise | 0.4 × hover motor frequency (70 Hz on a 5") |
| "Mid" design case at 50 % throttle | fixed | p90 of the throttle the pilot uses (≥ hover + 0.1), a settable target |
| Robust variants (+10 % / −12 % gain, +0.3 ms) | fixed for every quad | from the gain spread between chirp rounds, never narrower than the convention; can be widened, not narrowed |
| Step tests (300 deg/s in 50 ms, snap 0.6 × max rate in 30 ms, RC link 250 Hz) | fixed; the flick was faster than almost anything the 5" pilot flew | the pilot's typical and fast moves measured in the log; the link rate from the header or the log |
| FF overshoot targets (10 / 15 %) on synthetic ramps | fixed conventions on an unrealistic input | `as flown` step: the model replays the pilot's logged setpoint through the same estimator as the measurement; `peak_max` defaults to the flown tune's own peak |
| Chirp-quality warning (coherent band < 25 Hz) | fixed | scaled to the quad's control band |
| Noise budget from a temperature table | the only source | a starting point; the budget comes from the quad's own noise-vs-temperature table in `quad.md` |
| Stop rule (1 ms lag, 0.2 Ms, 15 %, 3 dB) | absolute | the pilot's own perceptibility from `history.md`; relative starting values; `ms_max` instead of "Ms 2.0" |
| D/P 0.5-1.0 (and 0.4-1.2, < 0.6), I/P 1.5-1.7, yaw D = 0, prefer `tpa_mode PD`, sweep ranges (D-term LPF 90-180 Hz, FF 60-180, TL 0/20/40, `rpm_filter_min_hz` 50-60 on 7-10") | rules or 5" ranges, partly contradictory | the flown tunes as reference, the model sweep decides; ranges bracket the flown values; lessons labelled *(seen on …)* |
| Chirp setup table | the only guidance | first-flight starting point (whoop and 7-10" rows marked as estimates); later flights derive amplitude, end frequency and duration from the last analysis |
| Flash capacity ("16 MB, 280 s") | stated as typical | computed from this quad's own log size and duration |
| Validation gates (replay > 60 %, closed loop < 1 dB / 8°) | thresholds | starting points; compare with what this quad's other logs reach |
| Hardware stop rules (imbalance > 30 %) | fixed | `motors` attributes an offset to one motor, or it changes between logs |
| `rules.py` (optional optimizer): ignored the FF targets, rpm weights "harmonics visible" without checking, dyn idle from p25 capped at 12000 rpm | bugs | targets used, weights left to the evidence, p20 as everywhere, firmware range 0-200 |
| `coherent_to_hz` | the highest coherent bin (one stray bin inflated it) | contiguous band |

Kept as they are, deliberately: the safety floor; the `simplified_*` guard; the firmware-version
gate; the dynamic notch at its minimum as a worst case; estimator internals (window lengths,
coherence thresholds, regularisation) whose effect on precision the output reports.
