"""End-to-end: synthetic quad with known plant -> chirp log -> identification recovers it."""

import dataclasses

import numpy as np
import pytest

from bftune.sysid.identify import identify
from bftune.synth.quad import CRAFTS, default_tune, simulate, true_plant

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("craft", ["3.5inch", "10inch"])
def test_identification_recovers_plant(craft):
    spec = CRAFTS[craft]
    f1 = 150.0 if spec.loop_hz >= 8000 else 100.0
    fl = simulate(spec, chirp_s=10, hover_s=3, f0=1.0, f1=f1, seed=1)
    idn = identify(fl, default_tune(spec))
    for axis in range(3):
        truth = true_plant(spec, axis).params
        est = idn.axes[axis].plant.params
        assert idn.axes[axis].chain.passed
        if axis == 2:
            # the yaw zero (1/(2π tz) ≈ 0.6-1.5 Hz) lies below the fit band, so only the
            # in-band gain K*tz is identifiable; that is what the controller sees.
            assert abs(est["K"] * est["tz"] / (truth["K"] * truth["tz"]) - 1) < 0.12, (axis, est, truth)
        else:
            assert abs(est["K"] / truth["K"] - 1) < 0.12, (axis, est, truth)
        assert abs(est["tau"] / truth["tau"] - 1) < 0.2, (axis, est, truth)
        assert abs(est["T"] - truth["T"]) < 0.0006, (axis, est, truth)


def test_chirp_reconstruction_without_debug_fields():
    spec = CRAFTS["3.5inch"]
    fl = simulate(spec, chirp_axes=(0,), chirp_s=10, hover_s=2, f0=1.0, f1=150.0, seed=2)
    fl.log.headers["debug_mode"] = "0"  # pretend debug_mode was not CHIRP
    fl2 = dataclasses.replace(fl, debug=np.zeros_like(fl.debug))
    idn = identify(fl2, default_tune(spec))
    assert idn.runs and idn.runs[0].reconstructed
    est, truth = idn.axes[0].plant.params, true_plant(spec, 0).params
    assert abs(est["K"] / truth["K"] - 1) < 0.12
