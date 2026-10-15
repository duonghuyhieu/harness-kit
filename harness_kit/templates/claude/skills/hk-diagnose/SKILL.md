---
name: hk-diagnose
description: Diagnostic loop for failures — reproduce, find the root cause with evidence, attribute it to one harness layer (instructions, tools, environment, state, feedback), log it, and fix that layer so the failure cannot recur. Use when a check or verify fails unexpectedly, an agent keeps failing at a task, a session went wrong, or the user types /hk-diagnose.
argument-hint: "[what went wrong]"
---

# Diagnose: fix the harness, not the model

Changing the model is the most expensive and least likely fix. A capable model failing on a well-structured task
usually means the harness let it fail.

1. **Reproduce** the failure with the exact command. For anything non-trivial, delegate to the **hk-debugger**
   subagent with the symptom and the command.
2. **Evidence:** `.harness/last-check.json`, `python scripts/harness.py trace -n 40`, `.harness/activity.jsonl`,
   loop reports (`python scripts/harness.py report`), and the app logs named in docs/RELIABILITY.md.
3. **Attribute** to one layer and log it:
   `python scripts/harness.py fail --layer <instructions|tools|environment|state|feedback> --note "<concrete>" [--feature <id>]`
4. **Fix the layer:**
   - instructions → a sharper feature behavior, a convention written into docs/, a hard constraint with its source
   - tools → a correct command in `.harness/config.json`, a missing permission
   - environment → init.sh, a pinned version, a lockfile
   - state → PROGRESS.md / DECISIONS.md / feature list corrections (status only through the runtime)
   - feedback → a test, a sharper verification command, a rule in `.harness/rules.json`
5. **Re-run** the original task and confirm the failure is gone. `python scripts/harness.py failures` shows which layer
   is the bottleneck over time; put the next harness investment there.
