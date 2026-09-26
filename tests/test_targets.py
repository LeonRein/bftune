"""Design targets: data-derived bands, labelled conventions, overrides, the fixed safety floor."""

import pytest

from bftune.optimize.targets import build_goals, data_bands


def test_bands_follow_the_flown_crossover():
    assert data_bands(18.0)["perf_band"] == (3.0, 54.0)
    assert data_bands(8.0)["perf_band"][1] == 24.0 and data_bands(None) == {}
    g, src = build_goals(None, 8.0)
    assert g.perf_band == (1.3, 24.0) and "flown" in src["perf_band"]


def test_conventions_are_labelled():
    g, src = build_goals("cinematic", None)
    assert g.ms_max == 1.7 and src["ms_max"] == "cinematic convention" and src["gm_min_db"] == "convention"


def test_overrides_and_floor():
    g, src = build_goals(None, None, {"style": "race", "ms_max": "2.2", "d_over_p": "0.5,1.4"})
    assert g.style == "race" and g.ms_max == 2.2 and g.d_over_p == (0.5, 1.4) and src["ms_max"] == "override"
    for bad in ({"ms_max": "2.8"}, {"pm_min": "20"}, {"noise_budget": "3"}):
        with pytest.raises(SystemExit):
            build_goals(None, None, bad)
    with pytest.raises(SystemExit):
        build_goals(None, None, {"not_a_target": "1"})


def test_profile_and_uncertainty_are_data_derived():
    from bftune.optimize.targets import build_goals

    prof = {"hover_throttle": 0.25, "throttle_pct": {"90": 0.53}}
    unc = {"k_hi": 1.15, "k_lo": 0.84, "dT": 0.0003, "source": "chirp rounds: gain spread ±15 %"}
    g, src = build_goals("freestyle", 20.0, None, None, prof, unc)
    assert g.mid_throttle == 0.53 and "log" in src["mid_throttle"]
    assert abs(g.gain_uncertainty - 0.15) < 1e-9 and src["gain_uncertainty"].startswith("chirp rounds")
    # a pilot hovering high: the mid case stays at least 0.1 above hover
    g2, _ = build_goals("freestyle", 20.0, None, None, {"hover_throttle": 0.5, "throttle_pct": {"90": 0.52}}, unc)
    assert g2.mid_throttle == 0.6
    # the robust variants may be widened, never narrowed below the data
    g3, _ = build_goals("freestyle", 20.0, {"gain_uncertainty": "0.25"}, None, prof, unc)
    assert g3.gain_uncertainty == 0.25
    with pytest.raises(SystemExit):
        build_goals("freestyle", 20.0, {"gain_uncertainty": "0.05"}, None, prof, unc)
    g4, src4 = build_goals("freestyle", 20.0, {"peak_max": "30,35,40"}, None, prof, unc)
    assert g4.peak_max == (30.0, 35.0, 40.0) and src4["peak_max"] == "override"


def test_chirp_uncertainty_from_round_spread():
    from types import SimpleNamespace

    from bftune.sysid.identify import chirp_uncertainty

    u = chirp_uncertainty({0: SimpleNamespace(round_gains=[0.95, 1.2]), 1: SimpleNamespace(round_gains=[1.02, 0.98])})
    assert abs(u["k_hi"] - 1.2) < 1e-6 and "rounds" in u["source"]
    u2 = chirp_uncertainty({0: SimpleNamespace(round_gains=[1.01, 0.99])})
    assert u2["k_hi"] == 1.1 and "floor" in u2["source"]  # never narrower than the convention
    assert chirp_uncertainty({0: SimpleNamespace(round_gains=[])})["source"].startswith("convention")


def test_idle_f_min_from_idle_stretches():
    from bftune.optimize.targets import build_goals

    g, src = build_goals("freestyle", 20.0, None, None, {"hover_throttle": 0.3, "throttle_pct": {"90": 0.5},
                                                          "idle_duration_p75_s": 0.4})
    assert g.idle_f_min == 2.5 and "idle" in src["idle_f_min"]


def test_metrics_ignore_frequencies_below_f_min():
    import numpy as np

    from bftune.analysis.loop import metrics

    f = np.geomspace(0.5, 200, 400)
    s = 2j * np.pi * f
    L = 40 / s * np.exp(-s * 0.002) + 3 / s**2  # an I-like low-frequency hump plus a real crossover
    full, cut = metrics(f, L), metrics(f, L, f_min=2.0)
    assert np.isfinite(cut.fc) and cut.fc >= 2.0 and cut.ms <= full.ms + 1e-9
