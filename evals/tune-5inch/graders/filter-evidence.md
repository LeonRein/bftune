---
type: llm
focus: trace
---
PASS if, before changing any dynamic-notch or gyro/D-term lowpass setting, the agent looked at the
noise evidence (`bftune noise` or the noise section of `bftune brief`) and justified the filter
decision with it (persistent non-RPM peaks or their absence, noise vs the proven-safe level).
FAIL if filters were changed without consulting the noise report.
