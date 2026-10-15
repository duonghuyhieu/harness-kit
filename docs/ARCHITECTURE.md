# Architecture — harness

> Top-level map of the system. Keep it short; per-module details live next to the code
> (`<module>/ARCHITECTURE.md`). Update it **in the same commit** as any boundary change.

## System shape

- Stack: Python >=3.10 (pip)
- Size: 9 code files, ~2,542 lines, 1 test file.
- Surfaces: a CLI (`harness.py` launcher / `harness` console script), a standalone runtime copied into target projects,
  and Claude Code skills/subagents. No services, no network calls, no third-party packages.

## Directory layout

```text
docs/  HARNESS-PRINCIPLES.md
harness_kit/  __init__.py, __main__.py, audit.py, cli.py, detect.py, installer.py, runtime.py, scaffold.py, templates/ …
bin/cli.js  npx launcher (finds Python, forwards to harness.py) · install.sh / install.ps1  wrappers
skills/  harness-retrofit/
tests/  test_kit.py
AGENTS.md
harness.py
pyproject.toml
README.md
```

## Module map

| Module | Responsibility | Main entry point | Allowed dependencies |
|---|---|---|---|
| `harness_kit/` | ? | ? | ? |

Inside `harness_kit/`:

| File | Responsibility | Entry point | May import |
|---|---|---|---|
| `detect.py` | Read-only stack/command/hotspot detection | `inventory()` | stdlib only |
| `audit.py` | 5-subsystem score, Fresh Session Test, clean-state scan, observed failures | `run_audit()` | `detect`, `runtime` |
| `scaffold.py` | Render templates, plan create-vs-propose, generate config/features/rules/settings | `retrofit()` | `detect`, `runtime` |
| `runtime.py` | Feature state machine, check, exit-check, hooks (copied as `scripts/harness.py`) | `main()` | **stdlib only** |
| `installer.py` | install / update / uninstall / doctor / global install of the Claude Code layer (manifest) | `install()` | `detect`, `scaffold`, `runtime` |
| `cli.py` | argparse front-end | `main()` | all of the above |
| `templates/` | Files generated into target projects | — | — |
| `templates/claude/` | The `.claude/` payload: agents, `hk-*` skills, `harness_kit/templates/claude/hooks/harness_hook.py` (standalone dispatcher), output style | — | — |

## Layer model and dependency rules

`detect` → `audit` / `scaffold` → `cli`. `runtime` is a leaf: everything may import it, it imports nothing from the kit
(enforced by rule R100 in `.harness/rules.json`). Templates are data; they reference runtime commands only by CLI name.

## Machine-enforced boundaries

The most important rules must have automated checks in `.harness/rules.json`
(run by `python scripts/harness.py rules`, included in `check`). Each rule has a WHY and a FIX.

## Hotspots (hard to change safely)

See the Hotspot section of `docs/QUALITY.md`. `runtime.py` (largest, copied into every project — a bug ships everywhere; always add a test and `upgrade`),
`detect.py` (per-stack command heuristics are easy to get subtly wrong), and template text that `runtime.py` parses
(PROGRESS.md `## Next Steps`, the auto markers, `## Hard constraints`).

## When changing the architecture

1. Update this file if the module map or boundaries change.
2. Record the reason in `docs/DECISIONS.md`.
3. If the rule must hold long-term → add a check to `.harness/rules.json`.
