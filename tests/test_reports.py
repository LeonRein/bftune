"""Reports (one HTML/Markdown style for every report), log reports and the measured step response."""

import pickle
from pathlib import Path

import numpy as np
import pytest

from bftune.report.doc import Doc, evidence


def test_doc_renders_html_and_markdown(tmp_path: Path):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    fig.savefig(tmp_path / "fig.png")
    plt.close(fig)
    doc = Doc("Tune <x>", subtitle="sub", kind="bftune test")
    doc.cards([("Verdict", "PASS", "pass")]).section("A").table(["h", "status"], [[1.5, "FAIL: pm"]], status_col=1)
    doc.section("B").code("set p_roll = 45", "tune").figure("fig.png", "a figure").figure("missing.png", "gone")
    doc.section("C").items(["one", "two"])
    p = doc.write(tmp_path, "r")
    h = p.read_text()
    assert "Tune &lt;x&gt;" in h and "prefers-color-scheme: dark" in h
    assert "data:image/png;base64," in h and "missing.png" not in h
    assert "class='fail'" in h and "class='num'" in h and "<nav>" in h
    md = (tmp_path / "r.md").read_text()
    assert md.startswith("# Tune <x>") and "| h | status |" in md and "```\nset p_roll = 45\n```" in md


def test_evidence_line_skips_long_values():
    line = evidence({"freq_hz": 66.0, "trim_pct": {"roll": 1.0}, "events": list(range(100))})
    assert "freq_hz 66.0" in line and "trim_pct" in line and "events" not in line


@pytest.fixture(scope="module")
def twin(tmp_path_factory):
    from bftune.synth.quad import CRAFTS, simulate

    d = tmp_path_factory.mktemp("twin")
    fl = simulate(CRAFTS["5inch"], chirp_s=8, hover_s=3, freestyle_s=30, seed=11)
    pkl = d / "twin.pkl"
    pkl.write_bytes(pickle.dumps(fl))
    return fl, pkl


@pytest.mark.slow
def test_log_step_recovers_a_known_delay(twin):
    from bftune.analysis.logstep import log_step_response

    fl = pickle.loads(pickle.dumps(twin[0]))
    d = int(round(0.008 * fl.fs))
    fl.gyro_unfilt = np.roll(fl.setpoint, d, axis=0)
    r = log_step_response(fl, 0)
    assert r is not None and abs(r["delay_50_ms"] - 1000 * d / fl.fs) <= 2.0
    assert abs(r["steady_state"] - 1.0) < 0.1  # normalised


@pytest.mark.slow
def test_log_report_single_and_comparison(twin, tmp_path: Path):
    from bftune.report.logs import format_summary, log_report

    fl, pkl = twin
    html, s = log_report([str(pkl)], tmp_path / "one", windows=[(1.0, 2.0)])
    assert html.exists() and (tmp_path / "one" / "logs.md").exists()
    assert "twin.pkl" in s["logs"] and "settings_diff" not in s
    assert any(k.startswith("window") for k in s["figures"]) and (tmp_path / "one" / "spectrogram_0.png").exists()
    other = tmp_path / "other.pkl"
    other.write_bytes(pkl.read_bytes())
    html2, s2 = log_report([str(pkl), str(other)], tmp_path / "two", spectrograms=False)
    assert s2["settings_diff"] == {} and len(s2["logs"]) == 2
    assert "report:" in format_summary(s2, html2)


@pytest.mark.slow
def test_analyze_writes_the_analysis_report(twin, tmp_path: Path):
    from bftune.pipeline import analyze

    analyze(str(twin[1]), None, tmp_path, log=lambda *_: None)
    h = (tmp_path / "analysis.html").read_text()
    assert "bftune analysis report" in h and "Model of the quad" in h and (tmp_path / "spectrogram.png").exists()


def test_shape_metrics_interpolates_and_finds_the_dip():
    from bftune.analysis.logstep import shape_metrics

    t = np.arange(0, 0.2, 0.001)
    y = np.interp(t, [0, 0.02, 0.04, 0.2], [0, 1.3, 0.9, 1.0])  # 0.5 reached at 0.5/65 s = 7.69 ms, between samples
    m = shape_metrics(t, y)
    assert abs(m["delay_50_ms"] - 1000 * 0.5 / 65) < 0.01
    assert abs(m["overshoot_pct"] - 30) < 0.5 and abs(m["undershoot_pct"] - 10) < 0.5


def test_dump_can_be_a_log_header(tmp_path: Path):
    from bftune.io.bbl import LOG_START_MARKER
    from bftune.io.dump import load_dump

    hdr = LOG_START_MARKER + b"\nH Firmware revision:Betaflight 2026.6.2\nH rollPID:45,80,30\nH pitchPID:47,84,34\n"
    p = tmp_path / "old.bbl"
    p.write_bytes(hdr + b"I" + bytes(20))
    cfg = load_dump(str(p))
    assert cfg.values["p_roll"] in ("45", 45) and "log header" in cfg.source


def test_exclude_disarms_a_window():
    from types import SimpleNamespace

    from bftune.flight import exclude

    fl = SimpleNamespace(t=np.arange(0, 10, 0.5), mode_mask=np.ones(20, dtype=np.int64) | 4)
    exclude(fl, [(2.0, 4.0)])
    assert (fl.mode_mask[4:8] & 1).sum() == 0 and (fl.mode_mask[4:8] & 4).all() and fl.mode_mask[0] & 1


def test_next_tune_dir_counts_history(tmp_path: Path):
    from bftune.project import next_tune_dir

    (tmp_path / "history.md").write_text("# q\n\n## 01 - 2026-09-26 - a\n\n## 02 - 2026-09-26 - b\n")
    assert next_tune_dir(tmp_path).name.startswith("03-")


def test_scaled_band():
    from bftune.analysis.profile import scaled_band

    assert scaled_band(175.0, 0.088, 0.47, 1000.0, (15, 80)) == pytest.approx((15.4, 82.25))
    assert scaled_band(1200.0, 0.088, 0.47, 1000.0, (15, 80))[1] == 450.0  # capped below Nyquist
    assert scaled_band(None, 0.088, 0.47, 1000.0, (15, 80)) == (15, 80)


@pytest.mark.slow
def test_profile_replay_and_as_flown_step(twin, tmp_path: Path):
    from bftune.pipeline import analyze
    from bftune.workbench import Workbench, brief

    an = analyze(str(twin[1]), None, tmp_path, log=lambda *_: None)
    prof = an.extra["profile"]
    assert prof["hover_motor_hz"] and 0.05 < prof["hover_throttle"] < 0.9
    wb = Workbench(tmp_path)
    a = wb.assess(wb.logged, steps=True, axes=[0])
    assert "as_flown" in a["axes"]["roll"]["step"]
    assert wb.target_sources["mid_throttle"].startswith("from the log")
    assert wb.peak_max.get("roll") is not None
    b = brief(wb)
    assert b["flight_profile"]["hover_throttle"] == prof["hover_throttle"]


def test_tunes_reads_synthetic_logs(twin):
    from bftune.tunes import group_tunes

    res = group_tunes([str(twin[1])])
    assert len(res["groups"]) == 1 and res["groups"][0]["sessions"] == ["twin.pkl"]
