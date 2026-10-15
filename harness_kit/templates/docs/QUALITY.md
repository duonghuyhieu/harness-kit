# Quality — {{project_name}}

> A snapshot of each module's health over time. New sessions read this file to choose what
> to optimize: **fix the lowest-graded module first**. Update after every significant session.

**Scale:** **A** verification passes, clean architecture, agent-readable, stable tests ·
**B** passes, minor gaps · **C** partially works, has gaps, hard to understand · **D** broken or major structural problems.

## Modules

| Module | Grade | Verification | Agent-readable | Test stability | Boundaries | Main gaps | Updated |
|---|---|---|---|---|---|---|---|
{{quality_rows}}

<!-- HARNESS:FILL: grade each module A–D based on evidence (tests, lint, file length, number of violations). -->

## Hotspots (optimization candidates)

Score = lines * (1 + commits in the last 12 months). Files that are large **and** frequently changed carry the highest optimization risk/reward.

| File | Lines | Commits (12 mo) | Score |
|---|---|---|---|
{{hotspot_rows}}

## Largest files

| File | Lines |
|---|---|
{{largest_rows}}

## Harness simplification log

| Date | Component removed (trial) | Result | Decision |
|---|---|---|---|
| | | | |

## Change history

### {{today}}
- Baseline after harness retrofit (automated figures above).
