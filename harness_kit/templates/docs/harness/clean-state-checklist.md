# Clean-state checklist (session exit)

Automated part: `python scripts/harness.py exit-check`. Parts to confirm yourself:

- [ ] Build passes, all tests pass (including tests that existed before this session).
- [ ] The standard boot path (`./init.sh`) still works without manual fixes.
- [ ] `feature_list.json` is accurate: what is passing, what is not yet verified.
- [ ] `PROGRESS.md` records: done / in progress / issues / next steps.
- [ ] No leftover debug code (`console.log`, temporary `print`, `debugger`, `breakpoint()`), no new TODO without a matching feature.
- [ ] No leftover temp files, scratch scripts, or stray logs.
- [ ] No half-finished step left unrecorded.
- [ ] New decisions are in `docs/DECISIONS.md`.
- [ ] The next session can continue from the repo alone, without rereading the chat.

Cleanup must be **idempotent** (running it repeatedly gives the same result), e.g. `rm -f`, restoring config files to a known state, then rerunning tests.

## Periodic cleanup (weekly)

- Scan for stale docs (`python scripts/harness.py docs`).
- Scan for structural violations (`python scripts/harness.py rules`).
- Update grades in `docs/QUALITY.md`.
- Open small, targeted cleanup changes (each one a feature).
- Rerun `check --full` after cleanup to detect drift.
