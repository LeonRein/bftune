"""Real-log findings: motor lag under battery sag, control bandwidth from incoherent stick data, idle-axis chirp search."""

import warnings
from types import SimpleNamespace

import numpy as np
from scipy.signal import butter, lfilter

from bftune.sysid.motor import MotorModel, identify_motors

FS = 1000.0


def _motor_flight(tau_of_hz, sag, seed=0, n=240_000):
    """One motor: first-order lag on the applied voltage cmd x Vbat; the battery sags with the command (~100 ms)."""
    rng = np.random.default_rng(seed)
    hold = rng.integers(500, 2000, n // 500)
    lv = np.repeat(0.15 + 0.8 * rng.beta(2, 3, hold.size), hold)[:n]  # mostly cruising, some punch-outs
    u = np.clip(lfilter([0.02], [1, -0.98], lv, zi=[0.5 * 0.98])[0] + 0.03 * rng.standard_normal(n), 0, 1)
    v = 1 - sag * lfilter([0.01], [1, -0.99], u, zi=[0.5 * 0.99])[0]
    hz = np.empty(n)
    hz[0] = 300.0
    for k in range(n - 1):
        hz[k + 1] = hz[k] + (600.0 * u[k] * v[k] - hz[k]) / FS / tau_of_hz(hz[k])
    return SimpleNamespace(n=n, fs=FS, t=np.arange(n) / FS, motor=u[:, None], vbat=16.0 * v, mode=lambda i: np.ones(n, bool),
                           motor_hz=hz[:, None] + 0.5 * rng.standard_normal((n, 1)))


def test_motor_lag_is_not_biased_by_battery_sag():
    # c6 read 46-57 ms at 350-450 Hz (~25 ms elsewhere): the sag during punch-outs is not in the logged command
    m0, m = (identify_motors(_motor_flight(lambda hz: 0.025, sag=s)) for s in (0.0, 0.3))
    np.testing.assert_allclose(m.tau, m0.tau, rtol=0.1)  # was up to 22 % short


def test_motor_bins_slower_than_the_best_sampled_one_at_higher_speed_are_rejected():
    # physically the lag shortens with speed; longer lags up there are large-signal or disturbance-dominated data
    fl = _motor_flight(lambda hz: 0.02 + 2e-4 * max(0.0, hz - 250), sag=0.0, seed=1)
    m, m_all = identify_motors(fl), identify_motors(fl, max_rise=np.inf)
    best = int(np.argmax(m.n))
    assert m_all.tau.max() > 1.25 * m.tau[best] and len(m.tau) < len(m_all.tau)
    assert np.all(m.tau[best:] <= 1.25 * m.tau[best])
    assert m.tau_at(1000.0) <= 1.25 * m.tau[best]


def test_motor_vs_axis_tau_note_checks_the_faster_speeds():
    from bftune.sysid.identify import motor_tau_note

    m = MotorModel(bins_hz=np.array([150.0, 250.0, 450.0]), tau=np.array([0.025, 0.022, 0.055]), gain=np.ones(3) * 500,
                   delay=0.0, n=np.ones(3, int), c0=40.0, c1=0.0, r2=np.ones(3))
    note = motor_tau_note(m, "roll", 0.021, 190.0)  # consistent at the identification speed, not at full throttle
    assert note and "55 ms at 450 Hz" in note
    m.tau[-1] = 0.020
    assert motor_tau_note(m, "roll", 0.021, 190.0) is None


def _stick_flight(stick_hz, seed=0, n=120_000, fn=25.0):
    rng = np.random.default_rng(seed)
    b, a = butter(4, stick_hz / (FS / 2))
    sp = np.zeros((n, 3))
    sp[:, :2] = 200 * lfilter(b, a, rng.standard_normal((n, 2)), axis=0) / np.sqrt(stick_hz / 100)
    bc, ac = butter(2, fn / (FS / 2))  # closed loop setpoint -> gyro: 2nd order, -90 deg at fn
    gy = lfilter(bc, ac, sp, axis=0) + 20 * rng.standard_normal((n, 3))
    return SimpleNamespace(n=n, fs=FS, t=np.arange(n) / FS, setpoint=sp, gyro_unfilt=gy, throttle=np.full(n, 0.3),
                           motor_hz=np.full((n, 4), 180.0), extras={}, mode=lambda i: np.ones(n, bool))


def test_control_bandwidth_uses_only_the_coherent_band(monkeypatch):
    # stick moves that never reach the crossover gave 9-113 Hz from noise phase; now the hover-motor fallback
    import bftune.analysis.logstep as ls
    from bftune.analysis.profile import control_scale

    monkeypatch.setattr(ls, "step_windows", lambda fl, ax: list(range(0, fl.n - 2000, 1000)))
    fc, src = control_scale(_stick_flight(stick_hz=4.0))
    assert fc == 0.11 * 180.0 and "hover motor" in src
    fc, src = control_scale(_stick_flight(stick_hz=150.0))  # broadband sticks: measured, 25 Hz / 1.25
    assert abs(fc - 20.0) < 1.0 and "measured" in src


def test_chirp_search_ignores_an_axis_without_stick_input():
    from bftune.sysid.chirp import _abs_corr

    sp = np.full(1940, -0.3)  # np.std(sp) == 5.6e-17, not 0: the old guard let it through to corrcoef
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert _abs_corr(np.cos(np.arange(1940) / 10), sp) == 0.0
        assert _abs_corr(np.zeros(100), np.arange(100.0)) == 0.0
