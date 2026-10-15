---
name: hk-start
description: Clock in to a session on a harnessed project — rebuild context from the repo (not chat history), check the baseline, and pick exactly one feature. Use at the start of every work session, after /clear, or when the user says "let's continue", "where were we", or types /hk-start.
---

# Clock-in

The goal is to be productive within minutes and to start from facts on disk.

1. `python scripts/harness.py start` — prints the next steps from PROGRESS.md, recent commits, the feature status, an
   uncommitted-changes warning, and runs the quick check.
2. Read `AGENTS.md` (hard constraints), then `PROGRESS.md` (Known Issues, Next Steps) and the newest entries of
   `docs/DECISIONS.md`. Do not reread whole docs folders; follow the docs map in AGENTS.md when a task needs it.
3. **Baseline first.** If `check` is red or the tree has unexplained changes from a previous session, fixing that is
   the first task: find the cause (`/hk-diagnose`), fix or record it in PROGRESS.md → Known Issues.
4. **Pick one feature.**
   - A feature is already active → continue it (WIP=1).
   - Otherwise `python scripts/harness.py next` (refuses while VCR < 1 because something is blocked: resolve or
     document the blocker first).
5. Tell the user in 3–5 lines: state, the feature you will work on, its verification command, and the plan.
   Then continue with `/hk-implement`.

Never start a second feature while one is active, and never decide that something is done without running its
verification.
