---
name: hk-implement
description: Implement exactly one harness feature end to end — activate it (WIP=1), build it inside its scope, run its verification, get an independent review from hk-reviewer, then pass the pass-gate with `verify`. Use when the user asks to build, implement or fix something that is (or should be) a feature, or types /hk-implement.
argument-hint: "[feature-id]"
---

# Implement one feature

## 1. Activate
- `python scripts/harness.py status`. A feature is active → that is the one (WIP=1), whatever was asked.
- Otherwise `python scripts/harness.py next` (or `next --id <ID>` for the one the user named). No matching feature
  → run `/hk-plan` first: there is no work without a verification command.

## 2. Build (maker)
- Small feature: implement it in this session. Large feature or a long session: delegate to the **hk-maker** subagent
  for a fresh context, giving it the feature id.
- Stay inside the feature's `scope`. Something else needs changing → note it and `add` it as a new feature later.
- Write or update the test named by the verification command first when you can, then the code.
- Run `python scripts/harness.py try <ID>` (no state change) and `python scripts/harness.py check` until both pass.
  3 failed attempts on the same error → change approach or `/hk-diagnose`; never weaken the test.

## 3. Independent review (checker)
- Delegate to the **hk-reviewer** subagent with only the feature id. Do not pass your reasoning or a summary of why it
  is correct — it must judge from the diff and the commands.
- FAIL → fix the listed issues (back to step 2) and review again. PASS → step 4.

## 4. Pass-gate
- `python scripts/harness.py verify <ID>` — it runs the verification command and the project check, and records the
  evidence. Only the harness moves a feature to `passing`, and `passing` is one-way, which is why the review comes first.
- Verify fails → the feature stays active; read the output and return to step 2.

## 5. Close
- Commit the verified work with the feature id and the *why* (ask the user before committing if they have not
  pre-approved commits).
- `python scripts/harness.py handoff` and update PROGRESS.md; record new decisions in `docs/DECISIONS.md`.
- Continue with the next feature only if context and time allow; otherwise `/hk-handoff`.
