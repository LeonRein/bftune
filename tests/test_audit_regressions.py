import numpy as np

from bftune.analysis.loop import rc_path_fr
from bftune.emit.cli import cli_block
from bftune.io.bbl import Log
from bftune.io.dump import config_from_headers
from bftune.model.params import Tune


def _log(headers):
    return Log(0, headers, [], np.zeros((0, 0)), [], np.zeros((0, 0)), np.zeros(0), [], {})


def test_header_enums_are_cli_names():
    cfg = config_from_headers(_log({"simplified_pids_mode": "2", "dterm_lpf2_type": "3", "tpa_mode": "0",
                                    "tpa_low_always": "1"}))
    assert cfg.values["simplified_pids_mode"] == "RPY"
    assert cfg.values["dterm_lpf2_type"] == "PT3"
    assert cfg.values["tpa_mode"] == "PD"
    assert cfg.values["tpa_low_always"] == "ON"


def test_no_profile_switch_without_dump():
    old = Tune.from_config(config_from_headers(_log({})))
    new = old.copy().update(p_roll=50)
    txt, rev, _ = cli_block(old, new, profile=None)
    assert "\nprofile " not in txt and "type `profile N`" in txt
    # the old slider state is unknown without a dump: the revert must not guess it
    assert "set simplified_" not in rev and "diff all" in rev


def test_yaw_hold_feedforward_adds_setpoint_term():
    t = Tune.from_config(config_from_headers(_log({})))
    f = np.array([0.5, 2.0])
    _, h_yaw = rc_path_fr(t, f, 250.0, 1 / 8000, axis=2)
    t0 = t.copy().set("feedforward_yaw_hold_gain", 0)
    _, h_plain = rc_path_fr(t0, f, 250.0, 1 / 8000, axis=2)
    assert np.all(np.abs(h_yaw) > np.abs(h_plain))  # hold element adds a (high-passed) setpoint term


def test_thrust_linear_scheduling_independent_of_identification_tune():
    """K holds the identification tune's curve slope at the identification throttle only: the same
    physical quad identified with thrust_linear 0 or 40 must give the same loop at every throttle."""
    from types import SimpleNamespace

    from bftune.model.controller import OperatingPoint
    from bftune.model.params import DEFAULTS, thrust_linear_slope
    from bftune.optimize.search import AxisProblem, Case, Goals

    y_id, K0 = 0.2, 1000.0
    tune = Tune({k: str(v) for k, v in DEFAULTS.items()})
    tune.values["thrust_linear"] = "40"
    cases = [Case(lab, OperatingPoint(throttle=y), {}, "integ_lag", 1.0, False)
             for lab, y in (("idle", 0.02), ("hover", y_id), ("mid", 0.5), ("full", 1.0))]
    B = {}
    for tl in (0, 40):
        idn = SimpleNamespace(dt=1 / 8000, time_scale=1.0, thrust_linear=tl, motor_output_limit=100,
                              axes={0: SimpleNamespace(op=SimpleNamespace(throttle=y_id))})
        K = K0 * thrust_linear_slope(tl, y_id)  # what identification at y_id measures
        for c in cases:
            c.plant_params = {"K": K, "T": 0.002, "tau": 0.02}
        B[tl] = AxisProblem(tune, 0, cases, idn, 8000.0, None, Goals(), None).B
    for b0, b40 in zip(B[0], B[40]):
        np.testing.assert_allclose(b40[0], b0[0], rtol=1e-9)
