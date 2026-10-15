# Product — {{project_name}}

> What agents **cannot** infer from the code: who uses it, for what, and what "correct" means.
> Ambiguous behavior is a spec gap, not a license to guess.

## Core

<!-- HARNESS:FILL: Primary users / Jobs they need done / Pain to remove / Quality bar for acceptance. -->

## Golden journeys (main user flows)

<!-- HARNESS:FILL: the 3–5 most important flows. Each flow: preconditions → steps → observable outcome.
Each flow should have an automated verification command (e2e test / script / curl) and a matching feature in feature_list.json. -->

## Product rules

- Prefer user-visible reliability over feature count.
- No hidden destructive actions; no silent failures.
- Changing user-visible behavior → update this file in the same session.

## Out of scope

<!-- HARNESS:FILL: what the product intentionally does NOT do, so agents don't add it on their own. -->
