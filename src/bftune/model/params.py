"""Tune parameters: CLI names, firmware defaults, validation against the settings DB."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources

from ..io.dump import Config

# Betaflight 2026.6 defaults for every setting bftune reasons about
# (betaflight/src/main/flight/pid.c pidProfile defaults, sensors/gyro.c, pg/rpm_filter.c,
#  pg/dyn_notch.c, fc/rc.c). Used when a value is missing from both dump and header.
DEFAULTS: dict[str, int | str] = {
    "p_roll": 45, "i_roll": 80, "d_roll": 30, "f_roll": 120,
    "p_pitch": 47, "i_pitch": 84, "d_pitch": 34, "f_pitch": 125,
    "p_yaw": 45, "i_yaw": 80, "d_yaw": 0, "f_yaw": 120,
    "d_max_roll": 40, "d_max_pitch": 46, "d_max_yaw": 0,
    "d_max_gain": 0, "d_max_advance": 35,
    "tpa_mode": "D", "tpa_rate": 65, "tpa_breakpoint": 1350,
    "tpa_low_rate": 20, "tpa_low_breakpoint": 1050, "tpa_low_always": "OFF",
    "gyro_lpf1_type": "PT1", "gyro_lpf1_static_hz": 250,
    "gyro_lpf1_dyn_min_hz": 250, "gyro_lpf1_dyn_max_hz": 500, "gyro_lpf1_dyn_expo": 5,
    "gyro_lpf2_type": "PT1", "gyro_lpf2_static_hz": 500,
    "gyro_notch1_hz": 0, "gyro_notch1_cutoff": 0, "gyro_notch2_hz": 0, "gyro_notch2_cutoff": 0,
    "dterm_lpf1_type": "PT1", "dterm_lpf1_static_hz": 75,
    "dterm_lpf1_dyn_min_hz": 75, "dterm_lpf1_dyn_max_hz": 150, "dterm_lpf1_dyn_expo": 5,
    "dterm_lpf2_type": "PT1", "dterm_lpf2_static_hz": 150,
    "dterm_notch_hz": 0, "dterm_notch_cutoff": 0,
    "yaw_lowpass_hz": 100,
    "rpm_filter_harmonics": 3, "rpm_filter_weights": "100,100,100", "rpm_filter_q": 500,
    "rpm_filter_min_hz": 100, "rpm_filter_fade_range_hz": 50, "rpm_filter_lpf_hz": 150,
    "dyn_notch_count": 3, "dyn_notch_q": 300, "dyn_notch_min_hz": 100, "dyn_notch_max_hz": 600,
    "iterm_relax": "RP", "iterm_relax_type": "SETPOINT", "iterm_relax_cutoff": 15, "iterm_windup": 80,
    "anti_gravity_gain": 80, "anti_gravity_cutoff_hz": 5, "anti_gravity_p_gain": 100,
    "feedforward_transition": 0, "feedforward_averaging": "2_POINT", "feedforward_smooth_factor": 65,
    "feedforward_jitter_factor": 7, "feedforward_boost": 15, "feedforward_max_rate_limit": 90,
    "feedforward_yaw_hold_gain": 15, "feedforward_yaw_hold_time": 100,
    "rc_smoothing": "ON", "rc_smoothing_auto_factor": 30, "rc_smoothing_auto_factor_throttle": 30,
    "rc_smoothing_setpoint_cutoff": 0, "rc_smoothing_throttle_cutoff": 0,
    "dyn_idle_min_rpm": 0, "dyn_idle_p_gain": 50, "dyn_idle_i_gain": 50, "dyn_idle_d_gain": 50,
    "dyn_idle_max_increase": 150,
    "motor_idle": 550, "thrust_linear": 0, "throttle_boost": 5, "throttle_boost_cutoff": 15,
    "pidsum_limit": 500, "pidsum_limit_yaw": 400, "vbat_sag_compensation": 0,
    "motor_output_limit": 100, "use_integrated_yaw": "OFF",
    "simplified_pids_mode": "RPY", "simplified_dterm_filter": "ON", "simplified_gyro_filter": "ON",
    "motor_poles": 14, "motor_kv": 1960, "pid_process_denom": 1, "dshot_bidir": "ON",
}

AXIS = ("roll", "pitch", "yaw")

# Scaling constants (betaflight/src/main/flight/pid.h)
PTERM_SCALE = 0.032029
ITERM_SCALE = 0.244381
DTERM_SCALE = 0.000529
FEEDFORWARD_SCALE = 0.013754
ITERM_RELAX_SETPOINT_THRESHOLD = 40.0


def _load_settings_db(version: str = "2026.6") -> dict:
    with resources.files("bftune.data").joinpath(f"settings_{version}.json").open() as fh:
        return json.load(fh)["settings"]


@dataclass
class Tune:
    """A set of CLI settings (string values as the CLI prints them)."""

    values: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: Config) -> Tune:
        vals = {k: str(v) for k, v in DEFAULTS.items()}
        for k in DEFAULTS:
            v = cfg.get(k)
            if v is not None:
                vals[k] = v
        # every other known setting from the dump/header: lets the agent change any of them with a
        # correct revert value (keys absent here are unknown and are not guessed)
        db = _load_settings_db()
        for k, v in getattr(cfg, "values", {}).items():
            if k not in vals and k in db:
                vals[k] = str(v)
        return cls(vals)

    def copy(self) -> Tune:
        return Tune(dict(self.values))

    def i(self, key: str) -> int:
        return int(str(self.values[key]).split(",")[0])

    def ints(self, key: str) -> list[int]:
        return [int(x) for x in str(self.values[key]).split(",")]

    def s(self, key: str) -> str:
        return str(self.values[key]).upper()

    def set(self, key: str, value) -> Tune:
        self.values[key] = ",".join(str(int(v)) for v in value) if isinstance(value, (list, tuple)) else str(value)
        return self

    def update(self, **kw) -> Tune:
        for k, v in kw.items():
            self.set(k, v)
        return self

    # --- physical gains -----------------------------------------------------------
    def kp(self, axis: int) -> float:
        return PTERM_SCALE * self.i(f"p_{AXIS[axis]}")

    def ki(self, axis: int) -> float:
        k = ITERM_SCALE * self.i(f"i_{AXIS[axis]}")
        if axis == 2 and self.s("use_integrated_yaw") != "ON":
            k *= 2.5
        return k

    def kd(self, axis: int) -> float:
        return DTERM_SCALE * self.i(f"d_{AXIS[axis]}")

    def kd_max(self, axis: int) -> float:
        d = self.i(f"d_{AXIS[axis]}")
        dm = self.i(f"d_max_{AXIS[axis]}")
        return DTERM_SCALE * max(d, dm) if d > 0 else 0.0

    def kf(self, axis: int) -> float:
        return FEEDFORWARD_SCALE * self.i(f"f_{AXIS[axis]}") * 0.01

    def tpa_factor(self, throttle: float, low_active: bool = False) -> float:
        """TPA multiplier (pid.c pidUpdateTpaFactor, multirotor)."""
        bp = min(max((self.i("tpa_breakpoint") - 1000) / 1000.0, 0.0), 0.99)
        rate = self.i("tpa_rate") / 100.0
        if low_active or self.s("tpa_low_always") == "ON":
            low_bp = min(min(max((self.i("tpa_low_breakpoint") - 1000) / 1000.0, 0.01), 1.0), bp)
            if throttle < low_bp:
                return 1.0 - (self.i("tpa_low_rate") / (100.0 * low_bp)) * (low_bp - throttle)
        return 1.0 - rate / (1.0 - bp) * max(throttle - bp, 0.0)

    def dyn_notch_enabled(self, loop_hz: float) -> bool:
        return self.i("dyn_notch_count") > 0 and loop_hz >= 2000


def validate(tune: Tune, keys: list[str] | None = None, version: str = "2026.6") -> list[str]:
    """Return a list of human-readable problems (empty = all values legal)."""
    db = _load_settings_db(version)
    problems = []
    for k in keys or list(tune.values):
        v = tune.values[k]
        meta = db.get(k)
        if meta is None:
            problems.append(f"{k}: unknown setting for firmware {version}")
            continue
        mode = meta.get("mode")
        if mode == "lookup":
            vals = [x.upper() for x in (meta.get("values") or [])]
            if vals and str(v).upper() not in vals:
                hint = " (to disable a lowpass set its *_hz to 0)" if k.endswith("_type") and "lpf" in k else ""
                problems.append(f"{k}={v}: must be one of {vals}{hint}")
        elif mode == "direct":
            try:
                iv = int(v)
            except ValueError:
                problems.append(f"{k}={v}: not an integer")
                continue
            lo, hi = meta.get("min"), meta.get("max")
            if lo is not None and iv < lo or hi is not None and iv > hi:
                problems.append(f"{k}={v}: outside [{lo}, {hi}]")
        elif mode == "array":
            n = len(str(v).split(","))
            if meta.get("length") and n != meta["length"]:
                problems.append(f"{k}={v}: expected {meta['length']} values")
    return problems


def thrust_linear_slope(e_pct: float, motor_cmd: float) -> float:
    """Small-signal gain of the thrust-linearization curve at a given motor command.

    pid.c pidApplyThrustLinearization(): f(x) = x*(1 + e(1-x)(1 + e(1-2x))), applied per motor
    after mixing; throttle is pre-compensated (pidCompensateThrustLinearization) so the
    operating motor command y is preserved and x = c^-1(y) ≈ y - e*y*(1-y). Returns f'(x).
    """
    e = e_pct / 100.0
    if e == 0:
        return 1.0
    y = min(max(motor_cmd, 0.0), 1.0)
    x = y * (1.0 - e * (1.0 - y))
    h = 1e-4
    f = lambda v: v * (1 + e * (1 - v) * (1 + e * (1 - 2 * v)))
    return (f(x + h) - f(x - h)) / (2 * h)
