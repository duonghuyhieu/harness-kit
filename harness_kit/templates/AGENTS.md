# AGENTS.md — {{project_name}}

<!-- HARNESS:FILL: 1–2 sentences: what this project does and for whom. Example: "Appointment-booking API for clinics, serving the mobile app." -->

Stack: {{stack_summary}}

> This file is a **map**, not an encyclopedia. Keep it under ~120 lines.
> Details live in `docs/` — read them only when the task needs them.

## Hard constraints (non-negotiable)

- Never mark work done without runnable evidence: feature status changes only via `{{h}} verify <id>` (source: harness-kit pass-gate).
- Never delete, skip, or loosen tests/lint/type-checks to get a pass (source: harness-kit).
- Never commit secrets (`.env`, tokens, passwords); sample config lives in `.env.example` (source: harness-kit).
- Do not change dependencies/runtime versions unless the feature requires it; if you must, record it in `docs/DECISIONS.md` (source: harness-kit).
<!-- HARNESS:FILL: add up to ~10 project-specific constraints, each with (source: ..., expires when: ...). Example: "- All DB queries go through the repository layer, no SQL in controllers (source: incident 2024-03; expires when: ORM is dropped)" -->

## Session start (clock-in)

1. `{{h}} start` — prints the next step, recent commits, feature status, and runs the baseline check.
2. Read `PROGRESS.md` (state + next steps) and `docs/DECISIONS.md` (why the code is the way it is).
3. If the baseline check **fails**: fix the baseline first; do not pile new work on a broken state.
4. `{{h}} next` — activate exactly **one** feature (WIP=1) from `feature_list.json`.

## Standard commands

| Purpose | Command |
|---|---|
| Set up environment | `./init.sh` (Windows: `.\init.ps1`) |
{{command_rows}}
| **Quick verification** | `{{h}} check` |
| **Full verification** | `{{h}} check --full` (adds build, e2e, regression of passing features) |

The real commands are declared in `.harness/config.json` — edit them there; do not copy commands into multiple places.

## Working rules

- **One feature at a time.** Open a new feature only after the current one passes `verify`.
- **Stay in scope.** No "refactoring while I'm here". Out-of-scope issue → `{{h}} add` it as a new feature. Declare `--scope` globs; `verify` flags changes outside them.
- **Refactoring legacy code:** write tests that pin current behavior (characterization tests) *first*, then change it.{{playbook_ref}}
- **A review issue repeated twice** → turn it into a machine-checkable rule in `.harness/rules.json` (with WHY + FIX).
- **Something went wrong?** Log the cause: `{{h}} fail --layer <instructions|tools|environment|state|feedback> --note "..."`, then fix that harness layer, not the model.
- If you don't know, say so; unclear architectural decision → ask a human, then record it in `docs/DECISIONS.md`.

## Definition of done

A feature is **done** only when all four hold:
1. Tier 1 — static: lint + type-check pass.
2. Tier 2 — runtime: tests pass, the app starts.
3. Tier 3 — system: the feature's `verification` command (end-to-end flow) passes via `{{h}} verify <id>` (source: harness-kit pass-gate).
4. The repo still boots from `./init.sh`, and progress is recorded in `PROGRESS.md`.

Do not move to the next tier while the previous one fails.

## Docs map (read when needed)

| Document | Read when |
|---|---|
{{docs_map_rows}}
## Session end (clock-out)

1. `{{h}} handoff`, then write by hand in `PROGRESS.md`: Done / In Progress / Known Issues / Next Steps.
2. Record new decisions (if any) in `docs/DECISIONS.md`.
3. `{{h}} exit-check` — must print **CLEAN STATE** (checks pass, no debug code, no stray files, progress recorded).
4. Commit verified work; the message names the feature id and *why*.
5. Running out of context? Stop starting new work and do the handoff first — don't rush to declare done.
