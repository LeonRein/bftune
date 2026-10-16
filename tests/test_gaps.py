"""Blackbox gaps (dropped frames, corrupt sections): data across a gap is never treated as continuous."""

import copy
from types import SimpleNamespace

import numpy as np
import pytest

from bftune.gaps import continuous, gaps, segments


def test_segments_split_at_gaps_and_drop_the_frames_around_them():
    t = np.concatenate([np.arange(100), 150 + np.arange(100)]) * 1e-3  # 50 frames missing after frame 99
    fl = SimpleNamespace(t=t, n=len(t))
    assert gaps(t).tolist() == [99]
    assert not continuous(fl)[99] and not continuous(fl)[100] and continuous(fl).sum() == 198
    assert segments(fl, np.ones(200, bool), 10) == [(0, 99), (101, 200)]


def _drop(fl, keep: np.ndarray):
    """The flight as the logger would have written it with frames missing (every per-frame array cut)."""
    out = copy.copy(fl)
    for k, v in vars(fl).items():
        if isinstance(v, np.ndarray) and v.shape[:1] == (fl.n,):
            setattr(out, k, v[keep])
    out.log = copy.copy(fl.log)
    out.log.main = fl.log.main[keep]
    return out


@pytest.fixture(scope="module")
def twin():
    from bftune.synth.quad import CRAFTS, default_tune, simulate

    spec = CRAFTS["3.5inch"]
    tune = default_tune(spec)
    return simulate(spec, tune, chirp_axes=(0,), chirp_repeats=3, chirp_s=6, hover_s=2, freestyle_s=8, seed=3), tune


def _gapped(fl, run):
    """Drop 40 frames every 0.3 s inside one chirp round and corrupt the frame before each gap (as the decoder does
    after a damaged stream: one frame of garbage, then a resync)."""
    keep = np.ones(fl.n, bool)
    starts = np.arange(run.start + 200, run.end - 200, int(0.3 * fl.fs))
    for s in starts:
        keep[s : s + 40] = False
    bad = fl.motor_hz.copy(), fl.gyro_unfilt.copy()
    bad[0][starts - 1] = -1600.0
    bad[1][starts - 1] = 1500.0
    out = copy.copy(fl)
    out.motor_hz, out.gyro_unfilt = bad
    return _drop(out, keep)


@pytest.mark.slow
def test_gap_in_one_chirp_round_leaves_uncertainty_and_motor_lag_unchanged(twin):
    from bftune.sysid.chirp import find_chirps
    from bftune.sysid.identify import identify
    from bftune.sysid.motor import identify_motors

    fl, tune = twin
    ref = identify(fl, tune)
    gl = _gapped(fl, ref.runs[1])
    assert [r.gaps > 0 for r in find_chirps(gl)] == [False, True, False]
    idn = identify(gl, tune)
    assert len(idn.runs) == 2 and any("blackbox gaps" in n for n in idn.notes)
    assert idn.uncertainty["k_hi"] == pytest.approx(ref.uncertainty["k_hi"], abs=0.02)
    assert idn.axes[0].plant.params["K"] == pytest.approx(ref.axes[0].plant.params["K"], rel=0.05)
    m0, m1 = identify_motors(fl), identify_motors(gl)
    hz = np.median(m0.bins_hz)
    assert float(m1.tau_at(hz)) == pytest.approx(float(m0.tau_at(hz)), rel=0.1)


@pytest.mark.slow
def test_one_corrupt_chirp_round_spoils_neither_the_model_nor_the_gain_spread(twin):
    from bftune.sysid.identify import identify

    fl, tune = twin
    ref = identify(fl, tune)
    r = ref.runs[1]
    bad = copy.copy(fl)
    for k in ("P", "gyro_unfilt", "gyro"):  # that round's data no longer follows its chirp (corrupt section, crash)
        x = getattr(fl, k).copy()
        x[r.start : r.end, 0] = np.roll(x[r.start : r.end, 0], int(0.5 * fl.fs))
        setattr(bad, k, x)
    idn = identify(bad, tune)
    assert any("1 of 3 chirp rounds incoherent" in n for n in idn.notes)
    assert len(idn.axes[0].round_gains) == 2
    assert idn.uncertainty["k_hi"] == pytest.approx(ref.uncertainty["k_hi"], abs=0.02)
    assert idn.axes[0].plant.params["K"] == pytest.approx(ref.axes[0].plant.params["K"], rel=0.05)
