---
name: hk-review
description: Independent review of the current change by the read-only hk-reviewer subagent, using the evaluator rubric and running the verification itself. Use before `verify`, before a commit or PR, when the user asks "review this", "is this done?", or types /hk-review.
argument-hint: "[feature-id | --diff <base>]"
---

# Independent review

The maker never grades its own work: agents systematically overrate their own output.

1. Find the target: the active feature (`python scripts/harness.py status`), the id the user gave, or the diff
   against the base they named.
2. Delegate to the **hk-reviewer** subagent. Give it only: the feature id (or the diff base) and, if present, the path
   of the sprint contract. Do not give it your reasoning, a summary of the change, or the expected verdict.
3. Relay the verdict to the user unchanged: PASS/FAIL, the issues with location and fix, and the commands it ran.
4. FAIL → fix and review again (a fresh reviewer each time). Repeated similar findings across features → propose a
   machine check in `.harness/rules.json` (pattern + why + fix) so the harness catches it next time.
5. PASS on an active feature → `/hk-verify <id>`.

If the rubric (`docs/harness/evaluator-rubric.md`) is missing, the reviewer uses its built-in checks; suggest adding
the rubric with `/hk-docs`.
