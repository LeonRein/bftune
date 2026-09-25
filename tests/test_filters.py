import numpy as np
import pytest

from bftune.model import filters as flt

DT = 1 / 8000


@pytest.mark.parametrize("order", [1, 2, 3])
def test_ptn_cutoff_is_minus_3db(order):
    # the firmware's cutoff correction places -3 dB at fc (approximately, discrete PT1 is not prewarped)
    h = flt.fr_ptn(100.0, np.array([100.0]), DT, order)
    assert abs(20 * np.log10(abs(h[0])) + 3.0) < 0.5  # PT2/PT3 correction assumes analog PT1s: -3.3 / -3.4 dB in firmware


def test_svf_lowpass_is_butterworth_at_cutoff():
    h = flt.fr_svf_lpf(200.0, np.array([200.0, 10.0]), DT)
    assert abs(20 * np.log10(abs(h[0])) + 3.01) < 0.02  # prewarped: exact at fc
    assert abs(abs(h[1]) - 1) < 1e-3


def test_notch_zero_at_centre():
    h = flt.fr_svf_notch(250.0, 5.0, np.array([250.0, 50.0]), DT)
    assert abs(h[0]) < 1e-6
    assert abs(abs(h[1]) - 1) < 0.01


def _impulse_fr(filt, n=4096):
    x = np.zeros(n)
    x[0] = 1.0
    y = np.array([filt(v) for v in x])
    return np.fft.rfft(y), np.fft.rfftfreq(n, DT)


@pytest.mark.parametrize("kind", ["PT1", "PT2", "PT3", "BIQUAD"])
def test_time_domain_matches_frequency_response(kind):
    H, f = _impulse_fr(flt.make_lowpass(kind, 180.0, DT))
    m = (f > 5) & (f < 1500)
    ref = flt.fr_lowpass(kind, 180.0, f[m], DT)
    assert np.max(np.abs(H[m] - ref)) < 1e-6


def test_time_domain_notch_matches():
    H, f = _impulse_fr(flt.SvfNotch(300.0, 3.0, DT, 0.8))
    m = (f > 5) & (f < 1500)
    ref = flt.fr_svf_notch(300.0, 3.0, f[m], DT, 0.8)
    assert np.max(np.abs(H[m] - ref)) < 1e-6


def test_dyn_lpf_curve_endpoints():
    assert flt.dyn_lpf_cutoff(250, 500, 5, 0.0) == 250
    assert flt.dyn_lpf_cutoff(250, 500, 5, 1.0) == 500
    assert 250 < flt.dyn_lpf_cutoff(250, 500, 5, 0.3) < 500
