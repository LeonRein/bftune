"""Workbench: candidate parsing, value specs, and an end-to-end synthetic session."""

from pathlib import Path

import pytest

from bftune.io.dump import Config
from bftune.model.params import Tune
from bftune.workbench import parse_candidate, parse_values


def test_parse_candidate_keeps_reasons():
    base = Tune.from_config(Config())
    t, why = parse_candidate(base, "set d_roll = 34   # noise limited\nset p_roll=40\n# comment\nprofile 0\n")
    assert t.i("d_roll") == 34 and t.i("p_roll") == 40
    assert why == {"d_roll": "noise limited"}


def test_parse_values():
    assert parse_values("20:30:5") == [20, 25, 30]
    assert parse_values("PT1,PT3") == ["PT1", "PT3"]
    assert parse_values("100,100,100;100,50,100") == ["100,100,100", "100,50,100"]


@pytest.mark.slow
def test_workbench_session_on_synthetic_quad(tmp_path: Path):
    import pickle

    from bftune.pipeline import analyze
    from bftune.synth.quad import CRAFTS, simulate
    from bftune.workbench import Workbench

    fl = simulate(CRAFTS["3.5inch"], chirp_s=10, hover_s=3, freestyle_s=8, seed=3)
    pkl = tmp_path / "twin.pkl"
    pkl.write_bytes(pickle.dumps(fl))
    out = tmp_path / "out"
    analyze(str(pkl), None, out, plots=False, log=lambda *_: None)
    wb = Workbench(out)
    cand = wb.write_candidate(tmp_path / "cand.txt")
    base, _ = wb.load(cand)
    a = wb.assess(base)
    assert set(a["axes"]) == {"roll", "pitch", "yaw"} and a["verdict"] in ("PASS", "FAIL")
    rows = wb.sweep(base, "d_roll", [10, 20])
    assert len(rows) == 2 and "roll" in rows[0] and "pitch" not in rows[0]
    sug = wb.suggest(base, [0], maxiter=8)
    assert sug["roll"]["p"] > 0
    res = wb.emit(base, {}, log=lambda *_: None)
    assert (out / "tune_cli.txt").exists() and (out / "report.md").exists() and res["verdict"] in ("PASS", "FAIL")
