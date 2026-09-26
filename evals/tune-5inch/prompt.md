---
name: tune-5inch
description: Plain-language tune request on a synthetic 5" twin with chirps
tags: [tune, chirp]
runs: 1
max_turns: 150
timeout_seconds: 2400
allowed_tools: [Bash, Read, Write, Edit, Glob, Grep, Skill, Agent]
---

Hey, can you tune my 5 inch? It's a 6S freestyle build. The blackbox log is in fpv/5inch/flight1.pkl
(a synthetic test log exported by bftune, no separate CLI dump). I flew chirps on all axes and then
some freestyle. Motors came down cool. It feels a bit soft and propwash in dives bugs me. I mostly fly
freestyle, sometimes a bit of racing with friends.

I won't be around to answer questions, so make reasonable assumptions and note them. Put the quad's
project folder in fpv/5inch/project.
