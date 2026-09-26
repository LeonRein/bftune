"""Design targets vs the safety floor - chosen by the agent from the data, not from hidden tables.

- SAFETY_FLOOR: fixed guardrail. Loose limits from general control practice (a loop with PM < 30 deg
  or Ms > 2.6 is lightly damped); they are conventions, not measurements, and exist so that no
  override can make a delivered tune fragile.
- Design targets (`Goals` fields): what "good" means for THIS quad and pilot. The agent sets them
  from the evidence it has: the flown tune's measured margins and how the pilot rated that tune,
  the pilot's ranked priorities, motor temperature. Where a value can be derived from data, the
  workbench derives it (the performance and tracking bands from the flown tune's crossover); the
  rest start from neutral conventions, labelled as such, and are expected to be overridden with
  a reason (`bftune targets --set`). Nothing here needs Betaflight source knowledge: that lives in
  the model (filters, controller, TPA, D-max, settings), which the tools already apply.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

# never relaxed: hover / mid / full-throttle cases must stay inside these (idle is judged vs the flown tune)
SAFETY_FLOOR = {
    "pm_min": 30.0, "gm_min_db": 4.0, "ms_max": 2.6,  # nominal cases
    "pm_min_robust": 25.0, "gm_min_robust_db": 3.0, "ms_max_robust": 3.2,  # uncertainty variants
    "dm_min_ms": 0.5,
    "noise_budget_max": 2.0,  # at most 2x a proven-safe level, even in a supervised headroom flight
}


def data_profile_targets(profile: dict | None) -> dict:
    """Targets the flight itself decides: the "mid" design case sits at the throttle the pilot actually uses
    (p90 of armed throttle, at least 0.1 above hover, at most 0.85) instead of a fixed 50 %."""
    if not profile or not profile.get("throttle_pct"):
        return {}
    p90 = profile["throttle_pct"].get("90")
    hov = profile.get("hover_throttle") or 0.3
    if p90 is None:
        return {}
    return {"mid_throttle": round(float(min(0.85, max(p90, hov + 0.1))), 2)}


def data_bands(flown_hover_fc: float | None) -> dict:
    """Frequency bands that scale with the quad itself: taken from the flown tune's hover crossover
    (a 10" crosses over near 8 Hz, a whoop far above a 5"), instead of a size class."""
    if not flown_hover_fc or flown_hover_fc != flown_hover_fc:
        return {}
    fc = float(flown_hover_fc)
    return {"perf_band": (round(max(0.5, fc / 6), 1), round(3.0 * fc, 1)),
            "tracking_band": (round(max(0.3, fc / 15), 1), round(0.6 * fc, 1))}


# style presets: FPV conventions (not measured) - a starting point the agent confirms or overrides
STYLE_DEFAULTS = {
    "freestyle": {"pm_min": 42.0},
    "race": {"ms_max": 2.1, "pm_min": 40.0, "idle_weight": 0.3, "i_over_p": (1.7, 1.7, 1.7),
             "ff_overshoot_flick": 12.0, "ff_overshoot_snap": 20.0},
    "cinematic": {"ms_max": 1.7, "pm_min": 50.0, "idle_weight": 0.8, "noise_budget": 0.8,
                  "ff_overshoot_flick": 3.0, "ff_overshoot_snap": 8.0},
    "longrange": {"ms_max": 1.8, "pm_min": 45.0, "idle_weight": 0.5, "noise_budget": 0.85,
                  "ff_overshoot_flick": 6.0, "ff_overshoot_snap": 12.0},
}
# only these fields are design targets (the rest of Goals are internal weights)
TARGET_KEYS = ("ms_max", "ms_max_robust", "pm_min", "pm_min_robust", "gm_min_db", "gm_min_robust_db", "dm_min_ms",
               "noise_budget", "d_over_p", "i_over_p", "perf_band", "tracking_band", "idle_weight",
               "ff_overshoot_flick", "ff_overshoot_snap", "dmax_ratio_max", "hf_extrapolation", "peak_max", "mid_throttle",
               "tracking_weight", "gain_range", "gain_uncertainty", "delay_uncertainty_ms")


def _parse(key: str, val: str, current):
    if key == "peak_max" and current is None:
        current = (0.0, 0.0, 0.0)
    if isinstance(current, tuple):
        parts = [float(x) for x in str(val).replace(";", ",").split(",")]
        if len(parts) != len(current):
            raise SystemExit(f"target {key}: expected {len(current)} comma-separated numbers, got {val!r}")
        return tuple(parts)
    return float(val)


def check_floor(key: str, value) -> str | None:
    """Return a message if a target would relax the safety floor."""
    f = SAFETY_FLOOR
    if key in ("pm_min", "pm_min_robust", "gm_min_db", "gm_min_robust_db", "dm_min_ms") and value < f[key]:
        return f"{key} {value} is below the safety floor {f[key]}"
    if key in ("ms_max", "ms_max_robust") and value > f[key]:
        return f"{key} {value} is above the safety floor {f[key]}"
    if key == "noise_budget" and value > f["noise_budget_max"]:
        return f"noise_budget {value} is above the safety floor {f['noise_budget_max']}"
    return None


def load_targets(out: Path) -> dict:
    p = Path(out) / "targets.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"overrides": {}, "reasons": {}}


def save_targets(out: Path, data: dict) -> None:
    (Path(out) / "targets.json").write_text(json.dumps(data, indent=1))


def flown_ratio_targets(flown) -> dict:
    """The flown tune's own I/P (the starting point for I), and a D/P guardrail window that contains its D/P."""
    if flown is None:
        return {}
    out = {}
    try:
        ip = tuple(round(flown.i(f"i_{a}") / max(flown.i(f"p_{a}"), 1), 2) for a in ("roll", "pitch", "yaw"))
        if all(0.3 <= x <= 4.0 for x in ip):
            out["i_over_p"] = ip
        dp = [flown.i(f"d_{a}") / max(flown.i(f"p_{a}"), 1) for a in ("roll", "pitch")]
        lo, hi = 0.4, 1.2  # convention (guardrail against degenerate solutions)
        if min(dp) < lo or max(dp) > hi:
            out["d_over_p"] = (round(min(lo, min(dp) - 0.1), 2), round(max(hi, max(dp) + 0.1), 2))
    except (KeyError, ValueError):
        return {}
    return out


def build_goals(style: str | None, flown_hover_fc: float | None = None, overrides: dict | None = None,
                noise_budget: float | None = None, profile: dict | None = None, uncertainty: dict | None = None,
                flown=None):
    """Goals: neutral conventions, then data-derived bands, then the style preset, then the agent's
    overrides. Returns (goals, sources) so every output can say where each value came from."""
    from .search import Goals

    overrides = dict(overrides or {})
    style = style or overrides.pop("style", None) or "freestyle"
    overrides.pop("style", None)
    g = Goals(style=style)
    src = {k: "convention" for k in TARGET_KEYS}
    for k, v in data_bands(flown_hover_fc).items():
        setattr(g, k, v)
        src[k] = "from the flown tune's crossover"
    for k, v in data_profile_targets(profile).items():
        setattr(g, k, v)
        src[k] = "from the log (p90 throttle)"
    for k, v in flown_ratio_targets(flown).items():
        setattr(g, k, v)
        src[k] = "from the flown tune (I/P)" if k == "i_over_p" else "convention, widened to the flown tune's D/P"
    if uncertainty:  # identification's own estimate (chirp rounds) or its convention
        g.gain_uncertainty = round(float(uncertainty.get("k_hi", 1.1)) - 1.0, 3)
        g.delay_uncertainty_ms = round(float(uncertainty.get("dT", 0.0003)) * 1000, 2)
        src["gain_uncertainty"] = src["delay_uncertainty_ms"] = uncertainty.get("source", "identification")
    for k, v in STYLE_DEFAULTS.get(style, {}).items():
        setattr(g, k, v)
        src[k] = f"{style} convention"
    for k, v in overrides.items():
        if k not in TARGET_KEYS:
            raise SystemExit(f"unknown target {k!r}; targets: {', '.join(TARGET_KEYS)}")
        val = _parse(k, v, getattr(g, k))
        if k in ("gain_uncertainty", "delay_uncertainty_ms") and uncertainty:
            lo = getattr(g, k)  # the data's estimate: overrides may widen it, never shrink it
            if val < lo - 1e-9:
                raise SystemExit(f"target rejected: {k} {val} is below the identification's estimate {lo} "
                                 "(the uncertainty variants may only be widened)")
        bad = check_floor(k, val)
        if bad:
            raise SystemExit("target rejected: " + bad)
        setattr(g, k, val)
        src[k] = "override"
    if noise_budget is not None:
        bad = check_floor("noise_budget", noise_budget)
        if bad:
            raise SystemExit("target rejected: " + bad)
        g.noise_budget = noise_budget
        src["noise_budget"] = "command line"
    return g, src


def targets_table(g, src: dict, reasons: dict | None = None) -> list[dict]:
    rows = []
    vals = {f.name: getattr(g, f.name) for f in fields(g)}
    for k in TARGET_KEYS:
        rows.append({"target": k, "value": vals[k], "source": src.get(k, "default"),
                     "floor": SAFETY_FLOOR.get(k if k != "noise_budget" else "noise_budget_max"),
                     "reason": (reasons or {}).get(k, "") if src.get(k) == "override" else ""})
    return rows


def format_targets(g, src: dict, reasons: dict | None = None) -> str:
    out = [f"design targets (style {g.style}). Sources: 'from the flown tune' = derived from the data; 'convention' = a "
           "neutral starting point, not a measurement - confirm or override it from the evidence; the safety floor is fixed."]
    for r in targets_table(g, src, reasons):
        fl = "" if r["floor"] is None else f"   floor {r['floor']}"
        why = f"   # {r['reason']}" if r["reason"] else ""
        out.append(f"  {r['target']:20s} {str(r['value']):16s} [{r['source']}]{fl}{why}")
    return "\n".join(out)


def goals_dict(g) -> dict:
    return {k: v for k, v in asdict(g).items() if k in TARGET_KEYS or k == "style"}
