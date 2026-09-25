from bftune.io.dump import parse_dump
from bftune.model.params import Tune, thrust_linear_slope, validate

DUMP = """# version
# Betaflight / STM32H743 (H743) 2026.6.2 Sep 25 2026 / 13:28:49 (e0b7bb01b) MSP API: 1.48
# name: Test
set gyro_lpf2_static_hz = 500
profile 0
set p_roll = 38
set tpa_mode = D
set tpa_rate = 65
set tpa_breakpoint = 1350
rateprofile 0
set roll_rc_rate = 21
"""


def test_parse_dump_profiles():
    cfg = parse_dump(DUMP)
    assert cfg.firmware_version == "2026.6.2"
    assert cfg.craft_name == "Test"
    assert cfg.values["p_roll"] == "38"
    assert cfg.values["roll_rc_rate"] == "21"


def test_gain_scaling_and_tpa():
    t = Tune.from_config(parse_dump(DUMP))
    assert abs(t.kp(0) - 0.032029 * 38) < 1e-12
    assert t.tpa_factor(0.2) == 1.0
    assert abs(t.tpa_factor(1.0) - 0.35) < 1e-9  # 65% TPA at full throttle


def test_validation_catches_out_of_range_and_bad_enum():
    t = Tune.from_config(parse_dump(DUMP)).update(p_roll=400, tpa_mode="XYZ")
    probs = validate(t, ["p_roll", "tpa_mode", "gyro_lpf2_static_hz"])
    assert any("p_roll" in p for p in probs)
    assert any("tpa_mode" in p for p in probs)
    assert not any("gyro_lpf2" in p for p in probs)


def test_thrust_linear_slope():
    assert thrust_linear_slope(0, 0.3) == 1.0
    assert thrust_linear_slope(40, 0.05) > 1.4  # boosts low-throttle authority
    assert thrust_linear_slope(40, 1.0) < 1.0
