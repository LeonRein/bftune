"""Motor event classification and the `applied` dump check."""

import numpy as np

from bftune.analysis.motors import rpm_events
from bftune.tunes import applied


class FakeFlight:
    def __init__(self, n=4000, fs=1000.0):
        self.n, self.fs = n, fs
        self.t = np.arange(n) / fs
        self.motor_hz = np.full((n, 4), 200.0)
        self.motor = np.full((n, 4), 0.3)
        self.throttle = np.full(n, 0.3)
        self.gyro = np.zeros((n, 3))
        self.armed = np.ones(n, bool)

    def mode(self, box):
        return self.armed


def test_stall_vs_mixer_vs_crash():
    fl = FakeFlight()
    # mixer dip: motor 1 commanded down in a hard move
    fl.motor_hz[500:540, 0] = 20
    fl.motor[500:540, 0] = 0.05
    fl.motor[500:540, 1:] = 0.9
    # real stall: motor 2 commanded full but rpm collapsed
    fl.motor_hz[1500:1527, 1] = 5
    fl.motor[1500:1527, 1] = 1.0
    # crash: gyro pegged around a collapse of motor 3
    fl.motor_hz[3000:3100, 2] = 0
    fl.gyro[3050:3060, 0] = 2000
    kinds = {e["motor"]: e["kind"] for e in rpm_events(fl)}
    assert kinds == {1: "mixer", 2: "stall", 3: "crash"}


def test_applied(tmp_path):
    cli = tmp_path / "tune_cli.txt"
    cli.write_text("set rc_smoothing_auto_factor = 25\nprofile 0\nset p_pitch = 42\nsave\n")
    old = tmp_path / "old.txt"
    old.write_text("set rc_smoothing_auto_factor = 30\nset blackbox_high_resolution = ON\nprofile 0\nset p_pitch = 44\n")
    new = tmp_path / "new.txt"
    new.write_text("set rc_smoothing_auto_factor = 25\nprofile 0\nset p_pitch = 42\n")
    res = applied(str(cli), str(new), str(old))
    assert not res["not_applied"] and res["profile_ok"]
    assert list(res["other_changes"]) == ["blackbox_high_resolution"]
    new.write_text("set rc_smoothing_auto_factor = 25\nprofile 0\nset p_pitch = 44\n")
    assert "p_pitch" in applied(str(cli), str(new))["not_applied"]
