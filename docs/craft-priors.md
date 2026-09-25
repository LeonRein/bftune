# Craft priors (sanity ranges)

These are rough ranges for identified parameters, used to sanity-check results and to seed the
synthetic twins (`bftune synth`). A result far outside its class usually points to a problem:
a damaged prop, a loose FC or gyro, motor desync, or a wrong `motor_poles`.

| class | loop | hover motor Hz | K roll [°/s² per pidSum] | motor τ at hover | delay T | yaw zero 1/(2π t_z) | typical crossover |
|---|---|---|---|---|---|---|---|
| 1S tinywhoop 65-75 mm | 4-8 kHz | 500-900 | 150-400 | 20-40 ms | 1.2-2.5 ms | 3-6 Hz | 10-25 Hz |
| 2.5-3.5" (4S) | 8 kHz | 180-300 | 80-200 | 12-25 ms | 1.0-2.0 ms | 1-2 Hz | 15-35 Hz |
| 5" (6S) | 8 kHz | 130-220 | 60-150 | 18-35 ms | 1.0-2.0 ms | 0.8-1.5 Hz | 15-35 Hz |
| 7-10" | 2-4 kHz | 60-130 | 20-60 | 40-90 ms | 1.5-3 ms | 0.4-1 Hz | 6-15 Hz |

Measured reference (3.5" 4S, 1960 kV, FLYWOOH743PRO, BF 2026.6.2):
roll K=130, pitch K=96 (thrust_linear 0), τ = 19 ms at 230 Hz, T = 1.3-1.7 ms; yaw K=11,
t_z = 0.11 s; noise dominated by the motor fundamental, which the RPM filter removes.

Noise and filter tendencies:
- Whoops: high motor frequency, a noisy frame, and brushed or low-pole motors. The RPM filter
  often has no telemetry (Bluejay adds it).
- 5" freestyle: frame resonances at 150-300 Hz are common, so one dynamic notch pays off.
- 7-10": motor frequencies of 60-150 Hz overlap the control band. Keep `rpm_filter_min_hz` low
  (50-60) and expect larger delays. Structural modes can sit close to crossover.
