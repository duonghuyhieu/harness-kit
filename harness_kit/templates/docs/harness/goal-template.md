# Goal template for autonomous loops

Write the goal like this document, then hand it to `/goal` (or the maker agent). The more specific and
machine-checkable it is, the better the loop performs. Three required parts: **goal + how to verify + stop conditions**.

## Goal
(One sentence.) Example: Complete every `refactor` feature that is not_started in feature_list.json.

## Acceptance criteria (one command per line)
- [ ] `python scripts/harness.py check --full` passes
- [ ] `python scripts/harness.py validate` passes
- [ ] Every target feature is passing (`python scripts/harness.py status`)

## Scope
### May change
- (specific directories/modules)
### Must not touch
- DB migrations, dependency versions, CI/CD config, public APIs

## Approach
1. `python scripts/harness.py start`, read `AGENTS.md`.
2. `next` → do exactly one feature → independent review → `verify <id>` (review first: `passing` is one-way).
3. Stuck for more than 2 rounds on one feature: change approach or `block` it with a reason.
4. At the end of each round, update the loop state (`docs/harness/loop-state-template.md`).

## Stop conditions
- All acceptance criteria pass
- At most 20 rounds
- 3 consecutive rounds without progress (the same error repeating)
- An issue needs a human decision (missing permissions, missing business info, API-breaking change)

> The code author does not grade their own work: an independent reviewer (a fresh agent session or a human; the
> `harness-checker` subagent when retrofitted with `--claude`) reviews every feature *before* `verify` marks it passing.
