from bftune.emit.cli import cli_block
from bftune.model.params import Tune


def test_cli_block_has_guard_profile_and_save():
    old = Tune.from_config(type("C", (), {"get": lambda self, k, d=None: None})())
    new = old.copy().update(p_roll=50, gyro_lpf2_static_hz=600)
    apply_txt, revert_txt, problems = cli_block(old, new, profile=0, craft="t")
    assert "set simplified_gyro_filter = OFF" in apply_txt
    assert "profile 0" in apply_txt and "set p_roll = 50" in apply_txt
    assert apply_txt.strip().endswith("save")
    assert "set p_roll = 45" in revert_txt  # firmware default restored
    # master settings come before the profile switch
    assert apply_txt.index("gyro_lpf2_static_hz") < apply_txt.index("profile 0")
    assert problems == []
