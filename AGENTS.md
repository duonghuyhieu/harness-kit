# AGENTS.md — harness-kit

A Python tool (standard library only) that installs harness engineering into projects: `install`/`update`/`uninstall`/
`doctor` (with a Claude Code layer in `.claude/`), `audit`, `retrofit`, and a runtime that drives the feature list.
This repo is itself run under its own harness. Design of v2: `docs/V2-DESIGN.md`.

Stack: Python >= 3.10 (stdlib only; the copied runtime targets >= 3.9). Usage: `README.md`. Rationale: `docs/HARNESS-PRINCIPLES.md`.

## Hard constraints (non-negotiable)

- Never mark work done without runnable evidence: feature status changes only via `python scripts/harness.py verify <id>` (source: harness-kit pass-gate).
- Never delete, skip, or loosen tests to get a pass (source: harness-kit).
- No dependencies outside the standard library: kit and runtime must run on a bare machine with only Python (source: design goal; expires when: never).
- `harness_kit/runtime.py` stays **standalone** (no imports from `harness_kit`, Python >= 3.9): it is copied verbatim as `scripts/harness.py` into target projects (source: design; expires when: the runtime ships as a package).
- `retrofit`/`install` never overwrite a user's existing file — conflicts go to `.harness/proposals/` (source: course harness-creator design rule). Exceptions: `<project>/.claude/settings.json` is merged entry by entry, and harness files in `.claude/` are replaced only while unchanged since install (manifest sha256), or with `--force` (source: user decisions 2026-10-09 and 2026-10-15).
- Hooks never answer `permissionDecision: "allow"`; the dispatcher fails open, and only the Stop hook may exit 2 (source: v2 design, claudekit analysis 2026-10-15).
- Templates must not contain fake paths with `/` in backticks or markdown links — the `docs` check reports them as broken; use `<...>` placeholders (source: test failure 2026-10-07).
- `.ps1` files are written as UTF-8 with BOM (source: mojibake on Windows PowerShell 5.1, 2026-10-07).
- All user-facing text (templates, skills, CLI/runtime messages, docs) is written in English (source: user decision 2026-10-07).

## Session start (clock-in)

1. `python scripts/harness.py start` — next step, recent commits, feature status, baseline check.
2. Read `PROGRESS.md` and `docs/DECISIONS.md`.
3. Baseline red → fix it first. Then `python scripts/harness.py next` (WIP=1).

## Standard commands

| Purpose | Command |
|---|---|
| Set up environment | `./init.sh` (Windows: `.\init.ps1`) |
| Test | `python -m unittest discover -s tests -t . -q` |
| Static check | `python -m compileall -q harness_kit tests harness.py` |
| Smoke | `python harness.py --help` |
| **Quick verification** | `python scripts/harness.py check` |
| **Full verification** | `python scripts/harness.py check --full` |

Commands are declared in `.harness/config.json`.

## Working rules

- **One feature at a time.** Declare `--scope` globs when adding a feature; `verify` flags changes outside them.
- **Runtime changed** → run tests, then `python harness.py upgrade .` so `scripts/harness.py` (this repo's own copy) matches `harness_kit/runtime.py`.
- **New behavior** → a test in `tests/test_kit.py` (v1 core) or `tests/test_v2.py` (hooks, installer) that exercises it on a temporary project (see `make_python_project`).
- **Claude layer changed** (`harness_kit/templates/claude/`) → skill `name` = directory name; hook output follows each event's contract (`hookSpecificOutput`); test it through the dispatcher (`run_dispatcher` in `tests/test_v2.py`).
- **New audit check** → keep 5 checks per subsystem (the score is out of 25).
- **Edit Python sources with the editor tool, not shell heredocs** — escaping broke `tests/test_kit.py` twice (see `.harness/failures.jsonl`).
- **Something went wrong?** `python scripts/harness.py fail --layer <instructions|tools|environment|state|feedback> --note "..."`, then fix that layer.
- Try changes on a realistic legacy project: `python harness.py audit <path>` · `python harness.py retrofit <path> --dry-run`.

## Definition of done

1. `python scripts/harness.py check` passes (compileall + all unit tests).
2. The feature's `verification` command passes via `verify <id>`.
3. `scripts/harness.py` is upgraded if the runtime changed; docs updated if behavior changed.
4. `python scripts/harness.py exit-check` prints CLEAN STATE.

## Docs map (read when needed)

| Document | Read when |
|---|---|
| `docs/ARCHITECTURE.md` | Changing module boundaries, adding a stack detector, adding a generated file |
| `docs/PRODUCT.md` | Unsure what a command should do for the user |
| `docs/RELIABILITY.md` | Debugging a retrofit/runtime run (trace, last-check, failures log) |
| `docs/QUALITY.md` | Choosing what to refactor next |
| `docs/DECISIONS.md` | Before changing an established approach (schema, file names, language) |
| `docs/HARNESS-PRINCIPLES.md` | Mapping a course lesson to a mechanism |
| `docs/CAPABILITIES.md` | Explaining what the kit does (and does not do) to a user |
| `docs/V2-DESIGN.md` | Changing the Claude Code layer: hooks, skills, agents, installer, manifest |
| `docs/HUONG-DAN.md` | The Vietnamese user guide (keep it in sync with README.md) |
| `docs/harness/` | Sprint contract, evaluator rubric, handoff, clean-state checklist, refactor playbook |

## Session end (clock-out)

1. `python scripts/harness.py handoff`, then write Done / In Progress / Known Issues / Next Steps in `PROGRESS.md`.
2. `python scripts/harness.py exit-check` — must print CLEAN STATE.
3. Commit verified work with the feature id and *why*.
