# Plugin evals

Each case builds a synthetic quad twin with known truth (`bftune synth`) in its scaffold script,
then asks the agent to tune it the way a pilot would. No private logs are needed.

```bash
claude plugin eval . --scaffold                       # all cases (each run takes 10-30 min)
claude plugin eval . --scaffold --case no-chirp-3inch5
```

| case | what it checks |
|---|---|
| `tune-5inch` | Plain-language request triggers the tune skill. The agent delivers a PASS tune with reasons, a revert block and a project folder, and bases filter decisions on the noise evidence. |
| `no-chirp-3inch5` | Freestyle-only log: the agent notices there is no chirp, keeps changes small and relative, and asks for a chirp flight. |
| `long-range-10inch` | 10" at a 4 kHz loop: large-craft judgement (low crossover, TPA for punch-outs, cool motors), not 5" numbers. |

Real logs: put them outside the repository, export `EVAL_LOG_DIR=/path/to/logs`, and adapt a
case's `fixture.sh` to copy them into the workspace. Never commit other people's logs.
