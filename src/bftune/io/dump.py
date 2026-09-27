"""Parse Betaflight CLI `dump` / `diff all` output and blackbox headers into one config.

The result is a flat ``Config`` mapping CLI setting names to string values for the
*active* PID profile and rate profile, plus the raw per-profile sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .bbl import Log

_SET_RE = re.compile(r"^set\s+(\S+)\s*=\s*(.*?)\s*$")
_VERSION_RE = re.compile(r"Betaflight\s*/\s*(\S+)\s*\((\S+)\)\s*(\d+\.\d+\.\d+)")


@dataclass
class Config:
    values: dict[str, str] = field(default_factory=dict)  # active view (master + active profiles)
    master: dict[str, str] = field(default_factory=dict)
    profiles: dict[int, dict[str, str]] = field(default_factory=dict)
    rateprofiles: dict[int, dict[str, str]] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)
    active_profile: int = 0
    active_rateprofile: int = 0
    profile_known: bool = False  # a `profile N` line was read (a real dump); a log header has no profile index
    is_diff: bool = False  # a `diff all`: settings at their default are left out
    defaulted: list[str] = field(default_factory=list)  # keys a diff left out, filled with the firmware default
    firmware_version: str | None = None
    board: str | None = None
    craft_name: str | None = None
    source: str = ""
    # set when a dump contradicts the log header on tuning settings: the model uses the header (what flew),
    # `quad` holds the dump-based config (the tune on the quad now), `mismatch` the (key, log, dump) triples
    mismatch: list = field(default_factory=list)
    quad: Config | None = None

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def int(self, key: str, default: int = 0) -> int:
        v = self.values.get(key)
        if v is None:
            return default
        try:
            return int(v.split(",")[0])
        except ValueError:
            return default

    def ints(self, key: str) -> list[int]:
        v = self.values.get(key)
        if not v:
            return []
        return [int(x) for x in v.split(",")]

    def str(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def on(self, key: str) -> bool:
        return self.values.get(key, "OFF").upper() in ("ON", "1", "TRUE")

    def firmware_tuple(self) -> tuple[int, ...] | None:
        if not self.firmware_version:
            return None
        return tuple(int(x) for x in self.firmware_version.split("."))


# defaults that depend on the target or the hardware (not safe to assume when a diff leaves them out)
_TARGET_DEFAULTS = {"pid_process_denom", "dshot_bidir", "motor_poles", "motor_kv"}


def _is_diff(text: str, n_set: int) -> bool:
    """`diff all` (settings at default left out) vs a full `dump`: the echoed command, else the size."""
    for raw in text.splitlines()[:5]:
        cmd = raw.strip().lstrip("#").strip().lower()
        if cmd.startswith("diff"):
            return True
        if cmd.startswith("dump"):
            return False
    return n_set < 300  # a 2026.6 dump prints every setting (~560 lines on a quad), a diff all ~100-150


def fill_diff_defaults(cfg: Config) -> None:
    """A `diff all` leaves out every setting at its default. Without this, a gap would be filled from the log
    header later, which is wrong when the pilot reset a setting to default after the logged flight."""
    from ..model.params import DEFAULTS

    for k, v in DEFAULTS.items():
        if k not in cfg.values and k not in _TARGET_DEFAULTS:
            cfg.values[k] = str(v)
            cfg.defaulted.append(k)


def parse_dump(text: str) -> Config:
    cfg = Config(source="dump")
    section: dict[str, str] = cfg.master
    n_set = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = _VERSION_RE.search(line)
            if m and cfg.firmware_version is None:
                cfg.board, cfg.firmware_version = m.group(1), m.group(3)
            if line.startswith("# name:"):
                cfg.craft_name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("profile "):
            n = int(line.split()[1])
            section = cfg.profiles.setdefault(n, {})
            cfg.active_profile = n  # dump lists the active profile last
            cfg.profile_known = True
            continue
        if line.startswith("rateprofile "):
            n = int(line.split()[1])
            section = cfg.rateprofiles.setdefault(n, {})
            cfg.active_rateprofile = n
            continue
        if line.startswith("feature "):
            name = line.split()[1]
            if name.startswith("-"):
                cfg.features[name[1:]] = False
            else:
                cfg.features[name] = True
            continue
        m = _SET_RE.match(line)
        if m:
            section[m.group(1)] = m.group(2)
            n_set += 1
    # `dump` prints the active profile selection via "set profile"? no: the last one listed
    # with `dump all` is re-selected at the end by "profile N"; handle both.
    cfg.values = dict(cfg.master)
    cfg.values.update(cfg.profiles.get(cfg.active_profile, {}))
    cfg.values.update(cfg.rateprofiles.get(cfg.active_rateprofile, {}))
    if n_set and _is_diff(text, n_set):
        cfg.is_diff = True
        fill_diff_defaults(cfg)
    return cfg


def load_dump(path: str) -> Config:
    """A CLI dump/diff, or a blackbox log whose header holds the tune on the quad now (its last session):
    for "the quad still runs the tune from log X" when no fresh dump exists."""
    raw = Path(path).read_bytes()
    if raw.lstrip()[:9] == b"H Product":
        from types import SimpleNamespace

        from .bbl import _parse_headers, split_logs

        a, b = split_logs(raw)[-1]
        cfg = config_from_headers(SimpleNamespace(headers=_parse_headers(raw, a, b)[0]))
        cfg.source = f"log header ({Path(path).name})"
        return cfg
    return parse_dump(raw.decode("utf-8", errors="replace"))


# Blackbox header keys that map 1:1 (or with a transform) onto CLI setting names.
_HEADER_PAIRS = {
    "rollPID": ("p_roll", "i_roll", "d_roll"),
    "pitchPID": ("p_pitch", "i_pitch", "d_pitch"),
    "yawPID": ("p_yaw", "i_yaw", "d_yaw"),
    "d_max": ("d_max_roll", "d_max_pitch", "d_max_yaw"),
    "ff_weight": ("f_roll", "f_pitch", "f_yaw"),
    "rc_rates": ("roll_rc_rate", "pitch_rc_rate", "yaw_rc_rate"),
    "rc_expo": ("roll_expo", "pitch_expo", "yaw_expo"),
    "rates": ("roll_srate", "pitch_srate", "yaw_srate"),
    "rate_limits": ("roll_rate_limit", "pitch_rate_limit", "yaw_rate_limit"),
    "gyro_lpf1_dyn_hz": ("gyro_lpf1_dyn_min_hz", "gyro_lpf1_dyn_max_hz"),
    "dterm_lpf1_dyn_hz": ("dterm_lpf1_dyn_min_hz", "dterm_lpf1_dyn_max_hz"),
    "gyro_notch_hz": ("gyro_notch1_hz", "gyro_notch2_hz"),
    "gyro_notch_cutoff": ("gyro_notch1_cutoff", "gyro_notch2_cutoff"),
}
_HEADER_RENAMES = {
    "motor_idle": "motor_idle",
    "gyro_hardware_lpf": "gyro_hardware_lpf",
}
# Enumerations logged numerically in headers
_FILTER_TYPES = {"0": "PT1", "1": "BIQUAD", "2": "PT2", "3": "PT3"}
_ENUM_HEADERS = {
    "gyro_lpf1_type": _FILTER_TYPES,
    "gyro_lpf2_type": _FILTER_TYPES,
    "dterm_lpf1_type": _FILTER_TYPES,
    "dterm_lpf2_type": _FILTER_TYPES,
    "tpa_mode": {"0": "PD", "1": "D"},
    "iterm_relax": {"0": "OFF", "1": "RP", "2": "RPY", "3": "RP_INC", "4": "RPY_INC"},
    "iterm_relax_type": {"0": "GYRO", "1": "SETPOINT"},
    "feedforward_averaging": {"0": "OFF", "1": "2_POINT", "2": "3_POINT", "3": "4_POINT"},
    "dshot_bidir": {"0": "OFF", "1": "ON"},
    "rc_smoothing": {"0": "OFF", "1": "ON"},
    "blackbox_high_resolution": {"0": "OFF", "1": "ON"},
    "simplified_pids_mode": {"0": "OFF", "1": "RP", "2": "RPY"},
    "simplified_dterm_filter": {"0": "OFF", "1": "ON"},
    "simplified_gyro_filter": {"0": "OFF", "1": "ON"},
    "tpa_low_always": {"0": "OFF", "1": "ON"},
    "use_integrated_yaw": {"0": "OFF", "1": "ON"},
    "dshot_edt": {"0": "OFF", "1": "ON", "2": "FORCE"},
    "rates_type": {"0": "BETAFLIGHT", "1": "RACEFLIGHT", "2": "KISS", "3": "ACTUAL", "4": "QUICK"},
    "mixer_type": {"0": "LEGACY", "1": "LINEAR", "2": "DYNAMIC", "3": "EZLANDING"},
}


def config_from_headers(log: Log) -> Config:
    """Build a best-effort Config from blackbox header fields (fallback if no dump)."""
    cfg = Config(source="blackbox-header")
    h = log.headers
    for key, names in _HEADER_PAIRS.items():
        if key in h:
            vals = h[key].split(",")
            for n, v in zip(names, vals):
                cfg.values[n] = v.strip()
    for key, val in h.items():
        if key.startswith("Field ") or key in _HEADER_PAIRS:
            continue
        name = _HEADER_RENAMES.get(key, key)
        if name in _ENUM_HEADERS:
            val = _ENUM_HEADERS[name].get(val.strip(), val)
        cfg.values.setdefault(name, val.strip())
    fw = h.get("Firmware revision", "")
    m = re.search(r"(\d+\.\d+\.\d+)", fw)
    if m:
        cfg.firmware_version = m.group(1)
    cfg.craft_name = h.get("Craft name")
    cfg.board = h.get("Board information")
    return cfg


def reconcile(dump: Config | None, header: Config, tuning_keys: set[str]) -> Config:
    """Merge dump and header, keeping the model honest when the dump belongs to another tune.

    A dump taken after the flight (or of a later tune) differs from the log header in tuning
    settings. The identification and noise model must use what actually flew, so the header wins
    for those keys; the dump-based config is kept as `quad` (the tune on the quad now).
    """
    merged = merge(dump, header)
    if dump is None:
        return merged

    def norm(v):
        return str(v).replace(" ", "").upper()

    mism = [(k, header.values[k], dump.values[k]) for k in sorted(tuning_keys)
            if k in header.values and k in dump.values and norm(header.values[k]) != norm(dump.values[k])]
    if not mism:
        return merged
    model = merge(dump, header)
    for k in tuning_keys:
        if k in header.values:
            model.values[k] = header.values[k]
    model.source = "header+dump (dump differs from the log: header used for the model)"
    model.mismatch = mism
    model.quad = merged
    return model


def merge(dump: Config | None, header: Config) -> Config:
    """Dump values win; header values fill gaps (e.g. when only a partial diff was given)."""
    if dump is None:
        return header
    out = Config(
        values=dict(header.values),
        master=dump.master,
        profiles=dump.profiles,
        rateprofiles=dump.rateprofiles,
        features=dump.features,
        active_profile=dump.active_profile,
        active_rateprofile=dump.active_rateprofile,
        profile_known=dump.profile_known,
        firmware_version=dump.firmware_version or header.firmware_version,
        board=dump.board or header.board,
        craft_name=dump.craft_name or header.craft_name,
        source="dump+header",
    )
    out.values.update(dump.values)
    return out
