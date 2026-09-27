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


def _set_line(values: Tune, old: Tune, k: str) -> str:
    v = values.values.get(k, old.values.get(k))
    if v is None or v == "":
        return f"# {k}: previous value unknown (not in the dump or log header) - restore it from your diff all backup"
    return f"set {k} = {v}"


def cli_block(
    old: Tune,
    new: Tune,
    profile: int | None = 0,
    craft: str | None = None,
    firmware: str | None = None,
    extra_comment: list[str] | None = None,
    include_guard: bool = True,
    save: bool = True,
    version: str = "2026.6",
    keys: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Return (apply_block, revert_block, problems). `keys` pins these settings instead of the changes vs `old`
    (full mode: the tune on the quad is unknown, so every tuning setting is written)."""
    db = _scope_db(version)
    full = keys is not None
    keys = list(keys) if full else changed_keys(old, new)
    guard = {k: v for k, v in GUARD.items() if include_guard}
    tmp = new.copy()
    for k, v in guard.items():
        tmp.values[k] = v
    all_keys = list(dict.fromkeys(list(guard) + keys))
    problems = validate(tmp, all_keys, version)
    problems += [f"{k}: {db[k]['scope']}-scope setting, not written by this block" for k in all_keys
                 if k in db and db[k].get("scope") not in ("master", "profile", "rateprofile")]
    problems += [f"revert: {p}" for p in validate(old, [k for k in all_keys if k in old.values], version)]

    def block(values: Tune, header: list[str], skip: tuple = ()) -> str:
        keys_ = [k for k in all_keys if k not in skip]
        master = [k for k in keys_ if db.get(k, {}).get("scope") == "master"]
        prof = [k for k in keys_ if db.get(k, {}).get("scope") == "profile"]
        rate = [k for k in keys_ if db.get(k, {}).get("scope") == "rateprofile"]
        lines = list(header)
        for k in master:
            lines.append(_set_line(values, old, k))
        if prof:
            if profile is not None:
                lines.append(f"profile {profile}")
            else:
                lines.append("# >>> PID profile settings below. No dump was given, so the profile index is unknown:")
                lines.append("# >>> type `profile N` (N = the PID profile you fly, 0-3) before pasting the following lines.")
            for k in prof:
                lines.append(_set_line(values, old, k))
        if rate:
            lines.append("# rate profile settings (active rate profile)")
            for k in rate:
                lines.append(_set_line(values, old, k))
        if save:
            lines.append("save")
        return "\n".join(lines) + "\n"

    stamp = _dt.date.today().isoformat()
    head = [
        f"# bftune tune for {craft or 'craft'}" + (f" (Betaflight {firmware})" if firmware else "") + f" - {stamp}",
        "# Paste into the Betaflight CLI. 'save' reboots the flight controller.",
    ]
    head += [f"# {c}" for c in (extra_comment or [])]
    if profile is None:
        head.append("# NOTE: no CLI dump was given. Back up first with `diff all`; that backup is the exact way back.")
    apply_txt = block(tmp, head)
    old_full = old.copy()
    rhead = [f"# bftune revert block - restores the values before {stamp}"]
    skip: tuple = ()
    if profile is None:  # no dump: the old slider state is unknown, so don't guess it
        skip = tuple(guard)
        rhead.append("# No dump was given: the simplified_* slider state before the tune is unknown and not restored here.")
        rhead.append("# The exact way back is your `diff all` backup.")
    revert_txt = block(old_full, rhead, skip)
    if full:  # the old tune is unknown: no revert block can be right
        revert_txt = (f"# bftune revert - {stamp}\n# This tune was written in full because the tune on the quad was unknown.\n"
                      "# The way back is the `diff all` backup you took before pasting.\n")
    return apply_txt, revert_txt, problems


def diff_table(old: Tune, new: Tune, reasons: dict[str, str] | None = None) -> list[tuple[str, str, str, str]]:
    rows = []
    for k in changed_keys(old, new):
        rows.append((k, str(old.values.get(k, "")), str(new.values[k]), (reasons or {}).get(k, "")))
    return rows
