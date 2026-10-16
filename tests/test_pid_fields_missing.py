"""blackbox_disable_pids = ON: no axisP/I/D/F in the log. pidsum is rebuilt from the motor outputs (inverse mixer)."""

import dataclasses

import numpy as np
import pytest

from bftune.flight import QUAD_X, Flight, pidsum_from_motors
from bftune.io.bbl import Log
from bftune.io.dump import Config
from bftune.noise.model import measure
from bftune.pipeline import sanity_warnings
from bftune.synth.quad import CRAFTS, default_tune, simulate
from bftune.sysid.identify import identify


def test_mixer_inverse_with_thrust_linear():
    """Forward Betaflight mixer (mixer.c, pid.c pidApplyThrustLinearization, yaw negated) then invert it."""
    rng = np.random.default_rng(0)
    n, e, lo, hi = 4000, 0.4, 48, 2047
    t = np.arange(n) / 1000.0
    err = rng.normal(0, 15, (n, 3))
    u = 2.0 * err + np.array([20.0, -10.0, 15.0])  # P on the tracking error plus an I offset
    x = 0.3 + (u * [1, 1, -1] / 1000.0) @ QUAD_X.T
    motor = x * (1 + e * (1 - x) * (1 + e * (1 - 2 * x)))
    raw = np.round(lo + motor * (hi - lo))
    log = Log(0, {"motorOutput": f"{lo},{hi}"}, [], np.zeros((n, 0)), [], np.zeros((0, 0)), np.zeros(0), [], {})
    z = np.zeros((n, 3))
    fl = Flight(log=log, cfg=Config(values={"thrust_linear": "40"}), t=t, fs=1000.0, loop_hz=8000.0, log_ratio=8,
                gyro=-err, gyro_unfilt=-err, setpoint=z, throttle=np.full(n, 0.3), P=z, I=z, D=z, F=z,
                motor=(raw - lo) / (hi - lo), motor_raw=raw, motor_hz=None, vbat=np.full(n, 16.0), rc=np.zeros((n, 4)),
                debug=np.zeros((n, 8)), mode_mask=np.ones(n, np.int64), pid_logged=False)
    rec, clipped = pidsum_from_motors(fl)
    assert not clipped.any()
    assert np.max(np.abs(rec - u)) < 1.0  # motor command quantisation only


@pytest.mark.slow
def test_identification_without_pid_fields_matches_logged():
    spec = CRAFTS["5inch"]
    tune = default_tune(spec)
    fl = simulate(spec, tune, chirp_s=10, hover_s=3, freestyle_s=0, seed=3)
    z = np.zeros_like(fl.P)
    nopid = dataclasses.replace(fl, P=z, I=z, D=z, F=z, pid_logged=False)
    nopid.pidsum_motors, nopid.mixer_clipped = pidsum_from_motors(nopid)

    ok = fl.mode(0) & ~nopid.mixer_clipped
    lim = np.array([500, 500, 400])
    err = nopid.pidsum[ok] - np.clip(fl.pidsum[ok], -lim, lim)
    assert np.all(np.sqrt(np.mean(err**2, axis=0)) < 0.05 * fl.pidsum[ok].std(axis=0) + 1.0)

    ref, rec = identify(fl, tune), identify(nopid, tune)
    for axis in range(3):
        a, b = ref.axes[axis], rec.axes[axis]
        assert b.chain.passed
        f = np.geomspace(a.fit_band[0], a.fit_band[1], 30)
        ratio = b.plant.fr(f) / a.plant.fr(f)
        assert np.max(np.abs(np.abs(ratio) - 1)) < 0.05, (axis, np.abs(ratio).round(3))
        assert np.max(np.abs(np.degrees(np.angle(ratio)))) < 3.0, (axis, np.degrees(np.angle(ratio)).round(1))

    assert any("blackbox_disable_pids = OFF" in w for w in sanity_warnings(nopid, None))
    bands = measure(nopid)
    assert bands and all(np.all(np.isnan(b.var_d)) for b in bands)
