"""End-to-end: synthetic quad with known plant -> chirp log -> identification recovers it."""

import dataclasses

import numpy as np
import pytest

from bftune.synth.quad import CRAFTS, default_tune, simulate, true_plant
from bftune.sysid.identify import identify

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("craft", ["whoop65", "3.5inch", "5inch", "10inch"])
def test_identification_recovers_plant(craft):
    spec = CRAFTS[craft]
    tune = default_tune(spec)
    fl = simulate(spec, tune, chirp_s=12, hover_s=3, freestyle_s=6, seed=1)
    idn = identify(fl, tune)
    chirp = np.concatenate([np.arange(r.start, r.end) for r in idn.runs])
    assert np.mean(np.any(fl.motor_raw[chirp] >= 2046, axis=1)) < 0.02, "twin saturates during chirps"
    for axis in range(3):
        truth = true_plant(spec, axis).params
        est = idn.axes[axis].plant.params
        assert idn.axes[axis].chain.passed
        if axis == 2:
            # the yaw zero (1/(2π tz) ≈ 0.5-4 Hz) can lie below the fit band, so the in-band gain K*tz
            # is what is identifiable (and what the controller sees)
            assert abs(est["K"] * est["tz"] / (truth["K"] * truth["tz"]) - 1) < 0.12, (axis, est, truth)
        else:
            assert abs(est["K"] / truth["K"] - 1) < 0.10, (axis, est, truth)
        # motor lag and delay trade off when the coherent band is short (whoops, big quads);
        # what the loop design needs is the phase inside the identified band: check that.
        ai = idn.axes[axis]
        f = np.geomspace(ai.fit_band[0], ai.fit_band[1], 30)
        ph_err = np.degrees(np.angle(ai.plant.fr(f) / true_plant(spec, axis).fr(f)))
        assert np.max(np.abs(ph_err)) < 8.0, (axis, craft, ph_err.round(1))
        assert abs(est["tau"] + est["T"] - truth["tau"] - truth["T"]) / (truth["tau"] + truth["T"]) < 0.15


def test_chirp_reconstruction_without_debug_fields():
    spec = CRAFTS["3.5inch"]
    tune = default_tune(spec)
    fl = simulate(spec, tune, chirp_axes=(0,), chirp_s=10, hover_s=2, freestyle_s=0, seed=2)
    fl.log.headers["debug_mode"] = "0"  # pretend debug_mode was not CHIRP
    fl2 = dataclasses.replace(fl, debug=np.zeros_like(fl.debug))
    idn = identify(fl2, tune)
    assert idn.runs and idn.runs[0].reconstructed
    est, truth = idn.axes[0].plant.params, true_plant(spec, 0).params
    assert abs(est["K"] / truth["K"] - 1) < 0.10
