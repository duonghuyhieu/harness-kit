# Evaluator rubric (for an independent checker)

Use after implementation, before acceptance. Every score must come with **evidence** (command output, file:line).

| Category | Question | Score (0–2) | Evidence |
|---|---|---|---|
| Correctness | Does behavior match the feature's requirements? Edge cases, error handling? | | |
| Verification | Did lint/type/test/e2e actually run, with output? | | |
| Scope discipline | Only touched the scope of the active feature? | | |
| Behavior preservation (refactor) | Characterization tests existed beforehand and still pass? Public API unchanged? | | |
| Reliability | Restart / rerun works without manual fixes? | | |
| Maintainability | Code and docs clear enough for the next session? No new duplication? | | |
| Handoff readiness | Can a new session continue from the repo alone? | | |

## Verdict
- Accept / Revise / Block

## Required follow-up
- Missing evidence:
- Required fixes:

> Evaluator calibration: run the rubric on a finished sprint, compare with a human's assessment,
> and make the criteria more specific wherever they diverge. Repeat 3–5 rounds and record the changes.
