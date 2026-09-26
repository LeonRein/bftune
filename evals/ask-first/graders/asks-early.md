---
type: llm
focus: last_message
---
Judge only the questions the agent put to the user, not whether its tools worked (tool or environment problems
reported in the same reply are fine).

PASS if this reply to the user asks for all of:
1. a fresh `diff all` (or CLI dump);
2. what the new tune should prioritise, as a ranking or pick-your-top-few (e.g. stick response, locked-in,
   propwash, smoothness, punch-outs, efficiency/cool motors) - asking only "what do you dislike" does not count;
3. the motor temperature after the logged flight;
4. changes or hardware issues since the log.

FAIL if any of the four is missing, or if the reply delivers a tune instead of asking. (That no experiments ran
before the questions is checked by a separate grader.)
