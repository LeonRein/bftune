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

    prof = {"hover_throttle": 0.25, "throttle_pct": {"90": 0.7}}
    unc = {"k_hi": 1.15, "k_lo": 0.84, "dT": 0.0003, "source": "chirp rounds: gain spread ±15 %"}
    g, src = build_goals("freestyle", 20.0, None, None, prof, unc)
    assert g.mid_throttle == 0.7 and "log" in src["mid_throttle"]  # the pilot flies above halfway hover-full
    assert g.hover_throttle == 0.25 and "hover" in src["hover_throttle"]
    assert abs(g.gain_uncertainty - 0.15) < 1e-9 and src["gain_uncertainty"].startswith("chirp rounds")
    # a pilot hovering high: the mid case sits halfway between hover and full throttle
    g2, _ = build_goals("freestyle", 20.0, None, None, {"hover_throttle": 0.5, "throttle_pct": {"90": 0.52}}, unc)
    assert g2.mid_throttle == 0.75
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


def test_mid_case_is_a_property_of_the_quad_and_extrapolation_is_flagged():
    from bftune.optimize.targets import extrapolation_note

    # the same 3.5" flown calmly (p90 0.35, up to 0.38) and hard (p90 0.63, up to 0.83): the same mid case
    calm, _ = build_goals("freestyle", 20.0, None, None, {"hover_throttle": 0.29, "throttle_pct": {"90": 0.35, "99": 0.38}})
    hard, _ = build_goals("freestyle", 20.0, None, None, {"hover_throttle": 0.30, "throttle_pct": {"90": 0.63, "99": 0.83}})
    assert abs(calm.mid_throttle - hard.mid_throttle) <= 0.01 and calm.mid_throttle > 0.6
    note = extrapolation_note(calm)
    assert note and "mid 0.65 (+0.27" in note and "full 1.00 (+0.62" in note and "less certain" in note
    assert "mid" not in extrapolation_note(hard) and extrapolation_note(
        build_goals("freestyle", 20.0, None, None, {"hover_throttle": 0.3, "throttle_pct": {"90": 0.6, "99": 0.95}})[0]) is None


def test_hover_case_at_logged_hover_and_extrapolated_cases_widened():
    from types import SimpleNamespace as NS

    import numpy as np

    from bftune.io.dump import Config
    from bftune.model.params import Tune
    from bftune.optimize.search import build_cases

    plant = NS(scaled=lambda hz, vbat, motor: NS(params={"K": hz, "T": 0.01}, structure="x"))
    idn = NS(axes={0: NS(op=NS(throttle=0.33, motor_hz=[220.0] * 4, vbat=23.0, dyn_notch_hz=None), plant=plant)}, motor=None)
    fl = NS(loop_hz=8000, idle_hz=lambda pct: 60.0, motor_hz_at=lambda thr: np.full(4, 100 + 400 * thr))
    tune = Tune.from_config(Config()).set("d_max_gain", 0).set("d_max_advance", 0).set("dyn_notch_count", 0)
    g, _ = build_goals("freestyle", 20.0, None, None, {"hover_throttle": 0.24, "throttle_pct": {"90": 0.35, "99": 0.38}})
    cases = {c.label: c for c in build_cases(fl, idn, tune, 0, g)}
    assert cases["hover/d"].op.throttle == 0.24 and cases["hover/d"].plant_params["K"] == 100 + 400 * 0.24
    # the reference stays the identification point when the log gives no hover throttle
    g0, _ = build_goals("freestyle", 20.0)
    assert {c.label: c for c in build_cases(fl, idn, tune, 0, g0)}["hover/d"].op.throttle == 0.33
    # inside the logged range: the identification's uncertainty; full throttle, 0.62 above it: widened
    k = {c.label: c.plant_params["K"] for c in cases.values()}
    assert abs(k["hover/d/hiK+delay"] / k["hover/d"] - (1 + g.gain_uncertainty)) < 1e-9
    assert abs(k["full/d/hiK+delay"] / k["full/d"] - (1 + g.gain_uncertainty + 0.3 * 0.62)) < 1e-9
