# bftune: an AI tuning engineer for Betaflight quads

bftune is a [Claude Code](https://claude.com/claude-code) plugin that tunes your FPV quad the way an
experienced tuning engineer would. It works for everything from 1S tinywhoops to 10" long range.

1. You give it your blackbox logs and tell it how the quad flies and what you want.
2. It works out what limits your quad, tests its ideas on a physical model of *your* quad, and
   hands you CLI commands to paste into Betaflight, with a reason for every change.
3. After you fly them, you tell it how it went, and it learns from that for the next round.

You never run a script. You have a conversation:

> **You:** Tune my 3.5" please. Logs and dump are in ~/fpv/mini/. It feels floaty and propwash is bad.
>
> **bftune:** *(reads the logs)* Two questions before I start: how warm were the motors after that
> pack, and do you mostly fly freestyle or race? …
>
> **bftune:** Your propwash comes from a sensitivity peak at 46 Hz. The D-term filter adds so much
> lag that P and D push the loop close to its limit there. Your log shows the same 46 Hz bump in
> the tracking error. Here is the plan: a steeper D-term filter at a lower cutoff, which lets D stay
> where it is with less lag; P −10 %; dynamic idle on, for authority in dives. The model
> predicts the peak drops from 3.3 to 1.9, with motor noise at 0.85× your current (cool) tune.
> Here's the CLI, the revert block and what to test first…

## Why an agent and not a script?
Every quad and every log is different. A fixed pipeline can only apply the same recipe each time.
bftune gives Claude a set of precise instruments and the engineering knowledge to use them:
- **A model of your quad**, identified from the log: rate dynamics per axis, motor lag vs rpm,
  delay, and an alias-aware noise model reconstructed from a 1-2 kHz log. It is a faithful port of
  Betaflight 2026.6's controller and filters.
- **Fast what-if tools**: any tune is scored in about a second (stability margins at idle, hover and
  full throttle, propwash sensitivity, stick latency and overshoot, motor noise and heat risk).
- **Diagnosis from flight data**: resonances, propwash, bounce-back, desyncs, motor imbalance,
  saturation.
- **Knowledge skills**: symptom → cause → fix, loop shaping, filters and noise, craft classes, and how
  far to trust the model against the pilot.

The agent decides what to investigate. It forms hypotheses ("the propwash comes from filter
lag"), tests them on the model, checks them against the flight data and your impressions, and
explains its choices. Every tune must still pass a fixed safety gate before it is handed out.

## Install
You need [Claude Code](https://claude.com/claude-code) and [uv](https://docs.astral.sh/uv/)
(uv runs the bundled Python tools; nothing else to install). In Claude Code:
```
/plugin marketplace add LeonRein/bftune
/plugin install bftune@bftune
```

## Use it
Start Claude Code in any folder and either type `/bftune:tune` or just ask, e.g. "tune my quad,
the logs are in ./logs". Other entry points:

| you want to… | say or type |
|---|---|
| get a tune from logs | `/bftune:tune` or "tune my quad" |
| report back after flying a tune | `/bftune:feedback` or "I flew the new tune, propwash is better but …" |
| know how to record a good log | `/bftune:flight-plan` or "how do I fly the test log?" |
| check a tune before flying it | `/bftune:review` or "is this diff safe on my quad?" |

### What to bring
- **A blackbox log** of the quad (`.BFL`/`.BBL`) and its **CLI `dump`** (or `diff all`).
- **Best: a log with chirps.** Betaflight 2026.6's CHIRP mode makes the quad sweep each axis while
  you hover, which gives a precise model. `/bftune:flight-plan` tells you exactly how to record it,
  for your craft and logging hardware.
- **Without chirps** it still works from normal flying, but with lower confidence, so it only makes
  small, targeted changes and asks for a chirp flight next.
- **Logs of other tunes of the same quad**, with how warm the motors got and how they felt. These are
  gold: they calibrate the model against reality.

A 1 kHz blackbox rate is enough. Faster is better, but only without gaps in the log.

### What the agent asks you
Right away, in its first reply: a fresh `diff all`, what the new tune should **prioritise** (ranked:
stick response, locked-in feel, propwash, smoothness, clean punch-outs, efficiency), how you fly,
how warm the motors got (cold / cool / slightly warm / warm / hot) and anything that changed since
the log (props, battery, weight). It asks only what the logs can't tell it, and before any long
analysis.

### How it decides what "good" is
There are two tiers. A fixed **safety floor** (phase margin, gain margin, peak sensitivity, delay
margin, a noise ceiling) protects every delivered tune. Above it, the **design targets** are chosen
for your quad and your priorities from the evidence: frequency bands scale with the crossover your
flown tune achieved (a 10" and a whoop differ by an order of magnitude), margins are calibrated on
how your flown tunes measured and how you rated them, and the noise budget follows your motor
temperatures. Every target in the report says where it came from (data, a labelled convention,
or the agent's override with its reason).

### What you get
- A **CLI block** to paste into the Configurator's CLI tab. It turns off the simplified-tuning
  sliders (so the Configurator doesn't overwrite the values later), sets the tune and saves.
- A **table of every change** with the reason, and what should feel different.
- A **revert block** to go back to your current tune.
- A **first-flight checklist** and what to log next.
- A **report** (`report.html`, with graphs): verdict, changes, copyable CLI, predicted margins,
  stick lag and motor noise, the design targets and the model of your quad. The analysis
  (`analysis.html`) and log comparisons (`logs.html`: spectrograms, the stick response measured
  from your flights) use the same style.

### Your quad's project folder
bftune keeps a folder per quad as its memory across flights:
```
mini35/
  quad.md          hardware, your style and priorities, lessons learned
  history.md       every iteration: what was flown, what you felt, what changed and why
  logs/            your logs and dumps
  analysis/        the model of each log
  tunes/01-2026-09-26/   delivered CLI, revert block, report, the engineer's worklog
```
Point the agent at that folder next time (`/bftune:feedback mini35/`) and it continues where it
left off.

## Safety
- Every delivered tune passes a deterministic gate. It needs phase margin, gain margin and peak
  sensitivity at idle, hover, mid and full throttle, including worse-than-measured variants (more
  gain, more delay, D-max). Motor noise must stay within a budget set from a tune that has already
  flown on your quad and how warm its motors got.
- Without a chirp, the gate becomes relative: no worse than the tune you flew.
- It stops and tells you when the problem is hardware (desyncs, a bent prop, a weak motor,
  saturation at hover) rather than tuning.
- It's a model. First flights are still first flights: hover, land, feel the motors, then push.
  Every tune comes with a revert block.

## Limits
- Model-based tuning needs Betaflight **2026.6.x**: the controller and filter model is a port of that
  release. On older firmware, bftune still diagnoses logs (problems, motor health, error spectra)
  but won't compute a tune. Newer releases are checked against the log before the model is trusted.
- The model is linear around each operating point. Saturation, airmode, I-term and anti-gravity
  are handled by judgement and margins, not by the model.
- Noise above the log's Nyquist frequency is inferred, not observed.
- Tested on real logs of a 3.5" 4S quad (several tunes, cross-validated) and on synthetic twins of a
  65 mm whoop, 3.5", 5" and 10" with known truth.

## For developers
The instruments are a Python package (`src/bftune`) with a CLI the agent calls. See
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the CLI reference, the tests and the plugin evals,
[docs/model.md](docs/model.md) for the firmware model with source references, and
[docs/python-api.md](docs/python-api.md) for the API.

License: GPL-3.0-or-later (bftune ports Betaflight's GPL-3.0 algorithms).
