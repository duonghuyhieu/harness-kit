# Reliability — {{project_name}}

> How the system proves it is healthy and can restart.

## Standard path

| Task | Command |
|---|---|
| Bootstrap | `./init.sh` (Windows: `.\init.ps1`) |
| Verify | `python scripts/harness.py check` / `check --full` |
{{reliability_rows}}

## Required runtime signals

- Structured logs (single-line JSON: `timestamp, level, component, action, duration_ms, correlation_id, error`) at service boundaries.
- Health checks for the main services.
- User-visible errors for every recoverable failure.
<!-- HARNESS:FILL: where the current logger lives, how to view logs when running locally, health endpoint (if any). -->

## Golden journeys

<!-- HARNESS:FILL: list the critical flows (matching docs/PRODUCT.md) with a repeatable verification command and failure signals. -->

## Rules

- A feature is not done if the system cannot restart cleanly afterwards.
- Runtime failures must be diagnosable from signals in the repo (logs, traces), not guesswork.
- Recurring failure → add a benchmark/guardrail (`.harness/rules.json` or a test).
- Cleanup is part of reliability: `python scripts/harness.py exit-check`.
