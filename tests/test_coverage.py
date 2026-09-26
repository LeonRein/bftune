"""Feature coverage table and model effects of settings added for full coverage."""

import json
from importlib import resources

from bftune.coverage import FEATURES, coverage, format_coverage
from bftune.io.dump import Config
from bftune.model.params import Tune


def _db():
    with resources.files("bftune.data").joinpath("settings_2026.6.json").open() as fh:
        return json.load(fh)["settings"]


def test_every_listed_setting_exists_in_firmware():
    db = _db()
    keys = [k for f in FEATURES for k in f.keys]
    assert len(keys) == len(set(keys)), "a setting is listed twice"
    missing = [k for k in keys if k not in db]
    assert not missing, missing
    assert not [k for k in keys if k.startswith(("s_", "spa_", "tpa_speed", "tpa_curve"))]  # wing-only


def test_coverage_marks_changes():
    base = Tune.from_config(Config())
    cand = base.copy().set("rpm_filter_q", 800)
    rows = coverage(base, cand)
    rpm = next(r for r in rows if r["group"] == "RPM filter")
    assert rpm["changed"] and not next(r for r in rows if r["group"] == "TPA")["changed"]
    assert "CHANGED" in format_coverage(rows, True)
