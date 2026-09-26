---
name: ask-first
description: First reply must be the questions (diff all, ranked priorities, motor temperature), not a long analysis
tags: [interview]
runs: 1
max_turns: 12
timeout_seconds: 600
allowed_tools: [Bash, Read, Write, Edit, Glob, Grep, Skill]
---

In the folder "1" is a log from my 5 inch 6S freestyle quad (a synthetic bftune test log). Can you please optimize the tune? Do you need anything else?
