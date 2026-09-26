"""Which tunes do these logs contain? Groups logs by the tuning settings in their headers.

Fast (headers only). Tells the agent, before any heavy analysis, how many different tunes the
pilot flew, how they differ, and whether a CLI dump matches one of them or is a tune that no log
contains (then the model must come from a log, and the dump is only "the tune on the quad now").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from .flight import tuning_keys
from .io.bbl import _parse_headers, split_logs
from .io.dump import config_from_headers, load_dump


def _norm(v) -> str:
    return str(v).replace(" ", "").upper()


@dataclass
class TuneGroup:
    label: str
    values: dict[str, str]
    sessions: list[str] = field(default_factory=list)


def log_tunes(paths: list[str]) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    """(session name, tuning values, other facts) for every session of every log."""
    keys = tuning_keys()
    out = []
    for p in paths:
        data = Path(p).read_bytes()
        ranges = split_logs(data)
        for i, (a, b) in enumerate(ranges):
            h, _ = _parse_headers(data, a, b)
            cfg = config_from_headers(SimpleNamespace(headers=h))
            vals = {k: str(cfg.values[k]) for k in keys if k in cfg.values}
            facts = {"debug_mode": h.get("debug_mode", "?"), "log_rate": h.get("P interval", "?"),
                     "bytes": b - a}
            name = Path(p).name + (f"[{i}]" if len(ranges) > 1 else "")
            out.append((name, vals, facts))
    return out


def group_tunes(paths: list[str], dump: str | None = None, show: list[str] | None = None) -> dict:
    sessions = log_tunes(paths)
    groups: list[TuneGroup] = []
    for name, vals, _ in sessions:
        for g in groups:
            common = set(g.values) & set(vals)
            if all(_norm(g.values[k]) == _norm(vals[k]) for k in common):
                g.sessions.append(name)
                break
        else:
            groups.append(TuneGroup(chr(ord("A") + len(groups)), vals, [name]))
    keys = sorted({k for g in groups for k in g.values})
    differ = [k for k in keys if len({_norm(g.values.get(k, "?")) for g in groups}) > 1]
    res = {"groups": [{"tune": g.label, "sessions": g.sessions} for g in groups],
           "differences": {k: {g.label: g.values.get(k) for g in groups} for k in differ},
           "sessions": {n: f for n, _, f in sessions}}
    if show:
        res["values"] = {n: {k: v.get(k) for k in show} for n, v, _ in sessions}
    if dump:
        d = load_dump(dump).values
        match = []
        for g in groups:
            common = [k for k in g.values if k in d]
            n_diff = sum(_norm(g.values[k]) != _norm(d[k]) for k in common)
            match.append((n_diff, g.label, len(common)))
        best = min(match) if match else None
        res["dump"] = {"matches": best[1] if best and best[0] == 0 else None,
                       "closest": None if not best else {"tune": best[1], "settings_differing": best[0]},
                       "differences_to_closest": {} if not best or best[0] == 0 else {
                           k: {"log": next(g for g in groups if g.label == best[1]).values[k], "dump": d[k]}
                           for k in next(g for g in groups if g.label == best[1]).values
                           if k in d and _norm(next(g for g in groups if g.label == best[1]).values[k]) != _norm(d[k])}}
    return res


def format_tunes(res: dict) -> str:
    out = [f"{len(res['groups'])} different tune(s) in these logs:"]
    for g in res["groups"]:
        facts = [f"{s} (debug {res['sessions'][s]['debug_mode']})" for s in g["sessions"]]
        out.append(f"  tune {g['tune']}: " + ", ".join(facts))
    if res["differences"]:
        out.append("  settings that differ between the tunes:")
        for k, v in res["differences"].items():
            out.append(f"    {k:28s} " + "  ".join(f"{t}={x}" for t, x in v.items()))
    if "values" in res:
        names = list(res["values"])
        out.append("  requested settings per log:")
        out.append("    " + " " * 28 + "".join(f"{n[:18]:>20s}" for n in names))
        for k in next(iter(res["values"].values())):
            out.append(f"    {k:28s}" + "".join(f"{str(res['values'][n][k]):>20s}" for n in names))
    if "dump" in res:
        d = res["dump"]
        if d["matches"]:
            out.append(f"dump: matches tune {d['matches']} (use it with the logs of that tune)")
        else:
            c = d["closest"]
            out.append(f"dump: matches NO log - the quad runs a tune none of these logs flew (closest: tune {c['tune']}, "
                       f"{c['settings_differing']} settings differ). Analyze a log with its own tune; the dump is "
                       "the tune on the quad now (pass it to analyze: the model uses the log's settings).")
            for k, v in list(d["differences_to_closest"].items())[:12]:
                out.append(f"    {k:28s} log {v['log']} -> dump {v['dump']}")
    if len(res["groups"]) > 1 or ("dump" in res and not res["dump"]["matches"]):
        out.append("Ask the pilot which tune is on the quad now and how warm the motors got with each tune.")
    if any(str(f.get("debug_mode")) == "96" for f in res["sessions"].values()):
        out.append("(debug 96 = CHIRP: those logs carry the chirp excitation.)")
    return "\n".join(out)


def applied(tune_cli: str, new_dump: str, old_dump: str | None = None) -> dict:
    """Did the pilot paste the delivered CLI? Compares its `set` lines with a dump taken afterwards, and
    lists every other difference between the old and new dump (settings changed by hand or reset)."""
    import re

    want, profile = {}, None
    for line in Path(tune_cli).read_text().splitlines():
        m = re.match(r"^\s*profile\s+(\d+)", line)
        if m:
            profile = int(m.group(1))
        m = re.match(r"^\s*set\s+(\S+)\s*=\s*(.*?)\s*$", line)
        if m:
            want[m.group(1)] = m.group(2)
    new = load_dump(new_dump)
    missing = {k: {"delivered": v, "on_quad": new.values.get(k)} for k, v in want.items()
               if _norm(new.values.get(k, "<default>")) != _norm(v)}
    res = {"delivered_settings": len(want), "not_applied": missing,
           "active_profile": new.active_profile, "profile_in_cli": profile,
           "profile_ok": profile is None or profile == new.active_profile}
    if old_dump:
        old = load_dump(old_dump)
        keys = set(old.values) | set(new.values)
        other = {k: {"old": old.values.get(k), "new": new.values.get(k)} for k in sorted(keys)
                 if k not in want and _norm(old.values.get(k, "")) != _norm(new.values.get(k, ""))}
        res["other_changes"] = other
    return res


def format_applied(res: dict) -> str:
    out = []
    if not res["not_applied"]:
        out.append(f"all {res['delivered_settings']} delivered settings are on the quad")
    else:
        out.append(f"{len(res['not_applied'])} of {res['delivered_settings']} delivered settings are NOT on the quad:")
        out += [f"    {k:30s} delivered {v['delivered']:>12s}   on quad {v['on_quad'] or '(default)'}"
                for k, v in res["not_applied"].items()]
        out.append("  (a `diff all` omits settings at their default: 'None' can mean the default value)")
    if not res["profile_ok"]:
        out.append(f"WARNING: the CLI targeted profile {res['profile_in_cli']} but profile {res['active_profile']} is active")
    if "other_changes" in res:
        oc = res["other_changes"]
        out.append(f"{len(oc)} other setting(s) changed between the old and new dump (not part of the tune):" if oc
                   else "no other settings changed between the old and new dump")
        out += [f"    {k:30s} {v['old'] if v['old'] is not None else '(default)'} -> "
                f"{v['new'] if v['new'] is not None else '(default)'}" for k, v in oc.items()]
    return "\n".join(out)
