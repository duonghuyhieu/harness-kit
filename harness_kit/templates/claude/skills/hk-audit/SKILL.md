---
name: hk-audit
description: Score the harness of this project on its five subsystems (instructions, tools, environment, state, feedback), name the bottleneck from observed failures, and propose the smallest next artifact to add. Use when the user asks "how good is our harness", "what should we improve", before/after a retrofit, or types /hk-audit.
---

# Harness audit

1. Measure:
   - Kit available: `python "{{KIT_ROOT}}/harness.py" audit .` (5 × 5 checks, Fresh Session Test, clean-state scan).
   - Always: `python scripts/harness.py status`, `python scripts/harness.py docs --strict`,
     `python scripts/harness.py failures` (the bottleneck by *observed* failures), `python scripts/harness.py report --list`.
2. Fresh Session Test — answer from the repo alone, citing files: what is this system, how is it organized, how do I
   run it, how do I verify it, where are we now. Every unanswered question is a finding.
3. Map each observed failure mode to the first artifact that fixes it (method map):

| Failure mode | First fix |
|---|---|
| Each session starts from zero | PROGRESS.md handoff sections, `start` |
| Scope creep, several things half done | feature list with WIP=1 and `--scope` |
| "Done" claimed too early | verification commands + `verify` pass-gate + independent review |
| Fragile or unknown startup | init.sh / init.ps1 + `commands.setup/smoke` |
| Weak handoffs | docs/harness/session-handoff.md, `handoff` |
| Subjective reviews | docs/harness/evaluator-rubric.md |
| Recurring review comment | a rule in `.harness/rules.json` |

4. Report: score per subsystem, the bottleneck (observed failures beat scores), and at most 3 changes ordered by
   expected value. Add the smallest artifact for a failure you have actually seen — do not grow AGENTS.md instead.
   Each change can become a `harness` feature (`/hk-plan`).
