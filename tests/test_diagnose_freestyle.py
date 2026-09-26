"""Diagnosis heuristics, no-chirp (freestyle) identification and the project helper."""

from pathlib import Path

import numpy as np
import pytest

from bftune.analysis.diagnose import diagnose, format_findings
from bftune.project import init_project, next_tune_dir


def test_project_init_and_tune_dirs(tmp_path: Path):
    made = init_project(tmp_path / "q", "My Quad")
    assert {p.name for p in made} == {"quad.md", "history.md"}
    assert "# My Quad" in (tmp_path / "q" / "quad.md").read_text()
    assert init_project(tmp_path / "q") == []  # never overwrites the agent's memory
    d1 = next_tune_dir(tmp_path / "q")
    assert d1.name.startswith("01-")
    d1.mkdir()
    assert next_tune_dir(tmp_path / "q").name.startswith("02-")


@pytest.fixture(scope="module")
def twin_no_chirp():
    from bftune.synth.quad import CRAFTS, simulate

    return simulate(CRAFTS["3.5inch"], chirp_axes=(), hover_s=4, freestyle_s=40, seed=5)


@pytest.mark.slow
def test_diagnose_on_clean_twin(twin_no_chirp):
    res = diagnose(twin_no_chirp)
    ids = {f["id"] for f in res}
    assert "no_chirp" in ids
    assert all(f["severity"] in ("info", "warn", "problem") for f in res)
    # a well-damped synthetic tune must not raise a resonance problem or a desync
    assert not any(f["id"].startswith("resonance") and f["severity"] == "problem" for f in res)
    assert "possible_desync" not in ids
    assert "no_chirp" in format_findings(res)


@pytest.mark.slow
def test_freestyle_identification_gain(twin_no_chirp):
    from bftune.model.params import Tune
    from bftune.synth.quad import CRAFTS, true_plant
    from bftune.sysid.identify import identify

    fl = twin_no_chirp
    idn = identify(fl, Tune.from_config(fl.cfg))
    assert idn.source == "freestyle" and idn.uncertainty["k_hi"] > 1.1
    for axis in (0, 1):
        k = idn.axes[axis].plant.params["K"]
        k_true = true_plant(CRAFTS["3.5inch"], axis).params["K"]
        assert abs(np.log(k / k_true)) < np.log(1.6), (axis, k, k_true)  # low confidence by design
