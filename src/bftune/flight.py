"""Flight: a decoded log in physical units, plus the merged configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .io.bbl import Log, decode
from .io.dump import Config, config_from_headers, load_dump, reconcile

AXES = ("roll", "pitch", "yaw")

# rcModeActivationMask bits (betaflight/src/main/fc/rc_modes.h, boxId_e)
BOX_ARM = 0
BOX_ANGLE = 1
BOX_HORIZON = 2
BOX_CHIRP = 6

DEBUG_CHIRP_NAME = "CHIRP"


@dataclass
class Flight:
    log: Log
    cfg: Config
    t: np.ndarray  # seconds since log start
    fs: float  # effective blackbox sample rate [Hz] (measured, not nominal)
    loop_hz: float  # nominal PID loop rate [Hz]
    log_ratio: int  # PID loops per logged frame
    gyro: np.ndarray  # (N,3) deg/s, fully filtered (what the PID uses)
    gyro_unfilt: np.ndarray  # (N,3) deg/s, before software filters
    setpoint: np.ndarray  # (N,3) deg/s, includes chirp excitation
    throttle: np.ndarray  # (N,) mixer throttle 0..1
    P: np.ndarray
    I: np.ndarray
    D: np.ndarray
    F: np.ndarray
    motor: np.ndarray  # (N,M) normalized 0..1 over motorOutput range
    motor_raw: np.ndarray  # (N,M) DShot command value
    motor_hz: np.ndarray | None  # (N,M) mechanical rotation frequency [Hz]
    vbat: np.ndarray  # (N,) volts
    rc: np.ndarray  # (N,4) rcCommand
    debug: np.ndarray  # (N,8)
    mode_mask: np.ndarray  # (N,) rcModeActivationMask
    extras: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.t)

    @property
    def pidsum(self) -> np.ndarray:
        return self.P + self.I + self.D + self.F

    def mode(self, box: int) -> np.ndarray:
        return (self.mode_mask >> box) & 1 == 1

    @property
    def debug_mode(self) -> int:
        return self.log.header_int("debug_mode", 0) or 0

    @property
    def motor_poles(self) -> int:
        return self.cfg.int("motor_poles", 14)

    @property
    def cell_count(self) -> int:
        v = float(np.percentile(self.vbat, 95)) if self.n else 0.0
        return max(1, int(round(v / 4.2 + 0.25)))


def _mode_mask_per_frame(log: Log) -> np.ndarray:
    n = log.main.shape[0]
    mask = np.zeros(n, dtype=np.int64)
    if log.slow.size and "flightModeFlags" in log.slow_names:
        col = log.slow[:, log.slow_names.index("flightModeFlags")]
        for at, v in zip(log.slow_at, col):
            mask[at:] = v
    # FLIGHTMODE events are more precise; apply in order
    for ev in log.events:
        if ev["type"] == 30:
            mask[min(ev["at"], n) :] = ev["flags"]
    return mask


def load_flight(bbl_path: str | Path, dump_path: str | Path | None = None, log_index: int | None = None) -> Flight:
    if str(bbl_path).endswith(".pkl"):  # synthetic flight from `bftune synth`
        import pickle

        with open(bbl_path, "rb") as fh:
            fl = pickle.load(fh)
        if dump_path:
            fl.cfg = reconcile(load_dump(str(dump_path)), fl.cfg, tuning_keys())
        return fl
    logs = decode(str(bbl_path))
    if log_index is None:
        log_index = int(np.argmax([lg.main.shape[0] for lg in logs]))
    log = logs[log_index]
    hdr_cfg = config_from_headers(log)
    cfg = reconcile(load_dump(str(dump_path)) if dump_path else None, hdr_cfg, tuning_keys())
    return flight_from_log(log, cfg)


def motor_saturated(fl: Flight) -> np.ndarray:
    """(N,) True where any motor sits at its maximum command: the motorOutput range scaled by motor_output_limit
    (mixer.c), so it also works with a limit below 100 % or a non-DShot protocol."""
    if fl.motor.size == 0:
        return np.zeros(fl.n, bool)
    lim = fl.cfg.int("motor_output_limit", 100) / 100.0 if fl.cfg is not None else 1.0
    return np.any(fl.motor >= 0.995 * lim, axis=1)


def exclude(fl: Flight, windows: list[tuple[float, float]]) -> Flight:
    """Treat these time windows (s) as disarmed, so every flight-data analysis skips them (a crash, a bench test)."""
    for t0, t1 in windows or []:
        sel = (fl.t >= t0) & (fl.t < t1)
        fl.mode_mask = np.where(sel, fl.mode_mask & ~np.int64(1 << BOX_ARM), fl.mode_mask)
    return fl


def tuning_keys() -> set[str]:
    """Settings that shape flight behaviour (model inputs and everything the agent may tune)."""
    from .coverage import FEATURES
    from .model.params import DEFAULTS

    return set(DEFAULTS) | {k for f in FEATURES for k in f.keys}


def flight_from_log(log: Log, cfg: Config) -> Flight:
    col = log.col
    t_us = col("time").astype(np.float64)
    t = (t_us - t_us[0]) * 1e-6
    dt = np.diff(t)
    fs = 1.0 / float(np.median(dt[dt > 0])) if len(dt) else 1000.0
    looptime = log.header_int("looptime", 125) or 125
    pid_denom = log.header_int("pid_process_denom", 1) or 1
    loop_hz = 1e6 / (looptime * pid_denom)
    p_int = log.headers.get("P interval", "1")
    log_ratio = int(p_int.split("/")[-1]) if p_int else 1

    hr = 10.0 if log.headers.get("blackbox_high_resolution", "0").strip() in ("1", "ON") else 1.0

    def tri(prefix: str, scale: float = 1.0) -> np.ndarray:
        out = np.zeros((log.main.shape[0], 3))
        for i in range(3):
            name = f"{prefix}[{i}]"
            if log.has(name):
                out[:, i] = col(name) / scale
        return out

    motor_names = [n for n in log.main_names if n.startswith("motor[")]
    motor_raw = np.stack([col(n) for n in motor_names], axis=1).astype(float) if motor_names else np.zeros((len(t), 0))
    mo = log.header_ints("motorOutput") or [0, 2047]
    motor = (motor_raw - mo[0]) / max(1, mo[1] - mo[0])

    erpm_names = [n for n in log.main_names if n.startswith("eRPM[")]
    poles = cfg.int("motor_poles", 14)
    motor_hz = None
    if erpm_names:
        erpm = np.stack([col(n) for n in erpm_names], axis=1).astype(float) * 100.0
        motor_hz = erpm / 60.0 / (poles / 2.0)

    vbat = col("vbatLatest") / 100.0 if log.has("vbatLatest") else np.zeros(len(t))
    rc = np.stack([col(f"rcCommand[{i}]") for i in range(4)], axis=1) / hr
    debug = np.stack([col(f"debug[{i}]") if log.has(f"debug[{i}]") else np.zeros(len(t)) for i in range(8)], axis=1)
    throttle = col("setpoint[3]") / 1000.0 if log.has("setpoint[3]") else (rc[:, 3] - 1000) / 1000

    return Flight(
        log=log,
        cfg=cfg,
        t=t,
        fs=fs,
        loop_hz=loop_hz,
        log_ratio=log_ratio,
        gyro=tri("gyroADC", hr),
        gyro_unfilt=tri("gyroUnfilt", hr),
        setpoint=tri("setpoint", hr),
        throttle=throttle,
        P=tri("axisP"),
        I=tri("axisI"),
        D=tri("axisD"),
        F=tri("axisF"),
        motor=motor,
        motor_raw=motor_raw,
        motor_hz=motor_hz,
        vbat=vbat,
        rc=rc,
        debug=debug,
        mode_mask=_mode_mask_per_frame(log),
    )


@dataclass
class FlightSummary:
    """The few per-flight facts the tuning tools need, cached with the analysis.

    Lets `evaluate`/`sweep`/`suggest`/`emit` run in about a second without re-decoding the log.
    """

    loop_hz: float
    fs: float
    log_ratio: int
    cfg: Config
    headers: dict
    thr_grid: np.ndarray  # throttle bin centres
    hz_grid: np.ndarray  # (T, M) median motor Hz per bin (NaN where too little data)
    sq_fit: np.ndarray  # polyfit(throttle, mean_hz^2, 1) for extrapolation
    n_motors: int
    idle_q: dict  # percentile -> idle motor Hz (armed, throttle < 3 %)

    def header_int(self, key: str, default: int = 0) -> int:
        v = self.headers.get(key)
        try:
            return int(str(v).split(",")[0]) if v is not None else default
        except ValueError:
            return default

    def motor_hz_at(self, thr: float) -> np.ndarray:
        """Median motor speed at a mixer throttle (measured bin within ±0.04, else extrapolated)."""
        k = int(np.argmin(np.abs(self.thr_grid - thr))) if len(self.thr_grid) else -1
        if k >= 0 and abs(self.thr_grid[k] - thr) <= 0.04 and np.all(np.isfinite(self.hz_grid[k])):
            return self.hz_grid[k].copy()
        hz = float(np.sqrt(max(np.polyval(self.sq_fit, thr), 1.0)))
        return np.full(self.n_motors, hz)

    def idle_hz(self, pct: int = 20) -> float:
        return float(self.idle_q.get(pct, 60.0))


def summarize(fl: Flight) -> FlightSummary:
    armed = fl.mode(0)
    grid = np.round(np.arange(0.0, 1.0001, 0.02), 2)
    m = fl.motor_hz.shape[1] if fl.motor_hz is not None else 4
    hz = np.full((len(grid), m), np.nan)
    sq = np.array([0.0, 200.0**2])
    idle_q: dict = {}
    if fl.motor_hz is not None:
        for i, g in enumerate(grid):
            sel = armed & (np.abs(fl.throttle - g) < 0.04)
            if sel.sum() > 100:
                hz[i] = np.median(fl.motor_hz[sel], axis=0)
        ok = armed & (fl.throttle > 0.05)
        if ok.sum() > 10:
            sq = np.polyfit(fl.throttle[ok], fl.motor_hz[ok].mean(axis=1) ** 2, 1)
        low = armed & (fl.throttle < 0.03)
        if low.sum() > 100:
            mh = fl.motor_hz[low].mean(axis=1)
            idle_q = {q: float(np.percentile(mh, q)) for q in (5, 10, 20, 25, 50)}
    return FlightSummary(
        loop_hz=fl.loop_hz, fs=fl.fs, log_ratio=fl.log_ratio, cfg=fl.cfg, headers=dict(fl.log.headers),
        thr_grid=grid, hz_grid=hz, sq_fit=np.asarray(sq), n_motors=m, idle_q=idle_q,
    )
