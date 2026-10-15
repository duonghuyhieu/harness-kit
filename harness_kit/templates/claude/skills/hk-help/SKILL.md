---
name: hk-help
description: Explain the harness-kit workflow and the /hk-* commands available in this project, and say what to do next given the current harness state. Use when the user asks "how does the harness work", "what commands are there", "where do I start", or types /hk-help.
argument-hint: "[topic]"
---

# harness-kit help

1. Run `python scripts/harness.py status` and show the user the state in 3–5 lines.
2. Recommend the next command from the table, based on that state:

| Situation | Command |
|---|---|
| Just installed, or H001–H006 not passing | `/hk-init` (initialization phase) |
| Start of a session | `/hk-start` (clock-in) |
| A new request or a vague task | `/hk-plan` (features with verification commands) |
| A feature to build | `/hk-implement` (one feature: next → maker → review → verify) |
| A diff to check | `/hk-review` (independent reviewer) |
| Work looks done | `/hk-verify <id>` (pass-gate) |
| End of a session | `/hk-handoff` (handoff + exit-check) |
| Something failed, or an agent keeps failing | `/hk-diagnose` |
| How good is this harness? | `/hk-audit` |
| Autonomous runs / metric goals | `/hk-loop` |
| Many roles, branching work | `/hk-graph` |
| Legacy refactoring | `/hk-optimize` |
| Logs, traces, golden paths | `/hk-observe` |
| Junk, drift, harness bloat | `/hk-clean` |
| Docs out of date | `/hk-docs` |

3. If the user asked about a topic, answer it from these sources (read only what is needed):
   `AGENTS.md` (rules and commands), `docs/harness/` (contracts, rubric, handoff, playbooks),
   `python scripts/harness.py --help` (every runtime command).

Core rules to state when relevant: one active feature at a time (WIP=1); a feature becomes `passing` only through
`python scripts/harness.py verify <id>` (one-way, with evidence); the maker never grades its own work; every session
ends in a clean state.
