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

## Install

Requirements: Python ≥ 3.10 (no packages). Node ≥ 18 only if you use `npx`.

```bash
# from a clone (any OS)
git clone https://github.com/duonghuyhieu/harness.git ~/harness-kit
python ~/harness-kit/harness.py install /path/to/project

# or the wrappers
~/harness-kit/install.sh /path/to/project          # macOS / Linux / Git Bash
.\harness-kit\install.ps1 C:\path\to\project       # Windows PowerShell
npx github:duonghuyhieu/harness install .          # npm (init is an alias of install)
pip install -e ~/harness-kit && harness install .  # a `harness` command on PATH
```

Then open the project in Claude Code and type **`/hk-init`** (initialization phase) or **`/hk-help`**.

`install` never overwrites your files: an existing `AGENTS.md`, `CLAUDE.md` or feature list is kept and the
harness version goes to `.harness/proposals/`. An existing `.claude/settings.json` is merged entry by entry (your
hooks, permissions and status line are kept). Options: `--level minimal|standard|full`, `--no-statusline`,
`--python "py -3"`, `--dry-run`, `--git-init`, `--commit`, `--global` (skills and agents into `~/.claude` only).

| Command | What it does |
|---|---|
| `harness install [path]` | project harness files + Claude Code layer + manifest (`.claude/harness-kit.json`, sha256 per file) |
| `harness update [path]` | refresh the `.claude/` layer and `scripts/harness.py`; files you changed are kept, the new version is proposed |
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
| PreToolUse | **denies** hand edits of `status`/`evidence` in the feature list and of harness state files; **asks** before writing outside the active feature's scope (`scope_guard` in `.harness/config.json`); never answers "allow" |
| PostToolUse | appends what the session touched to `.harness/activity.jsonl` |
| PreCompact | writes `.harness/compact-snapshot.md` |
| Stop | blocks finishing while debug code, junk files or a hand-edited feature list remain (never twice in a row) |
| SessionEnd | records whether the session ended with a handoff |

Hooks are cheap guardrails, not the gate: if Python or the runtime is missing they fail open (exit 0) with a note.
The real gates are `verify` and `exit-check`; secrets are protected by `permissions.deny`. Disable hooks for one
session with `HARNESS_HOOKS=off`. For filesystem and network isolation, also turn on Claude Code's own sandboxing:
the harness does not replace an OS-level sandbox.

## Runtime commands (any agent)

`start` · `status` · `next` · `try <id>` · `verify <id>` · `block/unblock` · `add` · `check [--full]` · `handoff` ·
`exit-check` · `fail --layer` / `failures` · `loop` (autonomous maker → reviewer → gate via `claude -p`) · `report` ·
`docs [--strict]` · `rules` · `size` · `trace`. Run `python scripts/harness.py --help`.

## Development

```bash
python -m unittest discover -s tests -t . -q     # 97 tests
python scripts/harness.py check                  # this repository runs under its own harness
```

See `AGENTS.md` for the rules of this repository.
