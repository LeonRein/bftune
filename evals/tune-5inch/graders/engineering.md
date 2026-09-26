---
type: llm
focus: last_message
---
Judge the agent's final answer to the pilot. PASS only if all of these hold:
1. It gives a paste-ready Betaflight CLI block (or points to the tune_cli.txt it wrote) and a revert block or revert file.
2. Every changed setting comes with a short reason tied to evidence (model margins, noise, flight data or the pilot's complaint), not generic advice.
3. It addresses the pilot's two complaints (soft feel, propwash in dives) with a stated mechanism.
4. It states that the tune passed the safety gate (PASS) and describes predictions as model predictions.
5. It lists the assumptions it made because the pilot was unavailable, and gives first-flight checks.
