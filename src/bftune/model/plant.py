"""Parametric rate-axis plant models: pidSum (1000 = full authority) -> gyro [deg/s].

All structures share an integrator-like rigid-body term, a motor lag and a pure delay T
that lumps ESC latency, DShot transport, gyro hardware LPF, SPI/DMA and dual-gyro skew.

  integ_lag        K e^{-sT} / (s (tau s + 1))
  integ_lag2       K e^{-sT} / (s (tau s + 1)(tau2 s + 1))
  pole_lag         K e^{-sT} / ((s + a)(tau s + 1))
  integ_lag_zero   K (tz s + 1) e^{-sT} / (s (tau s + 1))              (yaw: reaction torque)
  integ_lag_zero2  K (tz s + 1) e^{-sT} / (s (tau s + 1)(tau2 s + 1))

K has units deg/s^2 per pidSum unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

STRUCTURES: dict[str, list[str]] = {
    "integ_lag": ["K", "tau", "T"],
    "integ_lag2": ["K", "tau", "tau2", "T"],
    "pole_lag": ["K", "a", "tau", "T"],
    "integ_lag_zero": ["K", "tz", "tau", "T"],
    "integ_lag_zero2": ["K", "tz", "tau", "tau2", "T"],
}


@dataclass
class Plant:
    structure: str
    params: dict[str, float]
    # scheduling reference (conditions at identification)
    ref_motor_hz: float = 0.0
    ref_vbat: float = 0.0
    ref_throttle: float = 0.0
    info: dict = field(default_factory=dict)

    def fr(self, f: np.ndarray) -> np.ndarray:
        return plant_fr(self.structure, self.params, f)

    def scaled(self, motor_hz: float, vbat: float | None = None, motor_model=None) -> Plant:
        """Re-schedule to another operating point (motor speed, battery voltage).

        Torque authority ∝ omega * domega/dcmd (thrust ∝ omega^2). With a measured
        MotorModel both the authority ratio and the motor-lag ratio come from telemetry;
        without one, physics defaults are used: domega/dcmd ∝ V, tau ∝ 1/omega (bounded).
        Battery voltage scales authority linearly (domega/dcmd ∝ V at a given speed).
        """
        p = dict(self.params)
        if self.ref_motor_hz > 0 and motor_hz > 0:
            if motor_model is not None:
                k_ratio = motor_model.authority_ratio(motor_hz, self.ref_motor_hz)
                t_ratio = motor_model.tau_ratio(motor_hz, self.ref_motor_hz)
            else:
                k_ratio = motor_hz / self.ref_motor_hz
                t_ratio = min(max(self.ref_motor_hz / motor_hz, 0.5), 2.0)
            if vbat and self.ref_vbat:
                k_ratio *= vbat / self.ref_vbat  # domega/dcmd ∝ V at a given speed
            p["K"] *= k_ratio
            p["tau"] *= t_ratio
            if "tau2" in p:
                p["tau2"] *= 1.0  # second lag (ESC/prop) not rescaled
        return Plant(self.structure, p, motor_hz, vbat or self.ref_vbat, self.ref_throttle, dict(self.info))


def plant_fr(structure: str, p: dict[str, float], f: np.ndarray) -> np.ndarray:
    s = 2j * np.pi * np.asarray(f, dtype=float)
    K, T, tau = p["K"], p["T"], p["tau"]
    h = K * np.exp(-s * T) / (tau * s + 1)
    if structure == "integ_lag":
        h = h / s
    elif structure == "integ_lag2":
        h = h / (s * (p["tau2"] * s + 1))
    elif structure == "pole_lag":
        h = h / (s + p["a"])
    elif structure == "integ_lag_zero":
        h = h * (p["tz"] * s + 1) / s
    elif structure == "integ_lag_zero2":
        h = h * (p["tz"] * s + 1) / (s * (p["tau2"] * s + 1))
    else:
        raise ValueError(structure)
    return h
