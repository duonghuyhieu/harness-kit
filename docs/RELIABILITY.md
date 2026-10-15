# Reliability — harness

> How the system proves it is healthy and can restart.

## Standard path

| Task | Command |
|---|---|
| Bootstrap | `./init.sh` (Windows: `.\init.ps1`) |
| Verify | `python scripts/harness.py check` / `check --full` |
| Smoke (quick startup) | declare `commands.smoke` in `.harness/config.json` |

## Required runtime signals

- Structured logs (single-line JSON: `timestamp, level, component, action, duration_ms, correlation_id, error`) at service boundaries.
- Health checks for the main services.
- User-visible errors for every recoverable failure.
Signals are files under `.harness/`: `trace.jsonl` (every runtime command), `last-check.json` (last verification with per-step
status), `failures.jsonl` (agent failures by layer, committed), `session.json` (clock-in point). View with `python scripts/harness.py trace -n 20`.

## Golden journeys

| Journey | Verification | Failure signal |
|---|---|---|
| Retrofit a Python repo | `test_creates_files_and_never_overwrites` | file overwritten / placeholder left |
| Pass-gate | `test_pass_gate_and_wip` | passing without evidence |
| Clean-state guard | `test_stop_hook_blocks_on_debug_code_and_respects_loop_guard` | hook exit code != 2 |

## Rules

- A feature is not done if the system cannot restart cleanly afterwards.
- Runtime failures must be diagnosable from signals in the repo (logs, traces), not guesswork.
- Recurring failure → add a benchmark/guardrail (`.harness/rules.json` or a test).
- Cleanup is part of reliability: `python scripts/harness.py exit-check`.
