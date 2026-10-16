# What harness-kit can do

harness-kit turns a legacy repository into one that coding agents can work on reliably, then drives code
optimization through a verified, one-feature-at-a-time loop. It implements the structure taught in
[Learn Harness Engineering](https://walkinglabs.github.io/learn-harness-engineering/) (14 lectures; mapping in
`docs/HARNESS-PRINCIPLES.md`). Python ≥ 3.10, standard library only; the in-project runtime needs Python ≥ 3.9.

## 1. Assess a project — `harness audit <path>` (read-only)

- Scores the **five subsystems** (Instructions, Tools, Environment, State, Feedback), 5 checks each → score /100.
- Names the **bottleneck**: from real failures logged with `fail --layer` when they exist, otherwise the lowest score.
- Runs the **Fresh Session Test**: can a new agent session tell what the system is, how it is organized, how to run
  it, how to verify it, and where work stands — from the repo alone?
- Finds **hotspots** (lines × (1 + git commits in 12 months)) and the largest files: the optimization candidates.
- Scans for **dirty state**: temp files, logs in source, build output in source, tracked `.env`, TODO/FIXME counts.
- Prints a prioritized fix list; `--json` for tooling, `--out` for a file, `--min-score N` to gate CI.
- Honors commands the project declared in `.harness/config.json` over auto-detected ones.

## 2. Retrofit the harness — `harness retrofit <path> [--level minimal|standard|full] [--dry-run]`

- **Never overwrites a user file.** A collision becomes a proposal in `.harness/proposals/` plus a note. The only
  exception is opt-in: with `--claude`, an existing `.claude/` is extended (settings merged, same-named harness items replaced).
- **Detects the stack and real commands** (setup, dev, test, lint, type-check, build, e2e) for Node (npm/pnpm/yarn/bun,
  TypeScript, Playwright), Python (pip/uv/poetry/pipenv, Django/FastAPI, pytest/ruff/flake8/mypy/pyright),
  Java (Maven/Gradle, wrappers, Kotlin, Spring Boot), Go, Rust (workspaces with `--workspace`), .NET (C#/F#/VB,
  .sln/.slnx), PHP (Composer, Laravel, PHPUnit, PHPStan), Ruby (Bundler, Rails, RSpec, RuboCop).
- **Monorepos:** scans `packages/*`, `apps/*`, `services/*`; folds modules into their build root only when the root
  declares them (pom `<module>`, gradle `include`, solution project paths, cargo workspace members/excludes).
- All generated commands run under Windows cmd.exe as well as POSIX shells.

| Level | Generated files |
|---|---|
| minimal | `AGENTS.md` (≈70-line router), `CLAUDE.md` (`@AGENTS.md`), `PROGRESS.md`, `init.sh`, `init.ps1`, `feature_list.json` (seeded H001–H006), `.harness/config.json`, `scripts/harness.py`, `.gitattributes` |
| standard (default) | + `docs/ARCHITECTURE.md`, `PRODUCT.md`, `DECISIONS.md`, `QUALITY.md` (module grades + hotspots), `RELIABILITY.md`; `docs/harness/` (clean-state checklist, handoff, evaluator rubric, sprint contract, legacy-refactor playbook); `.harness/rules.json` |
| full | + docs/SECURITY.md, `docs/exec-plans/` (active, completed, tech-debt tracker), goal and loop-state templates |
| `--claude` (any level, off by default) | + Claude Code guardrails: .claude/settings.json, the harness-checker subagent, the harness-optimize skill (section 4) |

Seeded features make the retrofit itself verifiable: H001 baseline `check` passes · H002 a real test command ·
H003 a smoke check · H004 no unfilled placeholders or broken links · H005 legacy instructions merged ·
H006 at least 3 measurable `refactor` features.

## 3. Operate every session — `python scripts/harness.py <command>`

| Area | Commands | What it enforces |
|---|---|---|
| Session lifecycle | `start`, `handoff`, `exit-check` | clock-in shows next steps, recent commits, feature status and runs the baseline check; `handoff` writes a state block into PROGRESS.md; `exit-check` refuses to call a session done unless checks pass, progress was updated, and there is no debug code, junk file, tracked secret or invalid feature list |
| Feature state machine | `next`, `verify`, `block`, `unblock`, `add`, `status`, `validate` | WIP=1; no new work while VCR < 1; only `verify` can set `passing` (one-way) and it records evidence (commit, command, output, a hash of the scope files); hand-edited `passing` without evidence is flagged; a verification command that cannot fail (`echo`, `true`, `exit 0`, `python -c pass`, `\|\| true`, a pipe into `tail`/`tee`) is refused (`trivial_verification`); passing features whose scope files changed since they passed are listed as **stale** until `check --full` re-runs them |
| Verification | `check [--full] [--only …]` | rules → docs → lint → type-check → test (+ build, e2e, regression of every passing feature); errors are written as ERROR / WHY / FIX; missing tools are reported as environment problems |
| Scope | `add --scope <globs>`, `scope_drift: warn|error` | changes outside the active feature's scope are flagged by `verify` and `exit-check`, or block them |
| Architecture rules | `rules`, `.harness/rules.json` | regex rules with include/exclude globs, WHY and FIX; `error` fails `check` |
| Docs health | `docs [--strict]` | entry file ≤ 200 lines, ≤ 15 hard constraints each with a `source:`, no broken links, no unfilled placeholders, stale module docs |
| Metrics | `size <file>[::func] --max-lines N` | measurable refactor goals (file or Python function length) |
| Diagnostics | `fail --layer … --note …`, `failures`, `trace` | attribute each agent failure to a harness layer; activity log in `.harness/trace.jsonl`; last check in `.harness/last-check.json` |
| Environment | `setup [--print-dev | --run-dev]`, `init.sh`, `init.ps1` | one source of truth for commands; safe Python discovery (skips the Windows Store stub) |

## 4. Optional Claude Code guardrails — `retrofit --claude` (off by default since 2026-10-09)

The default harness is agent-agnostic. Only when asked, the retrofit also adds:
- **Least-privilege permissions** in .claude/settings.json: verification commands allowed; `rm -rf`, `git reset --hard`,
  `git push` ask first; force-push and reading `.env` denied.
- **Stop hook** (`harness.py hook-stop`): blocks the agent from finishing while cheap, deterministic violations remain;
  never loops (`stop_hook_active`), disabled with `HARNESS_STOP_HOOK=off`. The `hook-stop` command stays in the
  runtime either way; without the settings file nothing calls it.
- **harness-checker subagent**: an independent, read-only reviewer for the review-before-`verify` step.
- **/harness-optimize skill**: the optimization loop as a slash command.

Into a project that already has `.claude/`, this is additive: user agents, skills, commands, `settings.local.json` and
all settings keys/rules are kept; harness permission rules are appended and its Stop hook added (an older harness Stop
hook is replaced); only agents/harness-checker.md and `skills/harness-optimize/` are replaced by name. Re-running is
idempotent. An unreadable `settings.json` is left untouched (proposal + note).

Separately, `harness install-skill` installs the `/harness-retrofit` skill into `~/.claude/skills` when you want it.

## 5. Autonomous loop — `python scripts/harness.py loop` (lectures 09, 13, 14)

The system, not the agent, runs each feature: maker session (fresh, scope-limited) → reviewer session (fresh, read-only,
JSON verdict) → test gate (no fewer tests run; every new test file adds tests the runner runs) → `verify`. It uses the installed Claude Code CLI (`claude -p`, the user's login, no
API key, no Python dependency); nothing is written to `.claude/`.

Guardrails come in layers:
- `--permission-mode dontAsk` with a tool allowlist.
- A best-effort `PreToolUse` hook passed with `--settings`.
- The real gate after every session:
  - harness-owned files (the feature list, all of `.harness/` and `.claude/`, `scripts/harness.py`, `AGENTS.md`,
    `CLAUDE.md`, `CLAUDE.local.md`, `.mcp.json`, the rubric) are restored, and the attempt is rejected
  - the maker's out-of-scope changes are reverted, and the attempt is rejected
- The loop refuses to start on a tree with out-of-scope changes.

Stop conditions are machine-checked:
- attempts per feature (then block + failure log)
- budget per session and per run; a session never gets more than what is left of the run, and unknown cost is charged
  at its cap
- a stop as a `tools` problem when the reviewer cannot run its checks

Cost, turns and the reviewer's evidence are stored in the feature's evidence. Nothing is committed without `--commit`;
without it the loop stops after one feature for approval. Tested offline with a fake agent and on a demo project with real
`claude -p` runs.

Observability (lecture 11): each run leaves `.harness/runs/<id>/` (events of every step including passed gates, session
transcripts, hook decisions). `harness.py report` turns it into a theory checklist computed from those facts, where each
principle is held, enforced or violated, plus a timeline. The first real logged run showed, for example, 18 hook
decisions for 18 guarded tool calls, and a reviewer that lost a session to `cd <root> &&` prefixes; that led to a prompt
fix and an allowlist fix.

## 5b. Optimize legacy code safely

The loop (`docs/harness/legacy-refactor-playbook.md`): pick the lowest-graded module or top
hotspot from `docs/QUALITY.md` → a `refactor` feature with a measurable `verify` command and `--scope` →
characterization tests committed first and passing on the old code → small steps → independent review (a fresh
agent session or a human) → `verify` → update the grade.

## 6. Proven on this repository

The kit runs under its own harness:

- **Audit score:** 48 → 84/100, then 80/100 after the 2026-10-09 decision to remove this repo's own `.claude/`
  guardrails (the "agent permissions scoped" check now fails here, by choice).
- **Feature list:** all 14 features passing with evidence (5 harness setup, 5 bugfix, 1 test, 3 refactor).
- **Verification:** `check --full`, including regression of every feature, passes; 36 unit tests.
- **Refactors:** the three long functions shrank 85 → 9, 50/54 → 20/16 and 52 → 20 lines. Each refactor first committed
  a characterization golden, which then matched byte-for-byte.
- **Independent checker:**
  - Caught a process bug: `verify` ran before review, now fixed everywhere.
  - Caught real defects: CRLF breakage in `init.sh`, `.gitattributes` negations, a bare Gemfile producing a failing
    test command, `.kts` mislabeled as Kotlin, cmd.exe-unsafe commands, nondeterministic .NET language detection,
    false multi-module merges, and cargo members dropped from the gate.
- **Failure log** (`.harness/failures.jsonl`): 5 failures, attributed to the tools and instructions layers. Each one
  became a rule in `AGENTS.md` or a fix.

## 7. Limits (known, by design or not yet done)

- Detectors are fixture-tested; they have not yet run against real Java/Go/Rust/.NET/PHP/Ruby repositories.
- The skills (`/harness-retrofit`, `/harness-optimize`) are exercised manually, not by automated tests.
- No app-level structured logging/OpenTelemetry is injected (stack-specific; the skill guides it).
- `loop` is sequential (WIP=1) and needs the Claude Code CLI; no parallel worktrees and no scheduler (`/loop`, cron, CI)
  are generated — run it yourself or from your own scheduler. Reviewer quality depends on the model: the first real
  runs with haiku passed tests the runner never executed, which is why the mechanical test gate exists.
- `loop` skips the harness setup features (category `harness`, H00x) when it picks the next feature: they change
  `.harness/config.json` and the docs, which the loop protects; run them through `/hk-init`.
- `loop` cannot complete a feature that must change harness-owned files (in this repo: runtime features, since
  `scripts/harness.py` is always restored and `harness.py upgrade` is denied). Do those by hand.
- The `PreToolUse` hook is best effort: code the maker writes (its own tests) can still touch files. The post-session
  restore and scope revert are what make that harmless, and they cover only the guarded files and the scope.
  **The loop is not a sandbox**: a session can still write outside the repository (e.g. `~/.claude/`). Run it in a
  container or VM when that matters.
- The test gate runs the project's test suite once, plus once per new test file, after each passing review. That is
  slow for large suites.
- Gradle `project(':x').projectDir`, Composer < 2.2 on Windows, and a few `.gitattributes` edge cases are listed in
  `PROGRESS.md` → Known Issues.
