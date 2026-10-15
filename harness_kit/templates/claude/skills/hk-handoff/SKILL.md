---
name: hk-handoff
description: Clock out — leave the repo so the next session (with no chat history) can continue within minutes. Updates PROGRESS.md, records decisions, runs the clean-state exit check and prepares the commit. Use at the end of a session, when context is getting full (≥ 70 %), before /clear, or when the user types /hk-handoff.
---

# Clock-out

1. Stop starting new work. If a feature is half done, leave it `active` and write exactly where it stands.
2. `python scripts/harness.py handoff` — refreshes the auto-generated state block in PROGRESS.md.
3. Write the hand-maintained sections of PROGRESS.md, short and specific (see `docs/harness/session-handoff.md`):
   - **Done** — feature ids that passed `verify` this session (and nothing else)
   - **In Progress** — the active feature, what is done, what remains, the next concrete command to run
   - **Known Issues** — failures, blockers, flaky things, with the evidence
   - **Next Steps** — numbered, actionable, first one startable without asking anybody
4. New decisions → `docs/DECISIONS.md` (decision, reason, rejected alternatives, expiry).
5. `python scripts/harness.py exit-check` must print `CLEAN STATE`. Fix what it reports: debug code, junk files,
   tracked secrets, a broken startup path, an un-updated PROGRESS.md.
6. Propose a commit of the verified work (feature id + why). Commit only with the user's approval.
7. Tell the user in 3 lines: what is done, what is next, anything they must decide.
