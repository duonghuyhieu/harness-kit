---
name: hk-init
description: Initialization phase after `harness install` — make the project runnable, testable and resumable (baseline features H001–H006), bring tacit knowledge into the repo, and build a measurable backlog, without changing product code. Use right after installing the harness, on a legacy project, or when the user says "set up the harness", "retrofit", "make this project agent-friendly", or types /hk-init.
---

# Initialization phase

The installer already generated the harness files. The part that needs judgment is yours: real commands, knowledge
that lives only in people's heads, and a baseline that proves the project works. Kit tools (optional):
the kit CLI (`npx github:duonghuyhieu/harness-kit <command>`, or `python ~/.harness-kit/harness.py <command>` after the one-line installer) — skip the kit steps if neither is available.

## Rules
- This phase does not change product behavior. Only harness files, command config, docs and tests.
- Every H00x feature goes through `next` → work → independent review (**hk-reviewer**) → `verify`.
- Never invent knowledge. Collect what you cannot verify from code, git or docs into one list of questions.
- For a large project, delegate the work to the **hk-initializer** subagent module by module, or use Explore subagents
  to survey modules in parallel (responsibility, entry points, dependencies, conventions, risks — with file paths).

## Steps
0. **Safety and measure.** `git status` — a dirty tree → ask the user first; suggest a branch `harness/setup`.
   Kit available → `<kit CLI> audit . --out .harness-audit-before.md` and report the score and
   the bottleneck subsystem.
1. **H001 baseline.** `python scripts/harness.py next`. Make `.harness/config.json → commands` match reality (CI
   config, README, package scripts, Makefile); fix the config, not the code. `python scripts/harness.py setup`, then
   `check`. Red because of legacy failures → do not delete tests or disable rules: record them in PROGRESS.md → Known
   Issues, `add --category bugfix` per error group, put them in H001's `depends_on`, `block H001 --reason ...`.
2. **H004/H005 knowledge.** Sources: README, CONTRIBUTING, CI, lint config, `.env.example`, NOTE/HACK comments,
   `git log` (reverts, big commits), legacy agent instructions. Fill every `HARNESS:FILL`: AGENTS.md (≤ 15 hard
   constraints, each with a source and expiry), docs/ARCHITECTURE.md, PRODUCT.md, RELIABILITY.md, DECISIONS.md.
   Legacy instructions → classify each rule as hard constraint / topic doc / obsolete (ask before deleting).
   Machine-checkable rules → `.harness/rules.json`. `python scripts/harness.py docs --strict` must pass.
3. **H002/H003 tests and smoke.** No tests → the stack's standard runner plus one characterization test for the top
   hotspot. Declare `commands.test` and `commands.smoke`.
4. **H006 backlog.** Grade modules A–D in docs/QUALITY.md with evidence; ≥ 3 `refactor` features with measurable
   verification (characterization tests + `python scripts/harness.py size <file> --max-lines N`, coverage, benchmark),
   `--scope` and `--out-of-scope`.
5. **Checkpoint.** `/hk-handoff` (handoff, PROGRESS sections, exit-check). Kit available → audit again and compare.
   Propose the commit `chore(harness): initialize harness`.

## Report
Before/after score, which H features pass or are blocked and why, the open questions, the top 3 backlog items.
When the context is ~70 % full: stop, hand off, and continue next session with `/hk-start`.
