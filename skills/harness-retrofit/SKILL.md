---
name: harness-retrofit
description: Retrofit a legacy project with a harness engineering structure (AGENTS.md router, feature_list.json with a pass-gate, PROGRESS.md, init.sh, docs/ system of record, architecture rules, clean state), then build a code-optimization backlog. Use when the user says "standardize this project", "apply harness", "retrofit harness", "make this legacy project agent-friendly", "set up AGENTS.md", or types /harness-retrofit.
---

# Harness retrofit

The mechanical tooling (stack detection, file generation, scoring) is harness-kit:

```bash
KIT="python {{KIT_ROOT}}/harness.py"   # or `harness` if installed with pip install -e
```

The part **you** do is the part that needs judgment: bring tacit knowledge into the repo, establish a verification baseline,
and build the optimization backlog. Underlying principles: `{{KIT_ROOT}}/docs/HARNESS-PRINCIPLES.md` (read when you need the rationale).

## Rules of this skill
- The bootstrap phase **does not modify product code**. Only add harness files, command config, and tests.
- Never overwrite user files. Name collision → the kit writes a proposal to `.harness/proposals/`; merging requires the user's consent.
- Every H00x feature goes through the exact `next` → work → `verify` loop (eat your own dog food: use the pass-gate itself).
  Features that change code or tests also get an independent review (a fresh subagent with only the diff and
  `docs/harness/evaluator-rubric.md`) *before* `verify` — `passing` is one-way.
- Don't invent knowledge. Anything you can't infer from code/git/docs → add it to the questions for a human; don't guess.

## Phase 0 — Safety and measure first
1. `git status`. Dirty working tree → ask the user first. Suggest a dedicated branch `harness/retrofit`.
2. `$KIT audit . --out .harness-audit-before.md` (written outside `.harness/` to keep it separate) — report the score and bottlenecks to the user.

## Phase 1 — Scaffold
1. Choose a level: `minimal` (< 5k lines, 1 person), `standard` (default), `full` (> 50k lines, multiple stacks/teams).
2. `$KIT retrofit . --level <level> --dry-run` → summarize the plan for the user → `$KIT retrofit . --level <level>`
   (add `--git-init` if there is no git yet — ask first). Claude Code guardrails (`.claude/settings.json` with a Stop
   hook, `harness-checker` subagent, `/harness-optimize` skill) are **off by default**; add `--claude` only if the user asks.
3. Read the "Notes" part of the output: legacy instructions, missing tests, missing git...
4. If `.gitattributes` already existed but does not pin the harness scripts' line endings, merge
   `.harness/proposals/gitattributes.proposed` into it (with consent), then run `git add --renormalize .`.
5. With `--claude` only, into an existing `.claude/`: the user's entries are kept; the harness permissions and Stop
   hook are merged into `settings.json` (an older harness Stop hook is replaced), and same-named `agents/harness-checker.md`
   / `skills/harness-optimize/` are replaced. Report the "Merged" and "Replaced" lines of the output to the user. If the
   settings could not be merged (invalid JSON), it is untouched and a proposal is in `.harness/proposals/.claude/`.

## Phase 2 — Verification baseline (H001)
1. `python scripts/harness.py next` (activates H001).
2. Check that `.harness/config.json` → `commands` matches reality (read CI config, README, package scripts, Makefile).
   Fix the **config**, not the code. Add `commands.smoke` if known (e.g. `python -c "import app"`, `npm run build`).
3. `python scripts/harness.py setup`, then `python scripts/harness.py check`.
4. Baseline is red because legacy code has broken tests/dirty lint: do **not** delete/skip tests, do **not** disable rules.
   - Record the exact errors in `PROGRESS.md` → "Known Issues".
   - Create a bugfix feature per error group (`add --category bugfix`), add the ids to H001's `depends_on` (editing the
     feature list structure is allowed; only `status`/`evidence` must not be edited by hand), then `block H001 --reason "waiting on B00x"`.
   - Too many historical lint errors: ask the user whether to limit lint to changed files (record in DECISIONS) or clean up gradually.
5. Pass → `python scripts/harness.py verify H001`.

## Phase 3 — Bring knowledge into the repo (H004, H005)
1. Survey in parallel: for large projects, give each module/module group to an Explore subagent and ask it to return:
   responsibility, entry points, inbound/outbound dependencies, unusual conventions, risks — as a short table with file paths.
2. Sources of tacit knowledge to scan: README, CONTRIBUTING, old docs, wiki exports, CI config, lint/format config, `.env.example`,
   "NOTE/WARNING/HACK" comments, `git log` (large commits, reverts, recurring themes), legacy agent instructions.
3. Fill every `HARNESS:FILL`:
   - `AGENTS.md`: 1–2 sentence overview, ≤ 15 hard constraints **with source + expiry condition**, keep it ≤ 120 lines.
   - `docs/ARCHITECTURE.md`: module table, real dependency direction, hotspots.
   - `docs/PRODUCT.md`, `docs/RELIABILITY.md`, `docs/DECISIONS.md` (copy decisions that live outside the repo), `docs/SECURITY.md` if present.
   - Complex modules: add a short `<module>/ARCHITECTURE.md` or `CONSTRAINTS.md` next to the code.
4. H005 (if there are legacy instructions): classify each old rule → hard constraint (AGENTS.md) / topic doc (`docs/<topic>.md`) /
   obsolete (list for the user to approve deletion). Replace the old `CLAUDE.md` with the one in `.harness/proposals/` **only with the user's consent**.
   Rules a machine can check → put them in `.harness/rules.json` (pattern + why + fix).
5. Questions you can't answer yourself → collect them into one list and ask the user once.
6. `python scripts/harness.py docs --strict` passes → `verify H004` (and `H005`).

## Phase 4 — Tests and smoke (H002, H003)
- No tests yet: set up the stack's standard test runner + **one characterization test** for hotspot #1 (pin current behavior).
- Declare `commands.test` / `commands.smoke` in config. `verify H002`, `verify H003`.

## Phase 5 — Optimization backlog (H006)
1. Grade each module A–D in `docs/QUALITY.md` with evidence (tests present or not, file length, rule violations, how hard it is to understand).
2. Create ≥ 3 `refactor` features for hotspots / low-graded modules, each sized for one session, with a **measurable** verify command
   (characterization test + `python scripts/harness.py size <file> --max-lines N`, coverage threshold, benchmark...),
   an `--out-of-scope`, and `--scope` globs (files it may touch; `verify` flags drift). Cross-component work → `--verify-check full`. Turn important TODO/FIXMEs into features. Recurring bad patterns → `rules.json` rules (severity warn first).
3. `verify H006`.

## Phase 6 — Checkpoint and report
1. `python scripts/harness.py handoff`, then write the PROGRESS.md sections by hand.
2. `python scripts/harness.py exit-check` must report CLEAN STATE.
3. `$KIT audit .` — compare with `.harness-audit-before.md`, then delete the before file (or move it into the `docs/QUALITY.md` history).
4. Propose a commit (ask the user): `chore(harness): retrofit harness engineering`.
5. Report: before/after score, which H features are passing/blocked and why, open questions, top 3 optimization backlog items.
   Next step for the user: run the per-feature optimization loop in `docs/harness/legacy-refactor-playbook.md`
   (maker → independent review → `verify`); with `--claude` it is also available as `/harness-optimize`.

## When something goes wrong
Any time the agent (you included) fails during the retrofit, log it before fixing:
`python scripts/harness.py fail --layer <instructions|tools|environment|state|feedback> --note "..."`.
`$KIT audit .` uses these logs to name the real bottleneck.

## Multi-session projects
Retrofitting a large project rarely finishes in one session. When ~40% of context remains: stop starting new work, run `handoff`, record
a concrete next step, then `exit-check`. The next session starts with `python scripts/harness.py start`.
