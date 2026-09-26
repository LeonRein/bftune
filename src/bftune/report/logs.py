"""Look at flights: figures the agent can open (it reads images) and one HTML report per log, or a
side-by-side comparison of several logs of the same quad (e.g. before/after a tune).

Data only - no model - so it works on any log, with or without chirps:
  - throttle spectrograms of gyro (before/after filtering) and D-term, with the motor frequency drawn in
    (motor lines follow it, frame resonances stay at a fixed frequency),
  - the step response measured from ordinary flying (setpoint -> gyro, as flown, including FF),
  - the tracking-error spectrum (propwash wobble, rejection),
  - time-window plots of sticks, gyro, D-term and motors (`window`),
  - automatic findings (analysis.diagnose) and, for several logs, the settings that differ.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from ..flight import AXES, Flight, exclude, load_flight, tuning_keys, without_crashes
from . import plots as _style  # noqa: F401  (shared matplotlib look)
from .doc import Doc, evidence

LOG_COLORS = ["#6b7280", "#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed"]


def _armed_windows(fl: Flight, n: int, mask: np.ndarray | None = None) -> np.ndarray:
    ok = fl.mode(0) if mask is None else mask
    starts = np.arange(0, fl.n - n, n // 2)
    cs = np.concatenate([[0], np.cumsum(ok)])
    return starts[(cs[starts + n] - cs[starts]) == n]


def throttle_spectrum(fl: Flight, x: np.ndarray, n: int = 256, n_thr: int = 20) -> dict:
    """PSD per throttle bin (armed data), PIDtoolbox-style: rows = throttle, columns = frequency."""
    from numpy.lib.stride_tricks import sliding_window_view

    starts = _armed_windows(fl, n)
    f = np.fft.rfftfreq(n, 1 / fl.fs)
    edges = np.linspace(0, 1, n_thr + 1)
    P = np.full((n_thr, len(f)), np.nan)
    count = np.zeros(n_thr, int)
    if len(starts):
        w = np.hanning(n)
        seg = sliding_window_view(x, n)[starts]
        seg = (seg - seg.mean(axis=1, keepdims=True)) * w
        S = np.abs(np.fft.rfft(seg, axis=1)) ** 2 * 2 / (fl.fs * np.sum(w**2))
        thr = sliding_window_view(fl.throttle, n)[starts].mean(axis=1)
        b = np.clip(np.digitize(thr, edges) - 1, 0, n_thr - 1)
        for i in range(n_thr):
            if (b == i).sum() >= 3:
                P[i] = S[b == i].mean(axis=0)
                count[i] = (b == i).sum()
    motor = None
    if fl.motor_hz is not None and fl.motor_hz.size:
        mh = fl.motor_hz.mean(axis=1)
        armed = fl.mode(0)
        motor = np.array([np.median(mh[armed & (fl.throttle >= lo) & (fl.throttle < hi)])
                          if (armed & (fl.throttle >= lo) & (fl.throttle < hi)).sum() > 50 else np.nan
                          for lo, hi in zip(edges[:-1], edges[1:])])
    return {"f": f, "edges": edges, "P": P, "count": count, "motor_hz": motor}


def spectrogram_plot(fl: Flight, path: Path, title: str = "") -> Path:
    """3 rows (unfiltered gyro, filtered gyro, D-term) x 3 axes, throttle vs frequency, dB."""
    rows = [("gyro raw", fl.gyro_unfilt), ("gyro filtered", fl.gyro), ("D-term", fl.D)]
    nyq = fl.fs / 2
    fig, axs = plt.subplots(3, 3, figsize=(15, 10), sharex=True, sharey=True)
    for r, (name, sig) in enumerate(rows):
        specs = [throttle_spectrum(fl, sig[:, a]) for a in range(3)]
        allv = np.concatenate([10 * np.log10(s["P"][np.isfinite(s["P"])] + 1e-9) for s in specs] or [np.zeros(1)])
        vmax = np.percentile(allv, 99.5) if allv.size else 0
        im = None
        for a, s in enumerate(specs):
            ax = axs[r, a]
            ax.set_title(f"{AXES[a]}: {name}")
            ax.set_xlim(0, nyq)
            if not np.any(sig[:, a]):
                ax.text(0.5, 0.5, "not logged / zero", transform=ax.transAxes, ha="center", color="#6b7280")
                continue
            Z = 10 * np.log10(s["P"] + 1e-9)
            df = s["f"][1] - s["f"][0]
            fe = np.concatenate([s["f"] - df / 2, [s["f"][-1] + df / 2]])
            im = ax.pcolormesh(fe, s["edges"] * 100, Z, shading="flat", cmap="viridis", vmin=vmax - 50, vmax=vmax)
            if s["motor_hz"] is not None:
                mid = (s["edges"][:-1] + s["edges"][1:]) / 2 * 100
                for h, ls in ((1, "-"), (2, "--"), (3, ":")):
                    folded = np.abs((s["motor_hz"] * h + nyq) % fl.fs - nyq)  # where the harmonic lands after aliasing
                    ax.plot(folded, mid, color="w", lw=0.8, ls=ls, alpha=0.8,
                            label="motor ×1 / ×2 / ×3 (aliased)" if h == 1 else None)
            ax.grid(False)
            if a == 0:
                ax.set_ylabel("throttle %")
            if r == 2:
                ax.set_xlabel("Hz")
        if im is not None:
            fig.colorbar(im, ax=axs[r, :].tolist(), shrink=0.9, label="dB")
    axs[0, 0].legend(loc="upper right", fontsize=7, labelcolor="w")
    fig.suptitle(f"{title}  (log rate {fl.fs:.0f} Hz: content above {nyq:.0f} Hz is folded back)".strip())
    fig.savefig(path)
    plt.close(fig)
    return path


def measured_steps(fl: Flight) -> dict:
    from ..analysis.logstep import log_step_response

    out = {}
    for a in range(3):
        r = log_step_response(fl, a)
        if r is not None:
            out[AXES[a]] = r
    return out


def step_summary(r: dict) -> dict:
    return {"delay_50_ms": round(r["delay_50_ms"], 1), "overshoot_pct": round(r["overshoot_pct"], 1),
            "undershoot_pct": round(r["undershoot_pct"], 1), "windows": r["n"], "confidence": r["confidence"]}


def step_plot(steps: dict[str, dict], path: Path, model: dict | None = None) -> Path | None:
    """Measured step responses per log; `model` = {axis: model_step dict} draws the model's prediction for the
    flown tune on top (dashed), the direct visual check of the model."""
    if not any(steps.values()):
        return None
    fig, axs = plt.subplots(1, 3, figsize=(15, 4))
    for i, (name, st) in enumerate(steps.items()):
        col = LOG_COLORS[i % len(LOG_COLORS)] if len(steps) > 1 else "#2563eb"
        for a, ax_name in enumerate(AXES):
            r = st.get(ax_name)
            if r is None:
                continue
            t = r["t"] * 1000
            axs[a].plot(t, r["step"], color=col, label=f"{name}: 50 % at {r['delay_50_ms']:.1f} ms, "
                                                      f"peak +{r['overshoot_pct']:.0f} % (n={r['n']}, {r['confidence']})")
            axs[a].fill_between(t, r["step"] - r["std"], r["step"] + r["std"], color=col, alpha=0.12, lw=0)
    for a, ax_name in enumerate(AXES):
        m = (model or {}).get(ax_name)
        if m is not None:
            axs[a].plot(m["t"] * 1000, m["step"], color="#111827", ls="--", lw=1.2,
                        label=f"model, flown tune: 50 % at {m['delay_50_ms']:.1f} ms, peak +{m['overshoot_pct']:.0f} %")
    for a, ax in enumerate(axs):
        ax.axhline(1, color="k", lw=0.6)
        ax.set_title(f"{AXES[a]}: step response as flown")
        ax.set_xlabel("ms")
        ax.set_xlim(0, 200)
        ax.set_ylim(0, 1.6)
        ax.legend(loc="lower right")
    fig.savefig(path)
    plt.close(fig)
    return path


def errspec_plot(res: dict[str, dict], path: Path) -> Path | None:
    if not any(r["axes"] for r in res.values()):
        return None
    fig, axs = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
    for i, (name, r) in enumerate(res.items()):
        col = LOG_COLORS[i % len(LOG_COLORS)] if len(res) > 1 else "#2563eb"
        for a, ax_name in enumerate(AXES):
            v = r["axes"].get(ax_name)
            if v:
                axs[a].plot(range(len(v)), v, "o-", color=col, label=name)
    bands = next(iter(res.values()))["bands"]
    for a, ax in enumerate(axs):
        ax.set_xticks(range(len(bands)), bands, rotation=45, fontsize=8)
        ax.set_title(f"{AXES[a]}: tracking error")
        ax.set_xlabel("band [Hz]")
        ax.legend()
    axs[0].set_ylabel("dB (deg/s)²/Hz")
    fig.savefig(path)
    plt.close(fig)
    return path


def window_plot(fl: Flight, t0: float, t1: float, path: Path, title: str = "") -> Path:
    """Sticks/setpoint vs gyro per axis, D-term, throttle and motors (and rpm) in one time window."""
    s = np.searchsorted(fl.t, t0)
    e = max(s + 2, np.searchsorted(fl.t, t1))
    t = fl.t[s:e]
    fig, axs = plt.subplots(5, 1, figsize=(14, 12), sharex=True,
                            gridspec_kw={"height_ratios": [2, 2, 2, 1.5, 1.5]})
    for a in range(3):
        ax = axs[a]
        ax.plot(t, fl.setpoint[s:e, a], color="#111827", lw=1, label="setpoint")
        ax.plot(t, fl.gyro[s:e, a], color="#2563eb", lw=1, label="gyro (filtered)")
        ax.plot(t, fl.gyro_unfilt[s:e, a], color="#93c5fd", lw=0.6, alpha=0.7, label="gyro (unfiltered)")
        if np.any(fl.D[s:e, a]):
            ax2 = ax.twinx()
            ax2.plot(t, fl.D[s:e, a], color="#d97706", lw=0.6, alpha=0.8)
            ax2.set_ylabel("D-term", color="#d97706")
            ax2.grid(False)
        ax.set_ylabel(f"{AXES[a]} deg/s")
        ax.legend(loc="upper right")
    ax = axs[3]
    for m in range(fl.motor.shape[1]):
        ax.plot(t, fl.motor[s:e, m] * 100, lw=0.8, label=f"motor {m + 1}")
    ax.plot(t, fl.throttle[s:e] * 100, color="k", lw=1.2, label="throttle")
    ax.set_ylabel("%")
    ax.legend(loc="upper right", ncol=5)
    ax = axs[4]
    if fl.motor_hz is not None and fl.motor_hz.size:
        for m in range(fl.motor_hz.shape[1]):
            ax.plot(t, fl.motor_hz[s:e, m], lw=0.8)
        rmin = fl.cfg.int("rpm_filter_min_hz", 0)
        if rmin:
            ax.axhline(rmin, color="#dc2626", lw=0.8, ls="--", label=f"rpm_filter_min_hz {rmin}")
            ax.legend(loc="upper right")
        ax.set_ylabel("motor Hz")
    else:
        ax.plot(t, fl.vbat[s:e], color="k", lw=1)
        ax.set_ylabel("vbat")
    ax.set_xlabel("s")
    if title:
        fig.suptitle(title)
    fig.savefig(path)
    plt.close(fig)
    return path


def facts(fl: Flight) -> dict:
    armed = fl.mode(0)
    thr = fl.throttle[armed] if armed.any() else fl.throttle
    out = {"duration_s": round(float(fl.t[-1]), 1), "armed_s": round(float(armed.sum() / fl.fs), 1),
           "firmware": fl.cfg.firmware_version, "craft": fl.cfg.craft_name, "loop_hz": round(fl.loop_hz),
           "log_rate_hz": round(fl.fs), "throttle_p10_p50_p90": [round(float(x), 2) for x in np.percentile(thr, [10, 50, 90])]
           if thr.size else None}
    if armed.any() and fl.vbat.size:
        out["vbat_max_min"] = [round(float(np.percentile(fl.vbat[armed], 99)), 2),
                               round(float(np.percentile(fl.vbat[armed], 1)), 2)]
    if fl.motor_hz is not None and fl.motor_hz.size and armed.any():
        mh = fl.motor_hz[armed].mean(axis=1)
        out["motor_hz_p5_p50_p99"] = [round(float(x)) for x in np.percentile(mh, [5, 50, 99])]
    return out


def settings_diff(fls: dict[str, Flight]) -> list[list[str]]:
    keys = sorted(tuning_keys())
    rows = []
    for k in keys:
        vals = [str(fl.cfg.values.get(k, "")) for fl in fls.values()]
        if len({v.replace(" ", "").upper() for v in vals}) > 1:
            rows.append([k, *vals])
    return rows


def _label(path: str, fls: dict) -> str:
    name = Path(path).name
    parent = Path(path).parent.name
    return f"{parent}/{name}" if parent and sum(Path(p).name == name for p in fls) > 1 else name


def log_report(paths: list[str], out: Path, dump: str | None = None, index: int | None = None,
               windows: list[tuple[float, float]] | None = None, spectrograms: bool = True,
               excluded: list[tuple[float, float]] | None = None) -> tuple[Path, dict]:
    """Write OUT/logs.html + logs.md + PNGs; return (html path, compact summary for the agent)."""
    from ..analysis.diagnose import diagnose
    from ..analysis.errspec import error_spectrum

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    raw = {p: without_crashes(exclude(load_flight(p, dump if len(paths) == 1 else None, index), excluded))[0]
           for p in paths}
    fls = {_label(p, raw): fl for p, fl in raw.items()}
    one = len(fls) == 1
    first = next(iter(fls.values()))
    summary: dict = {"logs": {}, "figures": {}}
    doc = Doc(("Log report: " if one else "Log comparison: ") + (first.cfg.craft_name or "quad"),
              subtitle=" · ".join(fls) + f" · Betaflight {first.cfg.firmware_version}",
              kind="bftune log report" if one else "bftune log comparison")

    steps, errs = {}, {}
    for name, fl in fls.items():
        fd = diagnose(fl)
        steps[name] = measured_steps(fl)
        errs[name] = error_spectrum(fl)
        summary["logs"][name] = {
            "facts": facts(fl),
            "findings": [{"severity": f["severity"], "id": f["id"], "summary": f["summary"],
                          **({"worst_at_s": f["evidence"]["worst_at_s"]} if "worst_at_s" in f["evidence"] else {})}
                         for f in fd],
            "measured_step": {a: step_summary(r) for a, r in steps[name].items()},
            "errspec_db": errs[name]["axes"], "errspec_bands": errs[name]["bands"],
            "activity": errs[name].get("activity"),
        }
        summary["logs"][name]["_findings_full"] = fd

    # ---- overview cards
    cards = []
    for name, s in summary["logs"].items():
        f = s["facts"]
        prob = sum(x["severity"] == "problem" for x in s["findings"])
        warn = sum(x["severity"] == "warn" for x in s["findings"])
        pre = "" if one else f"{name}: "
        cards.append((pre + "flight", f"{f['armed_s']:.0f} s armed", None))
        cards.append((pre + "findings", f"{prob} problem · {warn} warn", "fail" if prob else ("warn" if warn else "pass")))
        for a in ("roll", "pitch"):
            m = s["measured_step"].get(a)
            if m:
                cards.append((pre + f"{a} stick lag as flown", f"{m['delay_50_ms']:.0f} ms · +{m['overshoot_pct']:.0f} %",
                               None if m["confidence"] != "low" else "warn"))
    doc.cards(cards)
    doc.note("Measured from the flight itself (no model). Compare logs only when they were flown similarly "
             "(see 'how hard it was flown'); stick-activity differences shift every band.", "")

    # ---- settings
    if not one:
        diff = settings_diff(fls)
        doc.section("Settings that differ")
        if diff:
            doc.table(["setting", *fls], diff)
        else:
            doc.p("The logs flew the same tuning settings.", "muted")
        summary["settings_diff"] = {r[0]: r[1:] for r in diff}

    # ---- findings
    doc.section("Findings")
    for name, s in summary["logs"].items():
        if not one:
            doc.sub(name)
        rows = []
        for f in s["_findings_full"]:
            ev = evidence(f["evidence"])
            rows.append([f["severity"], f["id"], f["summary"], ev])
        if rows:
            doc.table(["severity", "id", "finding", "evidence"], rows, status_col=0)
        else:
            doc.p("No findings.", "muted")

    # ---- flight facts
    doc.section("Flights")
    keys = ["duration_s", "armed_s", "loop_hz", "log_rate_hz", "throttle_p10_p50_p90", "vbat_max_min", "motor_hz_p5_p50_p99"]
    doc.table(["", *fls], [[k, *[str(s["facts"].get(k, "")) for s in summary["logs"].values()]] for k in keys])
    act_rows = [[n, str(s["activity"]["stick_rms_deg_s"]), str(s["activity"]["throttle_p25_p50_p75"]),
                 s["activity"]["motor_saturation_pct"]] for n, s in summary["logs"].items() if s.get("activity")]
    if act_rows:
        doc.sub("How hard it was flown (acro windows)")
        doc.table(["log", "stick RMS r/p/y [deg/s]", "throttle p25/50/75", "motor saturation %"], act_rows)

    # ---- step response + error spectrum
    doc.section("Stick response as flown")
    p = step_plot(steps, out / "log_steps.png")
    if p:
        summary["figures"]["steps"] = p.name
        doc.figure(p.name, "Setpoint → gyro step response deconvolved from ordinary flying (includes FF, I-term relax, "
                           "nonlinearities), normalised to its settled level; band = spread over windows. Time to 50 % "
                           "= stick lag as flown (±1 ms); peak within ~5 % is estimator artefact - compare logs.")
    else:
        doc.p("Not enough stick activity in acro for a measured step response.", "muted")
    p = errspec_plot(errs, out / "log_errspec.png")
    if p:
        summary["figures"]["errspec"] = p.name
        doc.figure(p.name, "Tracking-error spectrum: a bump at 30-60 Hz = sensitivity peak (propwash wobble); "
                           "higher 5-30 Hz = weaker disturbance rejection.")

    # ---- spectrograms
    if spectrograms:
        doc.section("Noise vs throttle")
        for i, (name, fl) in enumerate(fls.items()):
            p = spectrogram_plot(fl, out / f"spectrogram_{i}.png", name)
            summary["figures"][f"spectrogram {name}"] = p.name
            doc.figure(p.name, f"{name}: PSD per throttle band. Lines that follow the white motor curves are motor "
                               "harmonics (RPM filter's job); vertical stripes at a fixed frequency are frame "
                               "resonances (dynamic notch / lowpass); broad low-frequency energy at low throttle is "
                               "propwash or body motion.")

    # ---- time windows
    if windows:
        doc.section("Time windows")
        for j, (t0, t1) in enumerate(windows):
            for i, (name, fl) in enumerate(fls.items()):
                if t0 >= fl.t[-1]:
                    continue
                p = window_plot(fl, t0, t1, out / f"window_{i}_{j}.png", f"{name}  {t0:g}-{t1:g} s")
                summary["figures"][f"window {name} {t0:g}-{t1:g}"] = p.name
                doc.figure(p.name, f"{name}, {t0:g}-{t1:g} s: setpoint vs gyro, D-term (orange, right axis), motors, rpm.")

    for s in summary["logs"].values():
        s.pop("_findings_full")
    html = doc.write(out, "logs")
    return html, summary


def format_summary(summary: dict, html: Path) -> str:
    L = []
    for name, s in summary["logs"].items():
        f = s["facts"]
        L.append(f"== {name}: {f['armed_s']} s armed, loop {f['loop_hz']} Hz, log {f['log_rate_hz']} Hz, "
                 f"throttle p10/50/90 {f['throttle_p10_p50_p90']}")
        for x in s["findings"]:
            at = f"  (worst at {x['worst_at_s']} s)" if x.get("worst_at_s") else ""
            L.append(f"  [{x['severity']:7s}] {x['id']}: {x['summary']}{at}")
        if s["measured_step"]:
            L.append("  measured step (as flown): " + "; ".join(
                f"{a} 50 % at {m['delay_50_ms']:.1f} ms, peak +{m['overshoot_pct']:.0f} %, dip -{m['undershoot_pct']:.0f} % "
                f"(n={m['windows']}, {m['confidence']})"
                for a, m in s["measured_step"].items()))
        if s["errspec_db"]:
            L.append("  error spectrum dB " + " ".join(s["errspec_bands"]) + ": " + "; ".join(
                f"{a} {v}" for a, v in s["errspec_db"].items()))
        if s.get("activity"):
            L.append(f"  flown: stick RMS r/p/y {s['activity']['stick_rms_deg_s']} deg/s, "
                     f"motor saturation {s['activity']['motor_saturation_pct']} %")
    if "settings_diff" in summary:
        L.append("settings that differ: " + ("; ".join(f"{k} {' -> '.join(v)}" for k, v in summary["settings_diff"].items())
                                             or "none"))
    L.append(f"report: {html}")
    L.append("figures (open them to look): " + ", ".join(str(html.parent / v) for v in summary["figures"].values()))
    return "\n".join(L)
