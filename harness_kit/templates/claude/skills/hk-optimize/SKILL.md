---
name: hk-optimize
description: Safely optimize or refactor legacy code in a harnessed project — pick a target from docs/QUALITY.md, pin behavior with characterization tests, change in small steps, get an independent review, then pass the one-way pass-gate. Use when the user says "optimize the code", "refactor", "pay down tech debt", "clean up module X", or types /hk-optimize.
argument-hint: "[module or feature-id]"
---

# Optimize legacy code

Follow `docs/harness/legacy-refactor-playbook.md` when present. The loop:

1. `/hk-start` — a red baseline is fixed first.
2. **Target**: the module the user named, else the lowest-graded module or top hotspot in `docs/QUALITY.md`. Reuse a
   matching not_started `refactor` feature if one exists.
3. **Feature** (if missing): `python scripts/harness.py add --id R0xx --category refactor --behavior "<measurable goal>"
   --verify "<characterization tests> && python scripts/harness.py size <file> --max-lines N" --scope <globs>
   --out-of-scope "<public API, behavior>"`. The verification must measure the goal (size, complexity, benchmark,
   coverage), not only "tests pass".
4. `python scripts/harness.py next --id <ID>` (WIP=1).
5. **Characterization tests first**, passing on the old code, committed separately.
6. **Small steps**; run the tests after each; red → revert that step.
7. **Independent review** by **hk-reviewer** (refactor-specific: characterization tests existed before the change and
   passed on old code; public API unchanged; no behavior change mixed in).
8. `python scripts/harness.py verify <ID>` — only after the review passes.
9. Update `docs/QUALITY.md` (grade, history), `handoff`, commit with the feature id, `exit-check`.

Never mix refactoring with behavior changes (a bug found → a separate feature); never loosen tests; parallel
targets only in separate git worktrees.
