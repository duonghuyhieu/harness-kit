# harness-kit

**Harness engineering for coding agents.** One command installs a harness that makes agent work verifiable —
a short `AGENTS.md` router, a feature list with a one-way pass-gate, session handoffs, a stdlib-only runtime — plus
a **Claude Code layer** in `.claude/`: 6 subagents, 16 `/hk-*` slash commands, lifecycle hooks and a status line.

> Vietnamese guide: [docs/HUONG-DAN.md](docs/HUONG-DAN.md). Design: [docs/V2-DESIGN.md](docs/V2-DESIGN.md).
> Principles (14 lectures → mechanisms): [docs/HARNESS-PRINCIPLES.md](docs/HARNESS-PRINCIPLES.md).
> Everything the kit does, with evidence and limits (incl. the autonomous loop and its trust model):
> [docs/CAPABILITIES.md](docs/CAPABILITIES.md).

The idea in one line: **the harness, not the agent, decides what is done.** A feature becomes `passing` only when
`scripts/harness.py verify <id>` runs its verification command and the project check; hooks deny hand edits of that
state, keep one feature active at a time, and block finishing a turn with debug code or junk left behind.

## Install — one command, then it just works

Run **one** of these **inside your project folder**. It creates `.claude/` (agents, `/hk-*` skills, hooks, status
line, settings) and the harness files right there. Then open the folder in Claude Code — that is all.

```bash
# Windows (PowerShell)
irm https://raw.githubusercontent.com/duonghuyhieu/harness-kit/main/install.ps1 | iex

# macOS / Linux / Git Bash
curl -fsSL https://raw.githubusercontent.com/duonghuyhieu/harness-kit/main/install.sh | bash

# npm (Node ≥ 18)
npx github:duonghuyhieu/harness-kit                  # installs into the current folder
npm install -D github:duonghuyhieu/harness-kit       # same, on the first `npm install` only (skipped in CI)
```

Requirement: Python ≥ 3.10 on PATH (no packages) — the hooks run on it. Re-running the same command updates the kit
(files you changed are kept). `/hk-init` is optional: run it once if you want the agent to fill in `AGENTS.md`,
`docs/` and the verification baseline for your project; `/hk-help` lists every command.

From a clone, any target folder: `python harness.py install /path/to/project` (or `install.sh` / `install.ps1` with a
path, or `pip install -e .` for a `harness` command on PATH).

In a new project the feature list starts with the harness baseline (H001…: real setup/test commands, a first test);
`next` picks those first. To start on your own feature right away: `python scripts/harness.py next --id F012`.

`install` never overwrites your files: an existing `AGENTS.md`, `CLAUDE.md` or feature list is kept and the
harness version goes to `.harness/proposals/`. An existing `.claude/settings.json` is merged entry by entry (your
hooks, permissions and status line are kept). Options: `--level minimal|standard|full`, `--no-statusline`,
`--python "py -3"`, `--dry-run`, `--diff` (dry run with a diff of every existing file it would change),
`--force` (also restore harness files and settings entries you changed or removed), `--git-init`, `--commit`,
`--global` (skills and agents into `~/.claude` only). Updates keep what you removed on purpose (a permission rule,
a hook, the status line) and retire rules an older version added.

`harness` below means the kit CLI: `python ~/.harness-kit/harness.py` after a one-line install (PowerShell:
`python "$HOME\.harness-kit\harness.py"`), `npx github:duonghuyhieu/harness-kit <command>`, `python harness.py` in a
clone, or `harness` after `pip install -e .`. The install prints the exact health-check command for your machine.

| Command | What it does |
|---|---|
| `harness install [path]` | project harness files + Claude Code layer + manifest (`.claude/harness-kit.json`, sha256 per file) |
| `harness update [path]` | refresh the `.claude/` layer and `scripts/harness.py`; files you changed are kept, the new version is proposed (re-running `install` does the same) |
| `harness uninstall [path]` | remove unchanged harness files from `.claude/` and the harness entries in `settings.json` |
| `harness doctor [path]` | read-only health check: manifest, runtime, settings, hooks registered, Python, smoke runs |
| `harness audit [path]` | score the 5 subsystems (instructions, tools, environment, state, feedback) |
| `harness retrofit [path]` | agent-agnostic files only (Codex, Cursor…); `--claude` = install |

## What gets installed

```text
AGENTS.md               router: hard constraints, clock-in, commands, definition of done, docs map, clock-out
CLAUDE.md               @AGENTS.md + the Claude Code notes
PROGRESS.md             handoff log (auto status block + Done / In Progress / Known Issues / Next Steps)
feature_list.json       features: behavior + verification command + scope + status (harness-owned)
init.sh / init.ps1      setup → check → how to run the app
docs/                   ARCHITECTURE, PRODUCT, DECISIONS, QUALITY, RELIABILITY, harness/ (rubric, contract, playbooks)
scripts/harness.py      the runtime (Python stdlib only)
.harness/               config.json (canonical commands), rules.json (machine-checked rules), state (git-ignored)
.claude/
  agents/               hk-reviewer, hk-maker, hk-planner, hk-debugger, hk-docs-keeper, hk-initializer
  skills/hk-*/          /hk-help /hk-init /hk-start /hk-plan /hk-implement /hk-review /hk-verify /hk-handoff
                        /hk-audit /hk-diagnose /hk-loop /hk-graph /hk-optimize /hk-observe /hk-clean /hk-docs
  hooks/harness_hook.py one fail-open dispatcher → scripts/harness.py hook-*
  output-styles/        hk-evidence (claims cite the command that proved them)
  rules/                path-scoped rules (harness state files, tests), loaded only when those files are touched
  settings.json         merged: hooks, least-privilege permissions, status line
  harness-kit.json      manifest
```

## The workflow

```text
/hk-start      clock in: state from disk, baseline check, exactly one feature (WIP=1)
/hk-plan       request → features with a runnable verification command and scope
/hk-implement  next → build inside scope → try → hk-reviewer (fresh, read-only) → verify
/hk-handoff    PROGRESS.md, decisions, exit-check = CLEAN STATE, commit
```

| Hook | Behaviour |
|---|---|
| SessionStart | injects the harness state (features, VCR, active feature + verification, next steps, last check); after compaction also the pre-compaction snapshot |
| UserPromptSubmit | one reminder line while a feature is active (deduplicated) |
| PreToolUse | **denies** hand edits of `status`/`evidence` in the feature list, of how a started feature is judged (`verification`, `scope`, `activated_commit`), duplicate ids and harness state files; **asks** before writing outside the active feature's scope (`scope_guard` in `.harness/config.json`) and before edits of the harness's own configuration (`.harness/config.json`, `.claude/settings.json`, the dispatcher); **denies** hook bypasses in every configuration (`git commit --no-verify`/`-n`, `core.hooksPath`, `GIT_CONFIG_*`, `claude --safe-mode`/`--bare`, `CLAUDE_CONFIG_DIR`, `disableAllHooks`); **asks** before edits that weaken the checker — lint/test/CI configs, `.harness/rules.json`, assertions removed or skips added in a test file (Edit/Write), tests deleted, moved or edited in place by a shell command (`checker_guard`); **denies** running hook commands by hand (`harness.py hook-*`, `harness_hook.py`); on an `hk-reviewer` launch records the content the review starts from and refuses a prompt that dictates the verdict; never answers "allow" |
| PostToolUse | appends what the session touched to `.harness/activity.jsonl` |
| PreCompact | writes `.harness/compact-snapshot.md` |
| Stop | blocks finishing while debug code, junk files, a hand-edited feature list or changes the active feature's verification has not seen yet remain, or when the session verified or committed work without updating PROGRESS.md (never twice in a row; a stop it lets through with problems open is recorded as an incident); warns the user, without blocking, about rationalizations in the final message ("pre-existing issue", "should work", "tests pass" with no check run) |
| SessionEnd | records whether the session ended with a handoff; unverified or un-handed-off work becomes an incident shown at the next session start |
| SubagentStop | records each `hk-reviewer` verdict (`VERDICT: APPROVE / REJECT / UNCERTAIN`) with a fingerprint of the change it reviewed: with `review_gate: require` (Claude Code installs), `verify` needs an APPROVE of exactly the current change; an APPROVE counts only when Claude Code's own transcript of that reviewer run (in `~/.claude/projects`) carries it and its prompt matches a recorded hk-reviewer launch; `verify <id> --no-review "<reason>"` is recorded as an incident. Evidence against skipping a review, not a security boundary against an agent writing code to forge one (docs/DECISIONS.md, 2.3.1) |

Hooks are cheap guardrails, not the gate: if Python or the runtime is missing they fail open (exit 0) with a
warning you see in Claude Code (`systemMessage`); set `"guard_failure": "closed"` in `.harness/config.json` to make a
crashed guard deny instead. Edits of instruction files (`AGENTS.md`, `CLAUDE.md`, `.claude/agents|skills|rules/`)
ask first (`"instruction_guard": "off"` to stop that); single hooks can be switched off with `"hooks_off": [...]`
(switching off `guard` or `stop` is logged). Every injected context is logged with its session id and a content
hash: `python scripts/harness.py trace --grep hook-`. `python scripts/harness.py validate` checks the config.
The real gates are `verify` and `exit-check`; secrets are protected by `permissions.deny`. The permission allow
list covers runtime commands that only read or change state by the runtime's rules (`start`, `status`, `next`, `add`,
`handoff`, `check`, `exit-check`…); `try`, `verify`, `setup` and `loop` run a command taken from an editable file, so
Claude Code asks before running them. Every Bash rule has a `PowerShell(...)` twin (on Windows a Bash deny rule
alone turns the PowerShell tool off). Disable hooks for one session with `HARNESS_HOOKS=off`. For filesystem and network isolation, also turn on Claude Code's own sandboxing:
the harness does not replace an OS-level sandbox.

## Runtime commands (any agent)

`start` · `status` · `next` · `try <id>` · `verify <id>` · `block/unblock` · `add` · `check [--full]` · `handoff` ·
`exit-check` · `fail --layer` / `failures` · `loop` (autonomous maker → reviewer → gate via `claude -p`) · `report` ·
`docs [--strict]` · `rules` · `size` · `trace`. Run `python scripts/harness.py --help`.

## Development

```bash
python -m unittest discover -s tests -t . -q     # 117 tests
python scripts/harness.py check                  # this repository runs under its own harness
```

See `AGENTS.md` for the rules of this repository.
