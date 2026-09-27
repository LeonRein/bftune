"""Gate holes found in the pre-release audit: each must FAIL (or be refused), never PASS."""

import pickle
from pathlib import Path

import pytest

from bftune.io.dump import Config, parse_dump, reconcile
from bftune.model.params import DEFAULTS

DIFF = """# diff all

# version
# Betaflight / STM32F405 (F405) 2026.6.2 Sep 26 2026 / 13:02:08 (e0b7bb01b) MSP API: 1.48

profile 0
set p_roll = 50
"""


def test_diff_all_leaves_out_defaults():
    cfg = parse_dump(DIFF)
    assert cfg.is_diff and cfg.values["p_roll"] == "50"
    assert cfg.values["d_roll"] == str(DEFAULTS["d_roll"]) and "d_roll" in cfg.defaulted
    assert "pid_process_denom" not in cfg.values  # target-dependent default: not assumed


def test_full_dump_is_not_filled():
    cfg = parse_dump(DIFF.replace("# diff all", "# dump"))
    assert not cfg.is_diff and "d_roll" not in cfg.values


def test_reset_to_default_after_the_log_is_seen():
    """The log flew d_roll 40; the pilot reset it to default, then took a diff all (which omits d_roll)."""
    header = Config(values={"p_roll": "50", "d_roll": "40"}, source="blackbox-header")
    model = reconcile(parse_dump(DIFF), header, {"p_roll", "d_roll"})
    assert model.values["d_roll"] == "40"  # the model uses what flew
    assert model.quad is not None and model.quad.values["d_roll"] == str(DEFAULTS["d_roll"])  # the quad is at default


def _workbench(tmp_path: Path, craft: str, **kw):
    from bftune.pipeline import analyze
    from bftune.synth.quad import CRAFTS, simulate
    from bftune.workbench import Workbench

    fl = simulate(CRAFTS[craft], seed=5, **kw)
    pkl = tmp_path / f"{craft}.pkl"
    pkl.write_bytes(pickle.dumps(fl))
    out = tmp_path / f"out-{craft}"
    analyze(str(pkl), None, out, plots=False, log=lambda *_: None)
    wb = Workbench(out)
    base, _ = wb.load(wb.write_candidate(tmp_path / f"{craft}.txt"))
    return wb, base


@pytest.fixture(scope="module")
def chirp_wb(tmp_path_factory):
    return _workbench(tmp_path_factory.mktemp("chirp"), "3.5inch", chirp_s=10, hover_s=3, freestyle_s=8)


@pytest.mark.slow
def test_no_chirp_gate_checks_newly_enabled_dmax(tmp_path: Path):
    wb, base = _workbench(tmp_path, "5inch", chirp_axes=(), freestyle_s=40)
    assert wb.relative_gate
    t = base.copy().set("d_max_roll", 120).set("d_max_gain", 100).set("d_max_advance", 100)
    a = wb.assess(t, steps=False)
    assert a["verdict"] == "FAIL" and any("dmax" in v for v in a["axes"]["roll"]["violations"])


@pytest.mark.slow
def test_changes_on_an_axis_without_model_fail(chirp_wb):
    wb, base = chirp_wb
    yaw = wb.idn.axes.pop(2)
    try:
        a = wb.assess(base.copy().set("p_yaw", 200), steps=False)
        assert a["verdict"] == "FAIL" and any("p_yaw" in u for u in a["unchecked"])
        b = wb.assess(base.copy().set("p_roll", base.i("p_roll")), steps=False)
        assert not b["unchecked"]
    finally:
        wb.idn.axes[2] = yaw


@pytest.mark.slow
def test_typo_and_out_of_range_fail_and_emit_refuses(chirp_wb, tmp_path: Path):
    wb, base = chirp_wb
    typo = base.copy()
    typo.values["d_max_rol"] = "40"
    assert wb.assess(typo, steps=False)["verdict"] == "FAIL"
    bad = base.copy().set("p_pitch", 300)
    assert wb.assess(bad, steps=False)["verdict"] == "FAIL"
    with pytest.raises(SystemExit, match="p_pitch"):
        wb.emit(bad, dest=tmp_path / "emit", log=lambda *_: None)
    assert not (tmp_path / "emit" / "tune_cli.txt").exists()
