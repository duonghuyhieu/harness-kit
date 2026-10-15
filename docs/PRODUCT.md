# Product — harness

> What agents **cannot** infer from the code: who uses it, for what, and what "correct" means.
> Ambiguous behavior is a spec gap, not a license to guess.

## Core

- Users: developers/teams with legacy repositories who want coding agents to work on them reliably.
- Job: make a repo agent-ready (map, commands, state, verification), then optimize code safely with evidence.
- Pain removed: agents guessing setup, drifting out of scope, declaring victory early, losing state between sessions.
- Quality bar: never destroys user files; every generated rule is checkable by a command.

## Golden journeys (main user flows)

1. **Audit:** `python harness.py audit <repo>` → markdown report with score, bottleneck, next priorities. Verified by `AuditTests`.
2. **Retrofit:** `python harness.py retrofit <repo>` → harness files created, existing files only proposed. Verified by `RetrofitTests`.
3. **Feature loop:** `start` → `next` → work → checker review → `verify <id>` → `handoff` → `exit-check`. Verified by `RuntimeTests`, `GapFeatureTests`.
4. **Agent guardrail (opt-in, `retrofit --claude`):** Claude Code Stop hook blocks finishing on debug code/junk/invalid feature list. Verified by `test_claude_guardrails_opt_in` and the `test_stop_hook_*` tests.

## Product rules

- Prefer user-visible reliability over feature count.
- No hidden destructive actions; no silent failures.
- Changing user-visible behavior → update this file in the same session.

## Out of scope

- No code changes to the user's product during retrofit (only harness files and tests).
- No graph/orchestration engine (lesson 14: only worth it when ≥ 3 of 5 criteria hold).
- No app-level logging/OTel injection (stack-specific; the skill guides it instead).
