# Quality — harness

> A snapshot of each module's health over time. New sessions read this file to choose what
> to optimize: **fix the lowest-graded module first**. Update after every significant session.

**Scale:** **A** verification passes, clean architecture, agent-readable, stable tests ·
**B** passes, minor gaps · **C** partially works, has gaps, hard to understand · **D** broken or major structural problems.

## Modules

| Module | Grade | Verification | Agent-readable | Test stability | Boundaries | Main gaps | Updated |
|---|---|---|---|---|---|---|---|
| `harness_kit/` | A− | `check --full` + regression of 14 features pass | yes (AGENTS.md, docs) | stable (36 tests) | R100 enforced | runtime is one 1.1k-line file by design | 2026-10-08 |

| File | Grade | Evidence |
|---|---|---|
| `runtime.py` | A− | 13-scenario characterization golden + state-machine, hook and scope tests; `cmd_verify` 20 / `cmd_exit_check` 16 lines (R002). Stays one file on purpose (copied verbatim into projects) |
| `detect.py` | B | 8 stacks pinned (T001), cmd.exe-safe commands (B004), bounded .NET scan / F# / VB (B005), declared-membership multi-module roots (B003, 3 checker rounds). Not yet run on real non-Python/Node repos |
| `scaffold.py` | A | 6-scenario plan golden; `build_plan` 20 lines (R003); line-ending pinning via `git check-attr` (B001/B002) |
| `audit.py` | A | 4-scenario check golden; one builder per subsystem, all ≤ 18 lines (R001) |
| `cli.py` | B | thin, exercised indirectly through the modules it calls |

## Hotspots (optimization candidates)

Score = lines * (1 + commits in the last 12 months). Files that are large **and** frequently changed carry the highest optimization risk/reward.

| File | Lines | Commits (12 mo) | Score |
|---|---|---|---|
| `harness_kit/runtime.py` | 1058 | 0 | 1058 |
| `harness_kit/detect.py` | 407 | 0 | 407 |
| `harness_kit/scaffold.py` | 364 | 0 | 364 |
| `tests/test_kit.py` | 359 | 0 | 359 |
| `harness_kit/audit.py` | 248 | 0 | 248 |

## Largest files

| File | Lines |
|---|---|
| `harness_kit/runtime.py` | 1058 |
| `harness_kit/detect.py` | 407 |
| `harness_kit/scaffold.py` | 364 |
| `tests/test_kit.py` | 359 |
| `harness_kit/audit.py` | 248 |
| `harness_kit/cli.py` | 87 |
| `harness.py` | 10 |
| `harness_kit/__main__.py` | 5 |
| `harness_kit/__init__.py` | 4 |

## Harness simplification log

| Date | Component removed (trial) | Result | Decision |
|---|---|---|---|
| | | | |

## Change history

### 2026-10-08
- Promoted: `detect.py` C → B (T001, B003–B005), `audit.py` B → A (R001), `scaffold.py` B → A (R003, B001/B002),
  `runtime.py` B → A− (R002). Every refactor had a characterization golden committed first.
- Independent checker verdicts: 1 process Fail (B001: verify ran before review), 2 Fail-then-fixed (B002, B003),
  the rest minor/acceptable. Hotspot tables above are the 2026-10-07 baseline.

### 2026-10-07
- Baseline after harness retrofit (automated figures above).
