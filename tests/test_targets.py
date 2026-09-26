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
