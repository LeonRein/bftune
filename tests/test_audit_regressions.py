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
    txt, _, _ = cli_block(old, new, profile=None)
    assert "\nprofile " not in txt and "select the profile" in txt


def test_yaw_hold_feedforward_adds_setpoint_term():
    t = Tune.from_config(config_from_headers(_log({})))
    f = np.array([0.5, 2.0])
    _, h_yaw = rc_path_fr(t, f, 250.0, 1 / 8000, axis=2)
    t0 = t.copy().set("feedforward_yaw_hold_gain", 0)
    _, h_plain = rc_path_fr(t0, f, 250.0, 1 / 8000, axis=2)
    assert np.all(np.abs(h_yaw) > np.abs(h_plain))  # hold element adds a (high-passed) setpoint term
