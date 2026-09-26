# Python API for custom investigations

The CLI covers the common questions. When the agent needs something else, for example the
gyro spectrum during a particular manoeuvre, a plot of a flip, or a what-if on the plant, it writes a
short script against the package:

```bash
uv run --project "$CLAUDE_PLUGIN_ROOT" python my_check.py      # inside the plugin
uv run python my_check.py                                      # inside a clone of this repo
```

## Logs
```python
from bftune.flight import load_flight, AXES
fl = load_flight("logs/01.BFL", "logs/01-dump.txt")    # longest session; log_index=N to choose
fl.t, fl.fs, fl.loop_hz                                # time [s], log rate, PID loop rate
fl.gyro, fl.gyro_unfilt, fl.setpoint                   # (N,3) deg/s
fl.P, fl.I, fl.D, fl.F, fl.pidsum                      # (N,3) PID terms in pidSum units
fl.motor, fl.motor_hz, fl.throttle, fl.vbat            # motors 0..1, rotor Hz, 0..1, volts
fl.mode(0)                                             # armed mask; fl.mode(BOX_ANGLE) etc.
fl.cfg.int("p_roll"), fl.log.headers                   # config (dump merged with log headers)
```
`Flight.debug` holds the 8 debug channels. For `debug_mode = CHIRP`, `bftune.sysid.chirp.find_chirps(fl)`
lists the chirp runs.

## Diagnosis and spectra
```python
from bftune.analysis.diagnose import diagnose, propwash, oscillation   # individual heuristics too
from bftune.analysis.errspec import error_spectrum
diagnose(fl)            # list of dicts: id, severity, summary, evidence, causes, knobs
error_spectrum(fl)      # {"bands": [...], "axes": {"roll": [dB...]}, "windows": n}
```

## The model (needs `bftune analyze` first)
```python
from bftune.workbench import Workbench, apply_setting, parse_candidate
wb = Workbench("analysis/01", style="freestyle", noise_budget=0.9)
wb.an.idn.axes[0].plant          # Plant(structure, params: K, tau, T[, tz]); .fr(f), .scaled(motor_hz, vbat, motor_model)
wb.an.idn.motor                  # motor model: tau_at(hz), authority_ratio(hz, ref_hz)
wb.an.nm                         # noise model (bands with measured spectra and fitted 8 kHz PSD)
wb.an.idn.source                 # "chirp" or "freestyle"; wb.an.idn.uncertainty
t = apply_setting(wb.logged.copy(), "dterm_lpf2_static_hz", 120)
wb.assess(t)                     # same dict as `bftune assess --json`
wb.sweep(t, "d_roll", [25, 30, 35])
wb.step_metrics(t, 0)            # stick-flick lag / overshoot for roll
wb.axis_problem(t, 0).evaluate(p, i, d, dmax_ratio, detail=True)   # raw per-case margins
```

## Firmware model pieces
- `bftune.model.filters`: PT1/PT2/PT3, SVF lowpass and notch, the dynamic-LPF curve, the RPM
  notch bank and the dynamic-notch approximation, as z-domain frequency responses.
- `bftune.model.controller.controller_fr(tune, axis, op, f, dt, loop_hz)`: the linearized controller
  (P, D on measurement with its filters, TPA, thrust_linear) at an operating point.
- `bftune.model.params.Tune`: settings with firmware ranges; `Tune.from_config(fl.cfg)`.

The equations, with references to the firmware source, are in [model.md](model.md).
