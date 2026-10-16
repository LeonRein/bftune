"""Loop margins against constructed L(f) with known Nyquist geometry."""

import numpy as np

from bftune.analysis.loop import metrics

F = np.geomspace(0.5, 2000, 4000)
S = 2j * np.pi * F


def _phase_loop(ph0, ph1):
    """|L| = 100/f (crossover at 100 Hz); phase moving log-linearly from ph0 (0.5 Hz) to ph1 (100 Hz), in degrees."""
    x = np.log(F / 0.5) / np.log(200.0)
    return 100.0 / F * np.exp(1j * np.radians(ph0 + (ph1 - ph0) * x))


def test_single_crossover():
    wc, tau = 2 * np.pi * 50, 0.002
    m = metrics(F, wc / S * np.exp(-S * tau))  # PM = 90° - wc*tau = 54°, delay margin 5 - 2 = 3 ms
    assert m.ok and abs(m.fc - 50) < 0.2 and abs(m.pm - 54.0) < 0.3 and abs(m.dm_ms - 3.0) < 0.03
    assert m.gm_db > 0 and m.ms < 2


def _bump_loop(wn_hz, zp, tau):
    """Crossover near 50 Hz plus a resonant bump (a D-term peak) at wn_hz that can lift |L| above 1 again."""
    wn = 2 * np.pi * wn_hz
    return 2 * np.pi * 50 / S * np.exp(-S * tau) * (S**2 + 1.2 * wn * S + wn**2) / (S**2 + 2 * zp * wn * S + wn**2)


def _unit_circle_angle(L):
    near = np.abs(np.log(np.abs(L))) < 0.02  # points on |L| = 1
    return np.min(180 - np.abs(np.degrees(np.angle(L[near]))))  # their smallest angle to -1


def test_late_crossing_far_from_minus_one_does_not_hide_the_main_one():
    """The bump at 300 Hz grazes |L| = 1 with the phase wrapped to about -430° (108° from -1); the old code took
    that last crossing as the crossover and reported PM 108°. The main crossover (55°) is the limiting one."""
    L = _bump_loop(300, 0.05, 0.0025)
    m = metrics(F, L)
    assert m.ok and abs(m.pm - _unit_circle_angle(L)) < 1.0 and abs(m.pm - 55) < 2
    assert m.fc > 300  # fc stays the last downward crossing


def test_late_crossing_close_to_minus_one_limits_pm():
    L = _bump_loop(400, 0.05, 0.0003)
    m = metrics(F, L)
    assert m.ok and abs(m.pm - _unit_circle_angle(L)) < 2.0 and m.pm < 10
    assert m.ms > 1 / (2 * np.sin(np.radians(m.pm) / 2)) - 0.05  # |1+L| = 2 sin(PM/2) at that crossing


def test_phase_lead_past_90_at_crossover():
    """Phase +119° at |L| = 1 without ever passing -180 (only the positive real axis): stable, 61° from -1.
    The old mod-360 wrap made this -61°."""
    m = metrics(F, _phase_loop(-150.0, 119.0))
    assert m.ok and abs(m.pm - 61.0) < 0.5 and m.dm_ms > 0


def test_phase_lag_past_180_at_crossover_is_unstable():
    """The same point on the unit circle reached the other way (-241°, crossing the axis left of -1): unstable."""
    m = metrics(F, _phase_loop(-150.0, -241.0))
    assert not m.ok and abs(m.pm + 61.0) < 0.5 and m.dm_ms == 0 and m.gm_db < 0


def test_unstable_high_gain_with_delay():
    wc, tau = 2 * np.pi * 50, 0.008  # wc*tau = 144°: the phase passes -180 well before the crossover
    L = wc / S * np.exp(-S * tau) * (1 + 2 * np.pi * 5 / S)  # with an I-term (double integrator at low f)
    m = metrics(F, L)
    assert not m.ok and m.pm < 0 and m.gm_db < 0 and m.dm_ms == 0


def test_conditionally_stable_low_frequency_dip():
    """Triple integrator with a double lead: the phase starts at -270 (above the axis, |L| > 1), rises above -180
    while |L| > 1, then crosses over. Stable; the dip only limits how far the gain may fall."""
    z = 2 * np.pi * 5
    L = 2 * np.pi * 50 * (1 + S / z) ** 2 / S * (z / S) ** 2 / (1 + S / (2 * np.pi * 300)) ** 2
    m = metrics(F, L)
    assert m.ok and m.pm > 30 and m.gm_down_db > 0 and m.gm_db > 0
