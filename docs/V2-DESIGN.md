# harness-kit v2 — design

> Status: accepted 2026-10-15. v2 inherits everything from v1 (`D:/harness`, 19/19 features passing, 85 tests) and
> adds a Claude Code layer that installs into a project's `.claude/`, in the spirit of ClaudeX/claudekit
> (see the ClaudeX analysis CK01 in the knowledge base of the research repository, harness-v2), but built on the harness-engineering principles of
> the research paper in `research/`.

## 1. Goals

1. **One-command install** into any project: `harness install` (or `npx`, `install.ps1`, `install.sh`) writes the
   agent-agnostic harness files (v1 retrofit) **and** a Claude Code layer: agents, skills (`/hk-*` slash commands),
   hooks, a status line and least-privilege permissions.
2. **Safe lifecycle**: a manifest with per-file hashes makes `update` and `uninstall` safe — files the user changed are
   never overwritten or deleted; `settings.json` is merged semantically, never replaced.
3. **The harness enforces, prompts only guide**: every rule that matters is backed by a runtime check
   (pass-gate, WIP=1, scope, clean state). Hooks are cheap, deterministic guardrails; the real gates stay
   `verify` and `exit-check`.
4. **Agent-agnostic core**: the project files and `scripts/harness.py` keep working for Codex/Cursor/other agents;
   the `.claude/` layer is an adapter on top.

Non-goals: domain skills (UI, payments, media…), network calls, third-party dependencies, reading Claude credentials.

## 2. Lessons taken from claudekit (CK01)

| claudekit problem | v2 answer |
|---|---|
| `settings.json` copied whole: skipped when present (hooks silently missing) or overwritten with `--force` | semantic merge: harness entries are added per event, identified by the dispatcher path; user entries kept |
| no manifest, no uninstall, stale files after upgrades | `<project>/.claude/harness-kit.json` manifest (sha256 per file) → `update`, `uninstall`, `doctor` |
| relative hook paths break when started from a subdirectory | hook commands use `"$CLAUDE_PROJECT_DIR"`; the status line (which gets no such variable) uses `<project>/.claude/hooks/harness_hook.py` relative to the session directory |
| one node process per hook script, 3–6 per tool call | one Python dispatcher per event, logic in the runtime, loaded in-process |
| hooks that "talk to nobody" (wrong output fields) | each event's output contract is unit-tested (`hookSpecificOutput`, `decision`, exit codes) |
| `permissionDecision: "allow"` used to inject hints (auto-approves every write) | the harness never returns `allow`; only `deny`/`ask` or no decision |
| no `permissions` block | ships `deny` for secrets and force-push, `ask` for destructive commands |
| 79 skills / 21 MB copied into every project | 16 focused skills, ~100 KB, all about the harness workflow |
| MUST/MANDATORY prompts repeated in many files | single source of truth: `AGENTS.md` + runtime; skills reference commands, hooks inject only dynamic state |
| postinstall writes into the repo on every `npm install` | the postinstall of `npm install -D` installs only the first time (no `<project>/.claude/harness-kit.json` yet), never in CI, never for global/npx/transitive installs, and never fails npm; updates are explicit (re-run the install command) |

## 3. Layout after `harness install` (level `standard`)

```text
AGENTS.md  CLAUDE.md  PROGRESS.md  feature_list.json  init.sh  init.ps1   (v1 retrofit, never overwritten)
docs/ …  .harness/config.json  .harness/rules.json  scripts/harness.py      (v1 retrofit)
.claude/
  harness-kit.json            manifest: version, kit root, python, files + sha256
  settings.json               merged: hooks, permissions, statusLine (only if absent)
  hooks/harness_hook.py       dispatcher: loads scripts/harness.py, runs hook-<event>, fails open
  agents/hk-*.md              6 subagents
  skills/hk-*/SKILL.md        16 skills = slash commands /hk-*
  output-styles/hk-evidence.md
```

## 4. Hooks

All hooks run `python "$CLAUDE_PROJECT_DIR/.claude/hooks/harness_hook.py" <event>`. The dispatcher imports the
project's `scripts/harness.py` and calls the matching runtime command in-process. Any crash or a missing runtime ends
with exit 0 (fail-open) plus a warning the user sees (`systemMessage`): hooks are guardrails, the gates are
elsewhere, and a broken hook must never brick a session. Opt-in exception: `"guard_failure": "closed"` makes a
crashed or unreadable guard call a deny (the dispatcher writes the decision itself, so it works on every Claude Code
version; hook `onFailure: "block"` needs 2.1.295+). Every injected context and every guard decision is written to
`.harness/trace.jsonl` with the session id and a content hash (rotated); `hooks_off` switches single hooks off and
logs it for `guard` and `stop`. State files are written atomically (temporary file + rename). Secrets are protected by `permissions.deny`, which does not depend on hooks.

| Event (matcher) | Runtime command | Behaviour | Lesson |
|---|---|---|---|
| SessionStart (`startup\|resume\|clear\|compact`) | `hook-session-start` | injects ≤ 40 lines: features n/m, VCR, active feature + verification, next step, last check + age, uncommitted files; after `compact` also the pre-compaction snapshot | L03, L05 |
| UserPromptSubmit | `hook-prompt` | when a feature is active: one reminder line (id, scope, WIP=1); deduplicated (state hash, 10-minute TTL) | L07 |
| PreToolUse (`Edit\|Write\|MultiEdit\|NotebookEdit\|Bash\|PowerShell`) | `hook-guard` | deny hand edits of harness-owned state (`status`/`evidence`/`attempts` in the feature list, `.harness/` logs), of how a started feature is judged (`verification`, `verify_check`, `scope`, `cwd`, `activated_commit`) and duplicate ids; `ask` for writes outside the active feature's scope (config `scope_guard`) and for edits of the harness configuration (`.harness/config.json`, `.claude/settings*.json`, the dispatcher) | L07, L08 |
| PostToolUse (`Edit\|Write\|MultiEdit\|NotebookEdit\|Bash\|PowerShell`) | `hook-activity` | appends `{ts, session, tool, file or command}` to `.harness/activity.jsonl` (git-ignored; append-only, rotated atomically) | L11 |
| PreCompact | `hook-precompact` | writes `<project>/.harness/compact-snapshot.md` (status, active feature, files touched this session) | L05 |
| Stop | `hook-stop` (v1) | blocks finishing on cheap violations (debug code, junk, hand-edited passing, WIP > 1) and when the active feature has changes newer than its last recorded verification (`try`, `verify`, `check`); never twice in a row | L08, L12 |
| SessionEnd | `hook-session-end` | logs the session end and whether a handoff happened | L11, L12 |
| statusLine | `statusline` | `HK │ F012 active │ 14/19 passing · VCR 0.93 │ check PASS 12m │ ctx 42%` (+ `handoff soon` above 70 % context: the compaction snapshot is the fallback) | L05, L11 |

## 5. Skills (slash commands)

| Skill | Purpose | Main runtime commands |
|---|---|---|
| `/hk-help` | map of the workflow and commands | `status` |
| `/hk-init` | initializer phase: fill knowledge, baseline H001–H006, no product code | `audit`, `check`, `docs --strict` |
| `/hk-start` | clock-in: the 9-step startup flow | `start`, `status` |
| `/hk-plan` | request → features (behaviour, verification command, scope, deps), sprint contract | `add`, `validate` |
| `/hk-implement` | exactly one feature: next → maker → try → independent review → verify | `next`, `try`, `verify` |
| `/hk-review` | independent review of the diff by `hk-reviewer` with the rubric | — |
| `/hk-verify` | pass-gate with pre-checks (review done, scope) | `verify` |
| `/hk-handoff` | clock-out: handoff, PROGRESS sections, exit-check, commit | `handoff`, `exit-check` |
| `/hk-audit` | score the 5 subsystems, bottleneck, smallest next artifact (method map) | kit `audit`, `failures` |
| `/hk-diagnose` | failure → layer → `fail --layer` → fix the harness layer | `fail`, `failures` |
| `/hk-loop` | autonomous maker→reviewer→gate loop, or a metric goal loop | `loop`, `report` |
| `/hk-graph` | decide whether a graph is warranted (≥ 3/5 criteria) and document it | — |
| `/hk-optimize` | legacy refactor playbook: characterization tests first | `size`, `add`, `verify` |
| `/hk-observe` | runtime + process observability: logs, golden paths, trace | `trace`, `report` |
| `/hk-clean` | clean state and harness simplification (ablation log) | `exit-check` |
| `/hk-docs` | repo as system of record: fresh-session test, docs check | `docs --strict` |

Skill names use hyphens (the Agent Skills spec requires the name to match the directory); the `hk-` prefix is the
namespace.

## 6. Agents

| Agent | Tools | Role |
|---|---|---|
| `hk-initializer` | Read, Grep, Glob, Bash, Edit, Write | initialization phase only; never product code |
| `hk-planner` | Read, Grep, Glob, Bash | turns intent into verifiable features and sprint contracts; does not edit code |
| `hk-maker` | Read, Grep, Glob, Bash, Edit, Write | implements one active feature inside its scope; never changes feature status |
| `hk-reviewer` | Read, Grep, Glob, Bash | independent, read-only checker with the rubric; returns a verdict |
| `hk-debugger` | Read, Grep, Glob, Bash | evidence-driven diagnosis; attributes failures to a harness layer |
| `hk-docs-keeper` | Read, Grep, Glob, Edit, Write | keeps the repo the system of record; runs the fresh-session test |

## 7. Lifecycle commands

- `harness install [path]` — retrofit + Claude layer + manifest. `--global` installs only skills and agents into
  `~/.claude` (no hooks: they need a project runtime). On a project that already has a manifest, `install` runs
  `update`. `harness retrofit [path]` = the v1 agent-agnostic files only.
- `harness update [path]` — re-render: unmodified files are replaced, modified ones get a proposal in
  `.harness/proposals/`, files dropped from the kit are removed if unmodified; the runtime is upgraded.
- `harness uninstall [path]` — removes unmodified manifest files and the harness entries in `settings.json`; the
  project's harness files (AGENTS.md, feature list…) are project content and stay.
- `harness doctor [path]` — Python, runtime version, settings JSON, hook registration, dispatcher smoke run,
  manifest integrity.

## 8. Distribution

- `python <kit>/harness.py install .` — nothing to install first.
- `pip install -e <kit>` → `harness install .`
- `npx <kit-path-or-git-url> install .` — `bin/cli.js` (zero dependencies) finds Python and forwards the arguments.
- `install.ps1` / `install.sh` — find Python, run the kit installer on the current directory.
