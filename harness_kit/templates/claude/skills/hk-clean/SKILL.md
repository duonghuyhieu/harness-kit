---
name: hk-clean
description: Restore and keep a clean state — no debug code, junk files, tracked secrets, scope drift or stale docs — and periodically simplify the harness itself (remove components that no longer earn their place). Use before ending a session, before a release, when the repo feels messy, or when the user types /hk-clean.
argument-hint: "[--harness]"
---

# Clean state

Clean state is part of "done": the build and tests pass, progress is recorded, nothing temporary is left, and the next
session can start with the standard command.

## Repo
1. `python scripts/harness.py exit-check` and fix every ERROR: leftover debug code in changed lines, temporary/junk
   files (`*.tmp`, `*.bak`, `scratch.*`, …), tracked `.env` files, a broken smoke command, PROGRESS.md not updated,
   changes outside the active feature's scope.
2. WARNINGs: TODO/FIXME added this session → turn the important ones into features; uncommitted work → commit the
   verified part or document the rest in PROGRESS.md.
3. Cleanup must be idempotent and safe: delete only what the check names, and ask before deleting anything the user
   may have created on purpose.

## Harness (with `--harness`, or every few weeks)
Harnesses rot like code. For each harness component (an AGENTS.md section, a rule, a hook, a doc, a check step) ask:
did it prevent a failure recently (`python scripts/harness.py failures`, `trace`)? Is it duplicated elsewhere?
Would a fresh session miss it? Propose removing or merging the ones that do not earn their place, as an ablation:
remove one at a time, run the usual work, and watch for the failure it was meant to prevent. Record each decision
in the simplification log of `docs/QUALITY.md` and in `docs/DECISIONS.md`.
