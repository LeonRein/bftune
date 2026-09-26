"""Per-quad tuning project: the agent's memory across flights.

    <quad>/
      quad.md        hardware, pilot, style, constraints (facts that don't change per flight)
      history.md     one entry per iteration: what was flown, what the pilot felt, what changed, why
      logs/          blackbox logs + CLI dumps/diffs, named NN-<what>.BFL / NN-dump.txt
      analysis/      bftune analysis directories (one per analyzed log)
      tunes/NN-date/ delivered tune_cli.txt, revert_cli.txt, report, worklog
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

QUAD_MD = """# {name}

<!-- Facts about this quad and pilot. The bftune agent reads this first and keeps it current. -->

## Hardware
- Frame / size / props:
- Motors (kV, stator), cells:
- FC / gyro / ESC (bidirectional DShot?):
- Weight (AUW):
- Firmware:

## Pilot
- Style (freestyle / race / cinematic / long range):
- Priorities (e.g. locked-in, propwash, smoothness, motor temperature):
- Blackbox limits (max log rate without gaps, high resolution OK?):

## Constraints
- Motor temperature after flights with each tune (cool / warm / hot):
- Anything that must not change:
"""

HISTORY_MD = """# Tuning history: {name}

<!-- Newest entry last. One entry per iteration. Keep pilot quotes verbatim. -->

## 00 - {date} - project created
- Starting point: stock / previous tune (describe)
"""


def init_project(path: Path, name: str | None = None) -> list[Path]:
    path = Path(path)
    name = name or path.resolve().name
    made = []
    for d in ("logs", "analysis", "tunes"):
        (path / d).mkdir(parents=True, exist_ok=True)
    for fn, tpl in (("quad.md", QUAD_MD), ("history.md", HISTORY_MD)):
        f = path / fn
        if not f.exists():
            f.write_text(tpl.format(name=name, date=_dt.date.today().isoformat()))
            made.append(f)
    return made


def next_tune_dir(path: Path) -> Path:
    tunes = Path(path) / "tunes"
    tunes.mkdir(parents=True, exist_ok=True)
    nums = [int(p.name[:2]) for p in tunes.iterdir() if p.is_dir() and p.name[:2].isdigit()]
    return tunes / f"{(max(nums) + 1) if nums else 1:02d}-{_dt.date.today().isoformat()}"
