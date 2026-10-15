---
name: hk-docs
description: Keep the repository the system of record — run the fresh-session test, update AGENTS.md (a short router), docs/ and DECISIONS.md after changes, and fix what `scripts/harness.py docs --strict` reports. Use after architecture or behavior changes, when a new session had to rediscover something, when knowledge lives only in chat/Slack/heads, or when the user types /hk-docs.
---

# Repo as the system of record

Delegate to the **hk-docs-keeper** subagent for anything beyond a small edit.

1. `python scripts/harness.py docs --strict` — broken links, `HARNESS:FILL` placeholders, an entry file over its
   line limit, hard constraints without a `source:`.
2. Fresh-session test: from the repo alone, answer (1) what is this, (2) how is it organized, (3) how do I run it,
   (4) how do I verify it, (5) where are we now. Each gap → the doc that should answer it.
3. Put knowledge where the agent looks first and next to the code it describes:
   - rules everybody must follow → AGENTS.md hard constraints (≤ 15, each with source and expiry)
   - module details → `<module>/ARCHITECTURE.md` or docs/ARCHITECTURE.md
   - the *why* of a choice → docs/DECISIONS.md
   - a rule a machine can check → `.harness/rules.json` (pattern, why, fix)
4. Keep AGENTS.md a router (50–200 lines): if it grows, move detail into docs/ and leave a "read X when Y" row.
5. Re-run `docs --strict` until it passes, and report which of the five questions changed from unanswered to answered.
