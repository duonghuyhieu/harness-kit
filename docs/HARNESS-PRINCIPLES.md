# Harness Engineering principles → harness-kit components

A summary of the 14 lectures of the [Learn Harness Engineering](https://walkinglabs.github.io/learn-harness-engineering/) course
and how each principle is implemented in the kit. The numeric thresholds (100 lines, 15 constraints, 10%...) are
teaching defaults and can be tuned — they are not empirical limits.

## Core model

**Harness = everything outside the model weights**: instructions, tools, environment, state, verification feedback.
A strong model ≠ reliable execution. When an agent fails, **check the harness before changing the model**.

| Subsystem | Question | Artifact in the kit |
|---|---|---|
| Instructions | Does the agent know what to do? | `AGENTS.md` (~100-line router), `CLAUDE.md` (`@AGENTS.md`), `docs/*` |
| Tools | Does the agent have the right tools, with just enough permissions? | `.harness/config.json` (canonical commands), `scripts/harness.py` |
| Environment | Is the environment reproducible? | `init.sh` / `init.ps1`, lockfile, pinned runtime versions |
| State | Can the next session pick up where this one left off? | `PROGRESS.md`, `feature_list.json`, `docs/DECISIONS.md`, git |
| Feedback | Does the agent know whether it got it right? | `harness.py check` (rules, docs, lint, typecheck, test, build, e2e, regression) |

## 14 lectures → mechanisms

| Lecture | Principle | Implementation in the kit |
|---|---|---|
| 01 Strong agents still fail | 5 failure layers: vague requirements, implicit conventions, missing environment, no verification, lost state. "Done" must be checkable. | `audit` scores the 5 subsystems; `fail --layer` logs each real failure to `.harness/failures.jsonl`, and `audit`/`failures` name the bottleneck from observed failures; "Definition of Done" in AGENTS.md |
| 02 What a harness is | 5 subsystems; feedback has the highest ROI; harnesses rot like code. | `audit`'s 5×5 scoring scale; `check` is the first thing that must work (H001); optional least-privilege Claude Code settings via `retrofit --claude` |
| 03 The repo is the source of truth | What the agent can't see doesn't exist. 5-question Fresh Session Test. Knowledge lives next to code. ACID for state. | `audit` runs the Fresh Session Test; `docs/` + module docs next to code; H004 forces knowledge into the repo |
| 04 Giant instruction files fail | Lost in the middle, eats context. The entry point is a 50–200-line router, ≤15 hard constraints (with source/condition/expiry), progressive disclosure. | `docs` checks AGENTS.md ≤ 200 lines, ≤ 15 hard constraints, and a `source:` on each; long legacy instructions → proposal + H005 merge |
| 05 Losing continuity between sessions | Record the "why", not just the "what". PROGRESS + DECISIONS + git checkpoints. Clock-in/clock-out. Context anxiety. | `start` (clock-in), `handoff` (automatic status block), DECISIONS.md, the "hand off when context is nearly full" rule |
| 06 Initialization needs its own phase | The first session only builds infrastructure: runnable, testable, visible progress, a clear next step. Checkpoint commit. | `retrofit` + feature group H001–H006; the skill forbids changing product code during this phase; `--commit` |
| 07 Overreaching scope, unfinished work | WIP=1, proof of completion, VCR = verified / activated; block new work when VCR < 1. | `next` refuses when a feature is already active or VCR < 1; `--scope` globs per feature, `verify`/`exit-check` flag drift (`scope_drift: warn|error`) |
| 08 The feature list is a primitive | Behavior triple + verification command + status. A state machine controlled by the harness; passing is one-way. | `feature_list.json`; only `verify` moves a feature to passing and records evidence; `validate` catches passing without evidence |
| 09 Premature success claims | Systematic overconfidence. Three layers of verification. Separate maker and checker. Errors come with fixes. | `verify` runs the feature command + the project check (`--verify-check full` adds build/e2e/regression); independent review before `verify` (playbook; `harness-checker` subagent with `--claude`); ERROR / WHY / FIX messages |
| 10 E2E changes outcomes | Unit tests miss boundary bugs. Architecture rules become machine checks. Turn recurring review comments into rules. | `e2e` step in `check --full`; `.harness/rules.json` (pattern + why + fix) |
| 11 Observability belongs in the harness | Two layers: runtime (logs/traces) and process (sprint contract, rubric). | `.harness/trace.jsonl`, `last-check.json`; `docs/RELIABILITY.md`; sprint-contract and evaluator-rubric templates; per loop run, `.harness/runs/<id>/` (every step incl. passed gates, session transcripts, hook decisions) and `report` with a theory checklist computed from that log |
| 12 Sessions must leave a clean state | Clean state is part of "done": build/tests pass, progress recorded, no junk, startable. Idempotent cleanup; QUALITY doc; periodic harness simplification. | `exit-check` (debug code, junk files, tracked secrets, stale PROGRESS, smoke, scope drift); optional Claude Code Stop hook (`--claude`) blocks finishing on cheap violations; `docs/QUALITY.md` with a simplification log |
| 13 Loop engineering | Loop = goal + verification + stop condition; separate maker/checker; state on disk. | `harness.py loop`: the system drives maker → independent reviewer (fresh session, JSON verdict) → test gate → pass-gate, with machine stop conditions (attempts, per-session and per-run budget) and state on disk (feature list, trace, evidence); templates `goal-template.md`, `loop-state-template.md` |
| 14 Graph engineering | Nodes / edges / shared state / routing; use a graph only when ≥ 3/5 criteria are met. | `loop` is a small explicit graph: maker → reviewer → gate → verify, back-edges to the maker with concrete feedback, block after N attempts, stop on tools/budget, human approval node before commit (`--commit` to pre-approve) |

## Standard file set after a retrofit

```text
AGENTS.md                 router: hard constraints, clock-in, canonical commands, DoD, docs map, clock-out
CLAUDE.md                 @AGENTS.md + Claude Code-specific notes
PROGRESS.md               automatic status + done / in progress / issues / next steps
feature_list.json         {features: [{id, category, behavior, verification, status, depends_on, evidence}]}
init.sh, init.ps1         setup → check → print the app run command
.gitattributes            pins LF for init.sh / scripts/harness.py and CRLF for init.ps1 (proposal only if an existing file does not already pin them)
scripts/harness.py        runtime (standalone, needs only Python ≥ 3.9)
.harness/config.json      canonical commands per stack (single source of truth)
.harness/rules.json       machine-checked architecture rules
docs/ARCHITECTURE.md      module map, dependency direction, hotspots
docs/PRODUCT.md           users, golden journeys, out of scope
docs/DECISIONS.md         lightweight ADRs
docs/QUALITY.md           A–D grades per module, hotspots, history
docs/RELIABILITY.md       golden paths, runtime signals
docs/harness/             clean-state checklist, handoff, rubric, sprint contract, refactor playbook
(full) docs/SECURITY.md, docs/exec-plans/, goal/loop templates
(--claude) .claude/settings.json (permissions + Stop hook), .claude/agents/harness-checker.md, .claude/skills/harness-optimize/
```

## Why "code optimization" must come after the harness

Refactoring legacy code without verification is guesswork. The right order:

1. A working verification baseline (H001–H003).
2. Knowledge is in the repo (H004–H005).
3. A measurable backlog (H006): each optimization target is a feature with a quantitative verification command.
4. The optimize loop, one feature at a time: characterization tests → small incremental changes → independent checker → `verify` → update QUALITY.

## Further reading (the course's primary sources)
- OpenAI — Harness engineering: leveraging Codex in an agent-first world
- Anthropic — Effective harnesses for long-running agents; Harness design for long-running application development
- HumanLayer — Skill issue: harness engineering for coding agents
