"""Synthetic quad ("digital twin") for end-to-end tests of the tuning pipeline.

A rigid-body rate model with four first-order motors, quad-X mixing, thrust ∝ omega²,
yaw from drag torque plus rotor reaction torque, pure transport delays, gyro noise with
motor-harmonic lines and broadband content, and the Betaflight controller (exact filter
implementations, P/I/D-on-measurement, iterm relax, TPA on D, CHIRP excitation added to
the setpoint). It produces a `Flight` object sampled like a blackbox log (every Nth loop,
no anti-alias filter, integer quantization) so the whole analysis chain runs unchanged.

Ground truth is available analytically via `true_plant(axis)`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..flight import Flight
from ..io.bbl import Log
from ..io.dump import Config
from ..model import filters as flt
from ..model.params import Tune
from ..model.plant import Plant


@dataclass
class CraftSpec:
    name: str
    loop_hz: float = 8000.0
    log_ratio: int = 8
    cells: int = 4
    motor_poles: int = 14
    hover_throttle: float = 0.25
    hover_hz: float = 230.0  # nominal motor Hz at hover (actual value follows the motor curve)
    max_hz: float = 550.0
    idle_hz: float = 90.0
    tau_c0: float = 30.0  # 1/tau = c0 + c1*hz
    tau_c1: float = 0.065
    K_roll: float = 130.0  # deg/s^2 per pidSum unit at hover
    K_pitch: float = 100.0
    K_yaw: float = 11.0  # low-frequency yaw gain (drag torque)
    tz_yaw: float = 0.11  # yaw reaction-torque zero [s]
    T_esc: float = 0.0010  # command -> motor delay
    T_gyro: float = 0.0005  # sensor/hardware LPF delay
    aero_damping: float = 0.5  # 1/s
    noise_white: float = 0.3  # deg/s rms broadband at the loop rate
    noise_lines: tuple[float, ...] = (6.0, 2.0, 1.0)  # deg/s amplitude per harmonic at hover (∝ (hz/hover)^2)
    frame_mode_hz: float = 0.0  # optional structural resonance (noise only)
    frame_mode_amp: float = 0.0
    motor_spread: float = 0.06  # relative rpm differences between motors at hover
    tune: dict = field(default_factory=dict)  # CLI overrides for the synthetic "current" tune

    gamma: float = 0.75  # steady-state motor curve: hz = idle + (max-idle)*cmd^gamma
    chirp_f1: float = 200.0  # per-class chirp end frequency (bf-flight-protocol table)
    chirp_amp: tuple[float, float, float] = (230.0, 230.0, 180.0)

    def tau(self, hz: float) -> float:
        return 1.0 / (self.tau_c0 + self.tau_c1 * hz)

    def hz_at(self, thr: float) -> float:
        return self.idle_hz + (self.max_hz - self.idle_hz) * thr**self.gamma

    @property
    def w_hover(self) -> float:
        return self.hz_at(self.hover_throttle)


CRAFTS = {
    "whoop65": CraftSpec(
        "whoop65", loop_hz=4000, log_ratio=4, cells=1, motor_poles=12, hover_throttle=0.45, hover_hz=700, max_hz=1100,
        idle_hz=250, tau_c0=45, tau_c1=0.03, K_roll=260, K_pitch=250, K_yaw=40, tz_yaw=0.04, T_esc=0.0012,
        T_gyro=0.0007, noise_white=0.6, noise_lines=(10.0, 4.0, 2.0), frame_mode_hz=0, chirp_f1=300.0,
        chirp_amp=(150.0, 150.0, 120.0),
        tune={"p_roll": 70, "i_roll": 80, "d_roll": 55, "d_max_roll": 65, "p_pitch": 75, "i_pitch": 85, "d_pitch": 60,
              "d_max_pitch": 70, "p_yaw": 80, "i_yaw": 80, "motor_poles": 12, "gyro_lpf1_dyn_min_hz": 300,
              "gyro_lpf1_dyn_max_hz": 600, "dterm_lpf1_dyn_min_hz": 100, "dterm_lpf1_dyn_max_hz": 200},
    ),
    "3.5inch": CraftSpec("3.5inch", tune={"p_roll": 38, "i_roll": 56, "d_roll": 22, "d_max_roll": 25, "p_pitch": 47,
                                            "i_pitch": 55, "d_pitch": 25, "d_max_pitch": 29, "p_yaw": 55, "i_yaw": 80}),
    "5inch": CraftSpec(
        "5inch", cells=6, hover_throttle=0.22, hover_hz=160, max_hz=420, idle_hz=65, tau_c0=22, tau_c1=0.08,
        K_roll=95, K_pitch=80, K_yaw=8, tz_yaw=0.13, T_esc=0.0010, noise_white=0.4, noise_lines=(8.0, 3.0, 1.5),
        frame_mode_hz=180, frame_mode_amp=1.5,
    ),
    "10inch": CraftSpec(
        "10inch", loop_hz=4000, log_ratio=4, cells=6, hover_throttle=0.30, hover_hz=80, max_hz=190, idle_hz=35,
        tau_c0=8, tau_c1=0.12, K_roll=35, K_pitch=28, K_yaw=2.5, tz_yaw=0.25, T_esc=0.0015, T_gyro=0.0006,
        noise_white=0.3, noise_lines=(5.0, 2.0, 1.0), frame_mode_hz=95, frame_mode_amp=2.0, chirp_f1=120.0,
        chirp_amp=(150.0, 150.0, 120.0),
        tune={"p_roll": 60, "i_roll": 90, "d_roll": 45, "d_max_roll": 55, "p_pitch": 65, "i_pitch": 95, "d_pitch": 50,
              "d_max_pitch": 60, "p_yaw": 60, "i_yaw": 90, "gyro_lpf1_dyn_min_hz": 150, "gyro_lpf1_dyn_max_hz": 300,
              "dterm_lpf1_dyn_min_hz": 60, "dterm_lpf1_dyn_max_hz": 120, "dterm_lpf2_static_hz": 120,
              "rpm_filter_min_hz": 50, "dyn_notch_min_hz": 60, "dyn_notch_max_hz": 300},
    ),
}


SPREAD = (0.8, -0.6, 0.3, -0.5)


def _spread(spec: CraftSpec) -> np.ndarray:
    return 1 + spec.motor_spread * np.array(SPREAD)


def true_plant(spec: CraftSpec, axis: int) -> Plant:
    """Linearization of the twin at hover (average of the four motor lags).

    Equivalent pure delay: the command reaches the gyro after d_esc + d_gyro + 1 loops, but the
    twin's two Euler integrators respond within the same step (each ~half a sample *earlier*
    than a zero-order-hold continuous system), which cancels the extra loop: T = T_esc + T_gyro.
    """
    sp = _spread(spec)
    tau = float(np.mean([spec.tau(spec.w_hover * s) for s in sp]))
    T = round(spec.T_esc * spec.loop_hz) / spec.loop_hz + round(spec.T_gyro * spec.loop_hz) / spec.loop_hz
    if axis < 2:
        K = spec.K_roll if axis == 0 else spec.K_pitch
        return Plant("pole_lag", {"K": K, "a": spec.aero_damping, "tau": tau, "T": T}, spec.w_hover, 3.8 * spec.cells)
    return Plant("integ_lag_zero", {"K": spec.K_yaw, "tz": spec.tz_yaw, "tau": tau, "T": T}, spec.w_hover, 3.8 * spec.cells)


def default_tune(spec: CraftSpec, target_pm: float = 40.0, target_gm_db: float = 6.0) -> Tune:
    """A stable, deliberately conservative starting tune for the twin.

    Filters come from the class overrides; PID gains keep Betaflight's default P:I:D:Dmax
    ratios (45:80:30:40 roll, yaw P:I 45:80) and are scaled to the largest value that still
    gives PM >= target and GM >= target at hover on the true plant, then backed off 10 % (typical pilot-tune margins).
    """
    from ..analysis.loop import evaluate
    from ..model.controller import OperatingPoint

    base = {k: v for k, v in spec.tune.items() if not k.startswith(("p_", "i_", "d_", "d_max_", "f_"))}
    t = Tune.from_config(Config(values={k: str(v) for k, v in base.items()}))
    t.set("motor_poles", spec.motor_poles)
    t.set("dyn_notch_count", 1)
    ratios = {"roll": (45, 80, 30, 40), "pitch": (47, 84, 34, 46), "yaw": (45, 80, 0, 0)}
    op = OperatingPoint(throttle=spec.hover_throttle, motor_hz=list(_spread(spec) * spec.w_hover))
    for axis, name in enumerate(("roll", "pitch", "yaw")):
        plant = true_plant(spec, axis)
        best = None  # (fc, gains)
        rp, ri, rd, rdm = ratios[name]
        for dmul in ((1.0,) if rd == 0 else (0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.0)):
            for scale in np.geomspace(0.05, 4.0, 45):
                g = (max(1, round(rp * scale)), max(1, round(ri * scale)),
                     round(rd * scale * dmul), round(rdm * scale * dmul))
                t.update(**{f"p_{name}": g[0], f"i_{name}": g[1], f"d_{name}": g[2], f"d_max_{name}": max(g[2], g[3])})
                m, _, _ = evaluate(t, axis, plant, op, 1 / spec.loop_hz, spec.loop_hz)
                if np.isfinite(m.pm) and m.pm >= target_pm and m.gm_db >= target_gm_db and (best is None or m.fc > best[0]):
                    best = (m.fc, g)
        g = best[1] if best else (rp, ri, rd, rdm)
        g = tuple(max(1, int(round(x * 0.9))) if x else 0 for x in g)
        t.update(**{f"p_{name}": g[0], f"i_{name}": g[1], f"d_{name}": g[2], f"d_max_{name}": max(g[2], g[3]),
                    f"f_{name}": 100})
    return t


def _pilot_profile(spec: CraftSpec, n: int, dt: float, rng) -> tuple[np.ndarray, np.ndarray]:
    """Freestyle-like stick and throttle inputs: rate steps on all axes, punch-outs and chops."""
    sp = np.zeros((n, 3))
    thr = np.zeros(n)
    t = 0
    level = spec.hover_throttle
    while t < n:
        seg = int(rng.uniform(0.25, 0.6) / dt)
        target = rng.choice([0.05, spec.hover_throttle, 0.5, 0.75, 0.95], p=[0.2, 0.35, 0.2, 0.15, 0.1])
        ramp = np.linspace(level, target, min(seg, n - t))
        thr[t : t + len(ramp)] = ramp
        level = target
        ax = rng.integers(0, 3)
        rate = rng.uniform(150, 500) * rng.choice([-1, 1]) * (0.6 if ax == 2 else 1.0)
        on = int(rng.uniform(0.1, 0.3) / dt)
        sp[t : t + min(on, n - t), ax] = rate
        t += seg
    return sp, np.clip(thr, 0.0, 1.0)


def simulate(
    spec: CraftSpec,
    tune: Tune | None = None,
    chirp_axes: tuple[int, ...] = (0, 1, 2),
    chirp_repeats: int = 1,
    chirp_s: float = 20.0,
    f0: float = 1.0,
    f1: float | None = None,
    amplitude: tuple[float, float, float] | None = None,
    hover_s: float = 8.0,
    freestyle_s: float = 12.0,
    seed: int = 0,
    high_resolution: bool = False,
) -> Flight:
    """Hover, chirp runs at hover throttle, then freestyle (stick steps, punch-outs, chops).

    Returns a Flight sampled like a blackbox log. Chirp end frequency and amplitude default to
    the craft class (see skills/bf-flight-protocol).
    """
    rng = np.random.default_rng(seed)
    f1 = f1 or spec.chirp_f1
    amplitude = amplitude or spec.chirp_amp
    tune = tune or default_tune(spec)
    dt = 1.0 / spec.loop_hz
    lag, lead = 3.0, 30.0
    b0, b1, a1 = flt.chirp_leadlag(lag, lead, dt)
    # schedule
    segs: list[tuple[int, float]] = [(-1, hover_s)]
    for _ in range(chirp_repeats):
        for ax in chirp_axes:
            segs.append((ax, chirp_s))
            segs.append((-1, 1.0))
    if freestyle_s > 0:
        segs.append((-2, freestyle_s))
    n_total = int(sum(d for _, d in segs) * spec.loop_hz)
    axis_sched = np.concatenate([np.full(int(d * spec.loop_hz), a) for a, d in segs])[:n_total]
    n_total = len(axis_sched)

    # ---- plant constants from targets (linearized at hover) ----
    thr_h = spec.hover_throttle
    gamma = spec.gamma
    spread = _spread(spec)
    wh = spec.w_hover
    # omega_ss_i(c) = spread_i*(idle + (max-idle)*c^gamma); slope at hover
    g_h = (spec.max_hz - spec.idle_hz) * gamma * thr_h ** (gamma - 1)
    # rate_dot = sum_i m_i*kT*omega_i^2 -> K = kT*2*wh*g_h*sum(spread^2)/1000 (|m_i| = 1)
    ss2 = float(np.sum(spread**2))
    kT = {0: spec.K_roll * 1000 / (2 * wh * g_h * ss2), 1: spec.K_pitch * 1000 / (2 * wh * g_h * ss2)}
    # yaw: drag kQ*omega^2 + reaction Jr*domega/dt ; tz = Jr/(2 kQ wh)
    kQ = spec.K_yaw * 1000 / (2 * wh * g_h * ss2)
    Jr = spec.tz_yaw * 2 * kQ * wh
    # quad-X mixer (betaflight motor order: RR, FR, RL, FL): roll, pitch, yaw signs
    mix = np.array([[-1, 1, -1], [-1, -1, 1], [1, 1, 1], [1, -1, -1]], dtype=float)

    # ---- controller state ----
    kp = [tune.kp(a) for a in range(3)]
    ki = [tune.ki(a) for a in range(3)]
    kd = [tune.kd(a) for a in range(3)]
    lpf2 = [flt.make_lowpass(tune.s("gyro_lpf2_type"), tune.i("gyro_lpf2_static_hz"), dt) for _ in range(3)]
    lpf1_hz = tune.i("gyro_lpf1_dyn_min_hz") or tune.i("gyro_lpf1_static_hz")
    if tune.i("gyro_lpf1_dyn_min_hz") > 0:
        lpf1_hz = flt.dyn_lpf_cutoff(tune.i("gyro_lpf1_dyn_min_hz"), tune.i("gyro_lpf1_dyn_max_hz"), tune.i("gyro_lpf1_dyn_expo"), thr_h)
    lpf1 = [flt.make_lowpass(tune.s("gyro_lpf1_type"), lpf1_hz, dt) for _ in range(3)]
    d1_hz = tune.i("dterm_lpf1_dyn_min_hz") or tune.i("dterm_lpf1_static_hz")
    if tune.i("dterm_lpf1_dyn_min_hz") > 0:
        d1_hz = flt.dyn_lpf_cutoff(tune.i("dterm_lpf1_dyn_min_hz"), tune.i("dterm_lpf1_dyn_max_hz"), tune.i("dterm_lpf1_dyn_expo"), thr_h)
    dlp1 = [flt.make_lowpass(tune.s("dterm_lpf1_type"), d1_hz, dt) for _ in range(3)]
    dlp2 = [flt.make_lowpass(tune.s("dterm_lpf2_type"), tune.i("dterm_lpf2_static_hz"), dt) for _ in range(3)]
    ylp = flt.PTn(tune.i("yaw_lowpass_hz"), dt, 1) if tune.i("yaw_lowpass_hz") > 0 else (lambda x: x)
    harm = tune.i("rpm_filter_harmonics")
    rq = tune.i("rpm_filter_q") / 100.0
    rmin, rfade = tune.i("rpm_filter_min_hz"), tune.i("rpm_filter_fade_range_hz")
    rpm_notches = [[[flt.SvfNotch(max(h * wh, 1.0), rq, dt, 0.0) for h in range(1, harm + 1)] for _ in range(4)] for _ in range(3)]
    dn_count = tune.i("dyn_notch_count") if spec.loop_hz >= 2000 else 0
    dn_center = min(max(spec.frame_mode_hz or 0.9 * wh, tune.i("dyn_notch_min_hz")), tune.i("dyn_notch_max_hz"))
    dyn_notches = [[flt.SvfNotch(dn_center, tune.i("dyn_notch_q") / 100.0, dt) for _ in range(dn_count)] for _ in range(3)]
    dyn_g = tune.i("gyro_lpf1_dyn_min_hz") > 0
    dyn_d = tune.i("dterm_lpf1_dyn_min_hz") > 0
    kf = [tune.kf(a) for a in range(3)]
    rc_smooth = [flt.PTn(60.0, dt, 3) for _ in range(3)]
    ff_smooth = [flt.PTn(40.0, dt, 1) for _ in range(3)]
    prev_sp_rc = [0.0, 0.0, 0.0]
    relax_cut = tune.i("iterm_relax_cutoff")
    relax_axes = {0, 1, 2} if tune.s("iterm_relax").startswith("RPY") else ({0, 1} if tune.s("iterm_relax").startswith("RP") else set())
    relax_lpf = [flt.PTn(relax_cut, dt, 1) for _ in range(3)]
    amp = amplitude

    # ---- buffers ----
    d_esc = max(1, int(round(spec.T_esc * spec.loop_hz)))
    d_gyro = max(0, int(round(spec.T_gyro * spec.loop_hz)))
    cmd_hist = np.zeros((d_esc + 1, 4))
    rate_hist = np.zeros((d_gyro + 1, 3))
    omega = spread * wh
    rate = np.zeros(3)
    I = [0.0, 0.0, 0.0]
    prevD = [0.0, 0.0, 0.0]
    chirp_filt_state = [0.0, 0.0]  # x1, y1
    last_axis = -1
    chirp_count = 0
    beta = (f1 / f0) ** (1.0 / chirp_s)
    k0 = 2 * math.pi / math.log(beta)
    k1 = k0 * f0
    N_chirp = int(chirp_s * spec.loop_hz)
    # noise sources
    frame_bp = flt.SvfNotch(spec.frame_mode_hz or 100.0, 5.0, dt) if spec.frame_mode_amp > 0 else None
    line_phase = rng.uniform(0, 2 * math.pi, (4, len(spec.noise_lines), 3))

    step = spec.log_ratio
    n_log = n_total // step
    out = {k: np.zeros((n_log, 3)) for k in ("gyro", "gyro_unfilt", "setpoint", "P", "I", "D", "F")}
    out_motor = np.zeros((n_log, 4))
    out_hz = np.zeros((n_log, 4))
    out_thr = np.zeros(n_log)
    out_dbg = np.zeros((n_log, 8))
    li = 0
    white = rng.normal(0, spec.noise_white, (n_total, 3))
    frame_noise = rng.normal(0, 1, (n_total, 3)) if frame_bp else None
    thr_wander = thr_h + 0.004 * np.cumsum(rng.normal(0, 1, n_total)) / math.sqrt(spec.loop_hz)
    thr_wander = thr_h + np.clip(thr_wander - thr_h, -0.02, 0.02)
    n_free = int(np.sum(axis_sched == -2))
    pilot_sp, pilot_thr = _pilot_profile(spec, max(n_free, 1), dt, rng)
    free_idx = np.cumsum(axis_sched == -2) - 1
    for n in range(n_total):
        ax_ch = int(axis_sched[n])
        free = ax_ch == -2
        if free:
            ax_ch = -1
            thr_now = pilot_thr[free_idx[n]]
            sp_raw = pilot_sp[free_idx[n]]
        else:
            thr_now = thr_wander[n]
            sp_raw = (0.0, 0.0, 0.0)
        tpa = tune.tpa_factor(thr_now)
        if n % max(1, int(0.005 * spec.loop_hz)) == 0:  # dyn LPF update (<= every 5 ms)
            if dyn_g:
                fc = flt.dyn_lpf_cutoff(tune.i("gyro_lpf1_dyn_min_hz"), tune.i("gyro_lpf1_dyn_max_hz"), tune.i("gyro_lpf1_dyn_expo"), thr_now)
                for f_ in lpf1:
                    if hasattr(f_, "set_cutoff"):
                        f_.set_cutoff(fc, dt)
            if dyn_d:
                fc = flt.dyn_lpf_cutoff(tune.i("dterm_lpf1_dyn_min_hz"), tune.i("dterm_lpf1_dyn_max_hz"), tune.i("dterm_lpf1_dyn_expo"), thr_now)
                for f_ in dlp1:
                    if hasattr(f_, "set_cutoff"):
                        f_.set_cutoff(fc, dt)
        # ---- chirp generator (chirp.c) ----
        if ax_ch >= 0:
            if ax_ch != last_axis:
                chirp_count = 0
            if chirp_count < N_chirp:
                fch = f0 * beta ** (chirp_count * dt)
                sinarg = math.fmod(k0 * fch - k1, 2 * math.pi)
                exc = math.cos(sinarg)
                if fch < 1.0:
                    exc *= fch
                chirp_count += 1
            else:
                fch, sinarg, exc = 0.0, 0.0, 0.0
        else:
            fch, sinarg, exc = 0.0, 0.0, 0.0
        last_axis = ax_ch
        x1, y1 = chirp_filt_state
        yf = b0 * exc + b1 * x1 - a1 * y1
        chirp_filt_state = [exc, yf]

        # ---- sensor ----
        rate_hist[1:] = rate_hist[:-1]
        rate_hist[0] = rate
        meas_rate = rate_hist[d_gyro]
        noise = white[n].copy()
        for m in range(4):
            for h, a_h in enumerate(spec.noise_lines):
                amp_h = a_h * (omega[m] / wh) ** 2
                noise += amp_h * np.sin(2 * math.pi * (h + 1) * omega[m] * n * dt + line_phase[m, h])
        if frame_bp is not None:
            for a in range(3):
                noise[a] += spec.frame_mode_amp * 8 * (frame_noise[n, a] - frame_bp(frame_noise[n, a]))
        g_unf = meas_rate + noise
        u = [0.0, 0.0, 0.0]
        gf_all = [0.0, 0.0, 0.0]
        for a in range(3):
            x = lpf2[a](g_unf[a])
            for m in range(4):
                for h, nt in enumerate(rpm_notches[a][m]):
                    fc = min(max((h + 1) * omega[m], rmin), 0.48 * spec.loop_hz)
                    w = min(max((fc - rmin) / rfade, 0.0), 1.0) if rfade > 0 else 1.0
                    if (n + m + h) % 6 == 0:  # round-robin update like rpm_filter.c
                        nt.update(fc, rq, dt, w)
                    x = nt(x)
            x = lpf1[a](x)
            for dn in dyn_notches[a]:
                x = dn(x)
            gf = x
            gf_all[a] = gf
            sp_rc = rc_smooth[a](sp_raw[a])
            ffv = kf[a] * ff_smooth[a]((sp_rc - prev_sp_rc[a]) / dt)
            prev_sp_rc[a] = sp_rc
            sp = sp_rc + (amp[a] * yf if a == ax_ch else 0.0)
            err = sp - gf
            P = kp[a] * err
            if a == 2:
                P = ylp(P)
            ie = err
            if a in relax_axes:
                hp = abs(sp - relax_lpf[a](sp))
                ie *= max(0.0, 1.0 - hp / 40.0)
            I[a] += ki[a] * dt * ie
            lim = 0.8 * (400 if a == 2 else 500)
            I[a] = min(max(I[a], -lim), lim)
            dd = dlp2[a](dlp1[a](gf))
            D = -kd[a] * tpa * (dd - prevD[a]) / dt
            prevD[a] = dd
            s = P + I[a] + D + ffv
            lim2 = 400 if a == 2 else 500
            u[a] = min(max(s, -lim2), lim2)
            if n % step == 0 and li < n_log:
                out["setpoint"][li, a] = sp
                out["P"][li, a], out["I"][li, a], out["D"][li, a], out["F"][li, a] = P, I[a], D, ffv
        # ---- mixer + motors ----
        thr = thr_now
        cmd = thr + mix @ (np.array(u) / 1000.0)
        cmd = np.clip(cmd, 0.0, 1.0)
        cmd_hist[1:] = cmd_hist[:-1]
        cmd_hist[0] = cmd
        c_d = cmd_hist[d_esc]
        w_ss = (spec.idle_hz + (spec.max_hz - spec.idle_hz) * np.power(np.maximum(c_d, 1e-4), gamma)) * spread
        tau = 1.0 / (spec.tau_c0 + spec.tau_c1 * omega)  # (the rigid-body model uses exact omega^2)
        domega = (w_ss - omega) / tau
        omega = omega + domega * dt
        # ---- rigid body ----
        acc = np.zeros(3)
        for a in (0, 1):
            acc[a] = kT[a] * np.sum(mix[:, a] * (omega**2 - (spread * wh) ** 2)) - spec.aero_damping * rate[a]
        acc[2] = kQ * np.sum(mix[:, 2] * (omega**2 - (spread * wh) ** 2)) + Jr * np.sum(mix[:, 2] * domega) - 0.5 * rate[2]
        rate = rate + acc * dt
        if n % step == 0 and li < n_log:
            q = 10.0 if high_resolution else 1.0
            out["gyro"][li] = np.round(np.array(gf_all) * q) / q
            out["gyro_unfilt"][li] = np.round(g_unf * q) / q
            out["setpoint"][li] = np.round(out["setpoint"][li] * q) / q
            out_motor[li] = cmd
            q_hz = 100.0 / 60.0 / (spec.motor_poles / 2.0)  # eRPM/100 telemetry resolution
            out_hz[li] = np.round(omega / q_hz) * q_hz
            out_thr[li] = thr
            out_dbg[li, 0] = round(5000 * sinarg) if ax_ch >= 0 else 0
            out_dbg[li, 1] = ax_ch
            out_dbg[li, 2] = round(10 * fch)
            out_dbg[li, 3] = round(1000 * exc)
            li += 1
    fl = _to_flight(spec, tune, out, out_motor, out_hz, out_thr, out_dbg, n_log, f0, f1, chirp_s, amp)
    fl.log.headers["chirp_amplitude_roll"] = str(int(amp[0]))
    return fl


def _to_flight(spec, tune, out, motor, hz, thr, dbg, n, f0, f1, chirp_s, amp) -> Flight:
    fs = spec.loop_hz / spec.log_ratio
    t = np.arange(n) / fs
    lo, hi = 158, 2047
    motor_raw = np.round(lo + motor[:n] * (hi - lo))
    headers = {
        "looptime": str(int(round(1e6 / spec.loop_hz))),
        "pid_process_denom": "1",
        "P interval": str(spec.log_ratio),
        "I interval": "32",
        "motorOutput": f"{lo},{hi}",
        "debug_mode": "96",
        "chirp_frequency_start_deci_hz": str(int(f0 * 10)),
        "chirp_frequency_end_deci_hz": str(int(f1 * 10)),
        "chirp_time_seconds": str(int(chirp_s)),
        "Firmware revision": "Betaflight 2026.6.2 (synthetic)",
        "Craft name": f"synthetic {spec.name}",
    }
    log = Log(0, headers, [], np.zeros((n, 0)), [], np.zeros((0, 0)), np.zeros(0), [], {})
    vals = dict(tune.values)
    vals["motor_poles"] = str(spec.motor_poles)
    cfg = Config(values=vals, firmware_version="2026.6.2", craft_name=f"synthetic {spec.name}")
    mode = np.ones(n, dtype=np.int64)  # armed, acro
    mode[dbg[:n, 1] >= 0] |= 1 << 6
    return Flight(
        log=log, cfg=cfg, t=t, fs=fs, loop_hz=spec.loop_hz, log_ratio=spec.log_ratio,
        gyro=out["gyro"][:n], gyro_unfilt=out["gyro_unfilt"][:n], setpoint=out["setpoint"][:n], throttle=thr[:n],
        P=np.round(out["P"][:n]), I=np.round(out["I"][:n]), D=np.round(out["D"][:n]), F=out["F"][:n],
        motor=motor[:n], motor_raw=motor_raw, motor_hz=hz[:n], vbat=np.full(n, 3.8 * spec.cells),
        rc=np.zeros((n, 4)), debug=dbg[:n], mode_mask=mode,
    )
