# Decision log

> Record **what, why, when**. Newest on top. Do not change an established approach
> without reading the related entry here.
>
> Template:
> ## YYYY-MM-DD: <decision>
> - Reason:
> - Rejected alternatives: (and why)
> - Constraints / consequences:
> - Expires when:

## 2026-10-15: The kit lives in its own repository (harness-kit), separate from the research
- Reason: user decision — "only the application, like claudekit-main with .claude, not the research". The research
  (knowledge base, paper, reports) stays in `D:/harness-v2`.
- Consequences: this repo holds only what is installed or needed to build and test it.

## 2026-10-15: v2 — the Claude Code layer is the default of `install`, tracked by a manifest
- Reason: user request for a claudekit-like system that installs into `.claude/`. The analysis of ClaudeX
  (all-or-nothing settings copy, no manifest/uninstall, relative hook paths, an `allow` decision used to inject hints,
  hooks writing to fields Claude Code ignores) set the requirements.
- How: `harness install` = retrofit + `.claude/` payload (`harness_kit/templates/claude/`: 6 agents, 16 `hk-*` skills,
  a hook dispatcher, an output style) + semantic settings merge + `<project>/.claude/harness-kit.json` (sha256 per file,
  line endings normalized). `update` replaces unchanged files only; `uninstall` removes unchanged files and the
  harness settings entries (matched by command, plus the permission rules recorded as added).
- Hooks: one dispatcher (`<project>/.claude/hooks/harness_hook.py`) loads `scripts/harness.py` in-process and runs
  `hook-<event>`; fail-open; only Stop may exit 2; PreToolUse answers only deny/ask, never allow. Shell-form commands
  with `${CLAUDE_PROJECT_DIR}` (verified on Claude Code 2.1.287); the status line uses a relative path because it has
  no project-dir variable.
- Verified end to end with `claude -p` (2026-10-15): SessionStart context reached the model; an Edit of a feature's
  `status` was denied under `--permission-mode acceptEdits`.
- Rejected: copying `settings.json` whole (claudekit); one script per hook (process cost, duplicated config);
  publishing as a Claude Code plugin only (the user asked for files under `.claude/`; a plugin can come later).
- `retrofit` stays agent-agnostic (no `.claude/` unless `--claude`), so Codex/Cursor users get the core harness.
- Expires when: Claude Code changes the hook contract (re-check `doctor` and the dispatcher tests).

## 2026-10-10: Loop runs are logged per run, and the theory checklist is computed from the log
- Reason: user question — "how do I know it runs according to the theory?" The trace alone recorded only failures and no
  agent actions.
- How: `stream-json --verbose` transcripts (every tool call, session id, permission mode) in `.harness/runs/<id>/`.
  `events.jsonl` records every step, including passed gates; the hook appends its decisions via `HARNESS_RUN`.
  `report` derives held/enforced/violated per lesson from those facts.
- Rejected: asking the reviewer to self-report compliance (agents overclaim, lesson 09).
- Constraints: run logs are local (git-ignored, they hold code and paths), pruned to `loop_keep_runs` (run-named,
  non-link directories only); excluded from the integrity snapshot because the harness writes them during sessions.
- Learned in the independent review (Fail, then fixed):
  - The log on disk can be rewritten by code the maker wrote, so the report is built from the harness's in-memory
    events, and a changed file is flagged.
  - Refused tool calls (CLI `permission_denials`, or hook errors) are not counted as run. A real run had shown a
    reviewer "running verify" when that call had been refused.
  - A run with no end record is never "held".
  - The feedback row derives rejections from the log and compares full prompts.
  - Re-review: a finished `report.md` could be rewritten by project code run later. The report's sha256 is now anchored
    on the feature in the git-tracked `feature_list.json` (`loop_runs`, last 10), and `report` verifies it. With
    `--commit`, the commit happens after anchoring, so it includes the anchor.
- Allowlist additions from the first logged real run: `harness.py start` (AGENTS.md tells agents to clock in) and
  `cd *` (agents prefix `cd <root> &&`; every other part of a compound command must still be allowed).

## 2026-10-09: The loop drives the `claude` CLI, not the Agent SDK
- Reason: user decision ("no key needed, run the claude CLI"). `claude -p` uses the existing login, and a subprocess keeps
  the runtime stdlib-only (the SDK would be a Python dependency). Verified on this machine (CLI 2.1.280): JSON output,
  `--json-schema`, `--max-budget-usd`, and a `PreToolUse` hook passed via `--settings` that blocks out-of-scope writes.
- Consequences: one `claude -p` session per role and attempt (fresh context = maker/checker separation); guardrails via
  `--settings`, never `.claude/`; no auto-commit by default (human approval node).
- Learned in real runs: prompt rules alone did not stop a reviewer from passing tests the runner never executes →
  mechanical test-collection gate; a reviewer denied a command must not count against the maker → retry once, then stop
  as a `tools` failure.
- Learned in the independent review (Fail, then fixed):
  - A hook is best effort. `acceptEdits` auto-approves `rm`/`mv`/`sed` and wrappers slip past regexes. So sessions use
    `dontAsk`, and the real gate is a post-session integrity snapshot and restore of harness-owned files plus a scope
    check.
  - The restore covers all of `.harness/` and `.claude/` (a maker could otherwise drop the hook config or add local
    settings), and out-of-scope changes are reverted by the harness, since the maker is not allowed to.
  - The test-count baseline is persisted per feature, so a rerun cannot launder an uncollected test. The gate also
    rejects a lower count (deleted tests), and it proves each new test file adds tests: the count is taken again with
    that file hidden.
  - The prompt goes on stdin.
  - There is no `HARNESS_ROOT` env override; the hook gets `--root`.

## 2026-10-09: `--claude` extends an existing `.claude/` instead of proposing
- Reason: user decision — "projects that already have .claude: only add to it, delete nothing, except items with the
  same name as ours".
- How: .claude/settings.json is merged additively (user keys and rules kept, missing harness rules appended, only the
  harness's own `hook-stop` Stop entry replaced); agents/harness-checker.md and `skills/harness-optimize/` are replaced
  by name; everything else in `.claude/` is untouched. Invalid JSON → untouched + proposal. Idempotent.
- Rejected: proposals for `.claude/` items (the user would have to merge by hand every time).
- Constraint: this is the only exception to "retrofit never overwrites a user file", and only with `--claude`.

## 2026-10-09: Claude Code guardrails are opt-in (`retrofit --claude`)
- Reason: user decision. The default harness should be agent-agnostic and not change how Claude Code behaves in a
  project (permissions prompts, a Stop hook at the end of every turn) unless explicitly asked.
- Rejected alternatives: deleting the guardrails entirely (loses a working, tested option); keeping them on by default.
- Consequences: `retrofit` writes no `.claude/` unless `--claude`; review-before-verify is described agent-agnostically
  (fresh session or human); `hook-stop` stays in the runtime; this repo removed its own `.claude/`.
- Expires when: the user asks to turn them back on by default.

## 2026-10-07: Apply harness engineering to the project
- Reason: the legacy project lacked agent guidance, standard verification, and cross-session state; agents had to guess.
- Rejected alternatives: one large instruction file (it bloats, and rules in the middle get ignored).
- Constraints: `AGENTS.md` is a short router; standard commands live in `.harness/config.json`; feature status changes only via `scripts/harness.py`.
- Expires when: not applicable.

## 2026-10-07: All user-facing text in English
- Reason: user decision; skills and templates are consumed by agents across teams.
- Constraint: `runtime.py` keeps a Vietnamese regex alternative only to read older PROGRESS.md files.

## 2026-10-07: PROGRESS.md and `active` status instead of the course's claude-progress.md / in_progress
- Reason: lessons 02/03/05 and `audit-harness.sh` use PROGRESS.md; lessons 07/08 use `active`.
- Rejected: forcing one schema — the runtime reads aliases (`state`, `in_progress`, `done`, `passes`) so course-format lists still work.

## 2026-10-07: `verification` is a runnable command, not a list of manual steps
- Reason: the pass-gate must be executed by the harness, not judged by the agent (lesson 08).

## 2026-10-07: Stop hook does only cheap checks
- Reason: Stop fires at the end of every assistant turn; running tests there would make every turn slow.
- Consequence: tests/build are enforced by `verify` and `exit-check`, not the hook.

## 2026-10-15 — 2.0.1: fixes from three independent reviews (code, end-to-end install, docs)

- Harness hooks are recognized by the dispatcher path after any launcher (`py`, `py -3.12`, `uv run python`…):
  before, `--python py` hooks were duplicated on update and left behind on uninstall, pointing at a deleted dispatcher
  (exit 2 blocks every prompt). Uninstall now keeps the dispatcher while any harness hook is still registered.
- Hook stdin is decoded as UTF-8 from bytes (Windows used the ANSI code page: the guard missed non-ASCII paths).
- The pass-gate is closed against its own inputs: `verification`, `verify_check`, `scope`, `cwd`, `activated_commit`
  of an active or passing feature are locked; duplicate ids are denied; a broken feature list can be repaired.
- The permission allow list no longer covers `harness.py *`: `try`, `verify`, `setup`, `loop` run commands from
  agent-editable files and go through the normal prompt. Edits of the harness configuration are guarded with `ask`.
- `install` never replaces a `scripts/harness.py` that lacks the harness-kit header; re-running `install` on an
  installed project is an `update` (no proposals for identical files, no bogus legacy note, manifest untouched when
  nothing changed, no machine-specific kit path in committed files); postinstall runs only the first time and never
  in CI. install.ps1 runs in a script block and survives a Python probe that writes to stderr.

## 2026-10-15 — 2.1.0: the remaining requirements and review leftovers

- R1: path-scoped rules in `.claude/rules/` (harness state files, tests) load only when those files are touched.
- R5: a failed hook is visible (`systemMessage`), not only on stderr; `"guard_failure": "closed"` is the opt-in
  fail-closed guard (the dispatcher writes the deny itself, so it does not depend on `onFailure`, 2.1.295+).
- R9: `--diff` prints a unified diff of every existing file a plan changes. Updates keep entries the user removed
  (rules, hooks, status line: recorded as `settings_offered` in the manifest), retire rules an older version added
  (2.0.0's `Bash(python scripts/harness.py *)`), and `--force` restores everything. Uninstall removes the `$schema`
  key the harness added.
- R11: the Stop hook blocks once when the active feature has files newer than its last recorded verification
  (`try` now leaves a trace record).
- R13: every injected context (SessionStart, UserPromptSubmit) and every guard decision is traced with the session
  id and a content hash; the trace rotates; `trace --grep`.
- R16: state files are written atomically (temporary file + rename, retried on Windows sharing errors).
- R18: every key the runtime reads has a default; `validate` checks unknown keys, types and enum values; a test pins
  that the code reads no key without a default. Per-hook switches (`hooks_off`); turning off `guard`/`stop` is logged.
- R21: `doctor` compares the Claude Code version with the one the hooks were verified on (2.1.287) and names the
  `onFailure` minimum (2.1.295).
- Instruction files (AGENTS.md, CLAUDE.md, `.claude/agents|skills|rules|output-styles/`) ask before an edit
  (`instruction_guard`): instructions an agent writes are input to review. `python -c`/`node -e` one-liners that
  write a state file are denied (heuristic).
- PowerShell twins of every Bash rule: on Windows with Git Bash, a Bash deny rule alone turns the PowerShell tool off.
- Kept by design: `next` while a feature is active prints it and exits 0 (an idempotent "what am I on"); the status
  line keeps its relative path (Claude Code documents no project-dir variable for it; from a subdirectory it shows
  an error instead of the harness line — listed in Known Issues).


## 2026-10-15 — 2.1.1: version check at install, CI, .git protected

- R21: the install message warns when Claude Code is older than the version the hooks were verified on (2.1.287).
- R19: GitHub Actions runs the unit tests on Ubuntu, Windows and macOS with Python 3.10 and 3.12.
- R3: `Edit(./.git/**)` is denied: git internals change only through git.

## 2026-10-15 — 2.1.2: Python 3.10 reads TOML again

- Python 3.10 has no `tomllib`: the kit silently skipped `pyproject.toml` / `Cargo.toml`, so detection was weaker
  than on 3.11+ although 3.10 is the documented minimum. Found by the new CI (Ubuntu, Python 3.10). A small
  stdlib TOML reader is the fallback; a test pins it against `tomllib` on pyproject, Cargo and toolchain files.

## 2026-10-16 — 2.1.3: a red baseline from future features is explained, not just failed

- Found in experiment 2 (multi-session): tests written up front for later features kept the project check red, so
  `verify` could never pass the first feature, and WIP=1 plus the scope guard forbade fixing it inside that feature;
  the agent blocked, forced and unblocked features in a loop. `verify` now says when its own verification passed,
  which product features have not started, and what to do (pending tests or leave them out of `commands.test`).
