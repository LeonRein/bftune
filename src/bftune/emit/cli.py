"""Generate Betaflight CLI command blocks from a tune."""

from __future__ import annotations

import datetime as _dt
import json
from importlib import resources

from ..model.params import Tune, validate

# Settings that must be forced so explicit values are not overwritten later by the
# Configurator's tuning sliders (MSP_SET_SIMPLIFIED_TUNING re-applies slider formulas).
GUARD = {
    "simplified_pids_mode": "OFF",
    "simplified_dterm_filter": "OFF",
    "simplified_gyro_filter": "OFF",
}


def _scope_db(version: str = "2026.6") -> dict:
    with resources.files("bftune.data").joinpath(f"settings_{version}.json").open() as fh:
        return json.load(fh)["settings"]


def changed_keys(old: Tune, new: Tune) -> list[str]:
    keys = []
    for k, v in new.values.items():
        if str(old.values.get(k, "")).upper() != str(v).upper():
            keys.append(k)
    return keys


def cli_block(
    old: Tune,
    new: Tune,
    profile: int = 0,
    craft: str | None = None,
    firmware: str | None = None,
    extra_comment: list[str] | None = None,
    include_guard: bool = True,
    save: bool = True,
    version: str = "2026.6",
) -> tuple[str, str, list[str]]:
    """Return (apply_block, revert_block, problems)."""
    db = _scope_db(version)
    keys = changed_keys(old, new)
    guard = {k: v for k, v in GUARD.items() if include_guard}
    tmp = new.copy()
    for k, v in guard.items():
        tmp.values[k] = v
    all_keys = list(dict.fromkeys(list(guard) + keys))
    problems = validate(tmp, all_keys, version)

    def block(values: Tune, header: list[str]) -> str:
        master = [k for k in all_keys if db.get(k, {}).get("scope") == "master"]
        prof = [k for k in all_keys if db.get(k, {}).get("scope") == "profile"]
        rate = [k for k in all_keys if db.get(k, {}).get("scope") == "rateprofile"]
        lines = list(header)
        for k in master:
            lines.append(f"set {k} = {values.values.get(k, old.values.get(k, ''))}")
        if prof:
            lines.append(f"profile {profile}")
            for k in prof:
                lines.append(f"set {k} = {values.values.get(k, old.values.get(k, ''))}")
        if rate:
            lines.append("# rate profile settings (active rate profile)")
            for k in rate:
                lines.append(f"set {k} = {values.values.get(k, old.values.get(k, ''))}")
        if save:
            lines.append("save")
        return "\n".join(lines) + "\n"

    stamp = _dt.date.today().isoformat()
    head = [
        f"# bftune tune for {craft or 'craft'}" + (f" (Betaflight {firmware})" if firmware else "") + f" - {stamp}",
        "# Paste into the Betaflight CLI. 'save' reboots the flight controller.",
    ]
    head += [f"# {c}" for c in (extra_comment or [])]
    apply_txt = block(tmp, head)
    old_full = old.copy()
    revert_txt = block(old_full, [f"# bftune revert block - restores the values before {stamp}"])
    return apply_txt, revert_txt, problems


def diff_table(old: Tune, new: Tune, reasons: dict[str, str] | None = None) -> list[tuple[str, str, str, str]]:
    rows = []
    for k in changed_keys(old, new):
        rows.append((k, str(old.values.get(k, "")), str(new.values[k]), (reasons or {}).get(k, "")))
    return rows
