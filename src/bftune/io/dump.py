"""Parse Betaflight CLI `dump` / `diff all` output and blackbox headers into one config.

The result is a flat ``Config`` mapping CLI setting names to string values for the
*active* PID profile and rate profile, plus the raw per-profile sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    firmware_version: str | None = None
    board: str | None = None
    craft_name: str | None = None
    source: str = ""

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


def parse_dump(text: str) -> Config:
    cfg = Config(source="dump")
    section: dict[str, str] = cfg.master
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
    # `dump` prints the active profile selection via "set profile"? no: the last one listed
    # with `dump all` is re-selected at the end by "profile N"; handle both.
    cfg.values = dict(cfg.master)
    cfg.values.update(cfg.profiles.get(cfg.active_profile, {}))
    cfg.values.update(cfg.rateprofiles.get(cfg.active_rateprofile, {}))
    return cfg


def load_dump(path: str) -> Config:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_dump(fh.read())


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
        firmware_version=dump.firmware_version or header.firmware_version,
        board=dump.board or header.board,
        craft_name=dump.craft_name or header.craft_name,
        source="dump+header",
    )
    out.values.update(dump.values)
    return out
