# bftune: let Claude tune your Betaflight quad

bftune turns [Claude](https://claude.com/claude-code) into a tuning engineer for Betaflight FPV quads,
from tinywhoops to 10" long range. It reads your blackbox logs, builds a model of *your* quad, tests
tunes on that model and gives you CLI commands to paste into Betaflight, with a reason for every
change.

> [!IMPORTANT]
> **Betaflight 2026.6.2 only (for now).** The model is a port of that release's controller and
> filters. With other versions bftune can still diagnose your logs, but it won't compute a tune.

> [!WARNING]
> **No guarantee.** bftune is an experimental hobby project. A tune can be wrong, and a wrong tune can
> make your quad oscillate, overheat its motors, become uncontrollable or crash. You fly every tune at your own
> risk. Keep the revert block it gives you, do the first flight low and careful, and check the motor
> temperature after it.

## How to use it

1. **Put your blackbox logs** (`.BFL` / `.BBL`) **and a `diff all`** of the quad into one folder.
   (For the `diff all`: in Betaflight Configurator's CLI tab type `diff all`, then click *Save to File*.)
2. **Open that folder in Claude** (the Code tab of the Claude desktop app, or Claude Code in a
   terminal) and say: **"Tune my drone."**
3. **Answer its questions.** It asks what you want more of (stick response, propwash handling,
   smoothness …), how you fly and how warm the motors got.
4. **Paste the CLI block** it gives you into the Configurator's CLI tab and fly.
5. **Tell it how it flew** (and bring a new log if you have one). It keeps a memory of your quad in
   that folder and continues from there.

That's it. You never run a script and there are no settings to learn. You just talk to it.
The one-time setup is below in [Install](#install).

What a session looks like:

> **You:** Tune my drone. It feels floaty and propwash is bad.
>
> **Claude:** *(reads the logs)* A few questions before I start: how warm were the motors after that
> pack, and do you mostly fly freestyle or race? …
>
> **Claude:** Your propwash comes from a sensitivity peak at 46 Hz. The D-term filter adds so much
> lag that P and D push the loop close to its limit there. Your log shows the same 46 Hz bump in
> the tracking error. Here is the plan: a steeper D-term filter at a lower cutoff, which lets D stay
> where it is with less lag; P −10 %; dynamic idle on, for authority in dives. The model
> predicts the peak drops from 3.3 to 1.9, with motor noise at 0.85× your current (cool) tune.
> Here's the CLI, the revert block and what to test first…

## Install
You need:
- **A Claude Pro plan or higher** (Claude Code isn't in the free plan).
- **Claude Code**: the [Claude desktop app](https://claude.com/download) (Code tab) or
  [Claude Code in a terminal](https://code.claude.com/docs/en/quickstart).
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)**, which runs the bundled Python
  tools. Nothing else to install; the first run downloads what it needs.
- **Windows:** install [Git for Windows](https://git-scm.com/downloads/win) too. Claude Code
  then runs the tools through Git Bash, the setup bftune was built with. Without Git, Claude Code
  uses PowerShell and bftune's `bftune.cmd` launcher; that should work but hasn't been tested yet.
  Please report back either way.

Then add the plugin once. Start `claude` in a terminal (or open a Code session in the desktop app)
and type:
```
/plugin marketplace add LeonRein/bftune
/plugin install bftune@bftune
```
Choose "install for you" (user scope) so it works in every folder. After that the plugin is
available in the desktop app too. To update later: `/plugin marketplace update bftune`.

## Better logs, better tunes
- **Any normal flight works.** A pack of freestyle with some flips, rolls and dives is enough to
  start. From normal flying, bftune is less sure of the model, so it makes small, targeted changes.
- **Best: a log with chirps.** Betaflight 2026.6's CHIRP mode makes the quad sweep each axis while
  you hover, which gives a precise model. Ask Claude *"how do I fly a test log?"* (or type
  `/bftune:flight-plan`) and it tells you exactly how to record one, for your craft and logging
  hardware, with a CLI block to set it up.
- **Logs of other tunes of the same quad**, with how warm the motors got and how they felt, are
  gold: they calibrate the model against reality.
- A 1 kHz blackbox rate is enough. Faster is better, but only if the log has no gaps.

## What you can ask
You can just talk, or use the commands:

| you want to… | say or type |
|---|---|
| get a tune from logs | "tune my drone" or `/bftune:tune` |
| report back after flying a tune | "I flew the new tune, propwash is better but …" or `/bftune:feedback` |
| know how to record a good log | "how do I fly the test log?" or `/bftune:flight-plan` |
| check a tune before flying it | "is this diff safe on my quad?" or `/bftune:review` |

### What the agent asks you
In its first reply, before any long analysis: a fresh `diff all` if it has none, what the new tune
should **prioritise** (ranked: stick response, locked-in feel, propwash, smoothness, clean
punch-outs, efficiency), how you fly, how warm the motors got (cold / cool / slightly warm / warm /
hot) and anything that changed since the log (props, battery, weight). It only asks what the logs
can't tell it.

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
Open that folder again next time and tell Claude how the tune flew. It continues where it left off.

## How it works
Every quad and every log is different, so a fixed pipeline that applies the same recipe each time
isn't enough. bftune gives Claude precise instruments and the engineering knowledge to use them:
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

### How it decides what "good" is
There are two tiers. A fixed **safety floor** (phase margin, gain margin, peak sensitivity, delay
margin, a noise ceiling) applies to every delivered tune. Above it, the **design targets** are chosen
for your quad and your priorities from the evidence: frequency bands scale with the crossover your
flown tune achieved (a 10" and a whoop differ by an order of magnitude), margins are calibrated on
how your flown tunes measured and how you rated them, and the noise budget follows your motor
temperatures. Every target in the report says where it came from (data, a labelled convention,
or the agent's override with its reason).

## Safety
Read the warning at the top first. On top of that:
- Every delivered tune passes a deterministic gate. It needs phase margin, gain margin and peak
  sensitivity at idle, hover, mid and full throttle, including worse-than-measured variants (more
  gain, more delay, D-max). Motor noise must stay within a budget set from a tune that has already
  flown on your quad and how warm its motors got.
- Without a chirp, the gate becomes relative: no worse than the tune you flew.
- It stops and tells you when the problem is hardware (desyncs, a bent prop, a weak motor,
  saturation at hover) rather than tuning.
- Passing the gate means the *model* is happy, not that the quad will be. The model can be wrong
  (a bad log, a changed prop, something it doesn't model). First flights are still first flights:
  hover, land, feel the motors, then push. Every tune comes with a revert block.

## Limits
- Model-based tuning needs Betaflight **2026.6.2**: the controller and filter model is a port of that
  release. On older firmware, bftune still diagnoses logs (problems, motor health, error spectra)
  but won't compute a tune. Newer releases are checked against the log before the model is trusted.
- The model is linear around each operating point. Saturation, airmode, I-term and anti-gravity
  are handled by judgement and margins, not by the model.
- Noise above the log's Nyquist frequency is inferred, not observed.
- Tested on real logs of a 3.5" 4S and a 5" 6S quad (several tunes each) and on synthetic twins of a
  65 mm whoop, 3.5", 5" and 10" with known truth. Whoops and long-range quads have not been flown
  with it yet: reports are very welcome.
- Your logs are analysed on your computer. Claude sees the analysis results and the files it opens
  (for example your `diff all`), like in any Claude session.

## Feedback
Found a bad tune, a crash in the tools or a quad it doesn't handle well? Please
[open an issue](https://github.com/LeonRein/bftune/issues) with the log, the `diff all` and what
happened.

## For developers
The instruments are a Python package (`src/bftune`) with a CLI the agent calls. See
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the CLI reference, the tests and the plugin evals,
[docs/model.md](docs/model.md) for the firmware model with source references, and
[docs/python-api.md](docs/python-api.md) for the API.

License: GPL-3.0-or-later (bftune ports Betaflight's GPL-3.0 algorithms). Provided without any
warranty, see [LICENSE](LICENSE).
