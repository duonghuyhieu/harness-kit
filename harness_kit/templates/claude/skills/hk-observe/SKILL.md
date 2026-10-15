---
name: hk-observe
description: Make the system and the agent's process observable — runtime signals (structured logs, health checks, golden paths in docs/RELIABILITY.md) and process signals (harness trace, activity log, loop run reports, sprint contracts, rubric). Use when debugging is guesswork, the user asks to add logging/tracing, wants to know what an agent actually did, or types /hk-observe.
argument-hint: "[runtime | process]"
---

# Observability inside the harness

An agent can only fix what it can see. Two layers:

## Runtime (the product)
1. Read `docs/RELIABILITY.md`. Missing or thin → fill: golden user journeys, how to start the app, where logs go, how
   to check health, the known failure signatures.
2. Logs the agent can query: structured (JSON lines with timestamp, level, event, request/trace id, key fields),
   written to a predictable local path, no secrets. Prefer the stack's standard logger; add a correlation id at the
   entry points of the golden paths.
3. Make the golden paths machine-checkable: a smoke command (`commands.smoke`) and, for cross-component flows, an
   e2e script (`commands.e2e`, run by `check --full`).
4. Each addition is a feature with a verification command (`/hk-plan`), e.g. "a failed login logs `auth.failed`
   with a request id" verified by a test.

## Process (the agent's work)
- `python scripts/harness.py trace -n 40` — harness actions (checks, verifies, hooks).
- `.harness/activity.jsonl` — files and commands each session touched (PostToolUse hook).
- `.harness/last-check.json` — the latest check per step, with output tails.
- `python scripts/harness.py report [--list]` — loop runs with a theory checklist computed from the log.
- `python scripts/harness.py failures` — observed failures by harness layer.
- Sprint contracts and the evaluator rubric make the expected outcome observable before work starts.

Report what is observable now, what is missing, and the smallest additions that close the gap.
