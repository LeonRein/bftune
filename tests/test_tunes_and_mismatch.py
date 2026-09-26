"""Dump/log reconciliation, `tunes` grouping and `candidate --set`."""

import pytest

from bftune.flight import tuning_keys
from bftune.io.dump import Config, reconcile


def _cfg(values, source):
    return Config(values=dict(values), source=source)


def test_matching_dump_is_merged_normally():
    hdr = _cfg({"p_roll": "38", "dterm_lpf2_type": "PT1"}, "blackbox-header")
    dump = _cfg({"p_roll": "38", "dterm_lpf2_type": "pt1", "craft_name": "x"}, "dump")
    cfg = reconcile(dump, hdr, tuning_keys())
    assert not cfg.mismatch and cfg.quad is None and cfg.values["craft_name"] == "x"


def test_mismatched_dump_keeps_log_settings_for_the_model():
    hdr = _cfg({"p_roll": "38", "dterm_lpf2_type": "PT1", "tpa_mode": "D"}, "blackbox-header")
    dump = _cfg({"p_roll": "40", "dterm_lpf2_type": "PT3", "tpa_mode": "D"}, "dump")
    cfg = reconcile(dump, hdr, tuning_keys())
    assert cfg.values["p_roll"] == "38" and cfg.values["dterm_lpf2_type"] == "PT1"  # what flew
    assert cfg.quad.values["p_roll"] == "40" and cfg.quad.values["dterm_lpf2_type"] == "PT3"  # on the quad
    assert {k for k, _, _ in cfg.mismatch} == {"p_roll", "dterm_lpf2_type"}


def test_no_dump():
    hdr = _cfg({"p_roll": "38"}, "blackbox-header")
    assert reconcile(None, hdr, tuning_keys()) is hdr


@pytest.mark.slow
def test_mismatched_dump_end_to_end(tmp_path):
    import pickle

    from bftune.pipeline import analyze
    from bftune.synth.quad import CRAFTS, simulate
    from bftune.workbench import Workbench, parse_candidate

    fl = simulate(CRAFTS["3.5inch"], chirp_s=8, hover_s=2, freestyle_s=6, seed=4)
    pkl = tmp_path / "twin.pkl"
    pkl.write_bytes(pickle.dumps(fl))
    flown_p = fl.cfg.values.get("p_roll") or "38"
    dump = tmp_path / "diff.txt"
    dump.write_text(f"# diff all\nprofile 0\nset p_roll = {int(flown_p) + 7}\nset thrust_linear = 30\n")
    out = tmp_path / "A"
    an = analyze(str(pkl), str(dump), out, plots=False, log=lambda *_: None)
    assert an.quad_tune is not None and an.tune.i("p_roll") == int(flown_p)
    assert an.quad_tune.i("p_roll") == int(flown_p) + 7
    wb = Workbench(out)
    assert all(ai.chain.passed for ai in wb.idn.axes.values())  # model built from what flew
    cand, why = parse_candidate(wb.on_quad, "set f_roll = 140  # less lag\n")
    res = wb.emit(cand, why, log=lambda *_: None)
    cli = (out / "tune_cli.txt").read_text()
    assert "set f_roll = 140" in cli and "set p_roll" not in cli and "thrust_linear" not in cli  # relative to quad
    assert "on_quad" in res["assessment"]


def test_firmware_support_levels():
    from bftune.pipeline import firmware_support

    assert firmware_support("2026.6.2")[0] == "ok"
    assert firmware_support("2027.1.0")[0] == "newer"
    assert firmware_support("4.5.1")[0] == "older"
    assert firmware_support(None)[0] == "unknown"
