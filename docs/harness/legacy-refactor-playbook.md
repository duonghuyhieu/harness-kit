# Legacy code optimization playbook (within the harness)

Goal: improve legacy code **without changing behavior**, in small steps, each with
evidence. No tests means no refactoring — that is guessing.

## 0. Pick a target
- Open `docs/QUALITY.md`: prioritize the **lowest-graded** module, then **hotspots** (large + frequently changed files).
- One target = one feature. Scope fits one session: "Extract validation out of `OrderService.create`" (right size),
  not "Refactor the order module" (too broad) or "Rename variable x" (too narrow).

## 1. Create a feature with a verification command
```bash
python scripts/harness.py add --id R012 --category refactor \
  --behavior "OrderService.create split into validate/persist/notify; API behavior unchanged" \
  --verify "pytest tests/characterization/test_order_create.py -q && python scripts/harness.py size src/orders/service.py --max-lines 400" \
  --out-of-scope "DB schema, public API, payment module"
```
The `verification` command must exit 0 **only when** the goal is met (characterization tests + measurable metrics).

## 2. Characterization tests first (pin current behavior)
- Write tests that record the **current** output for representative inputs and edge cases (including "odd" behavior — that is the current contract).
- Tests must **pass on the old code** before you change a single line. Separate commit: `test(R012): characterize order create`.
- Hard-to-test code (DB/time/network dependencies): create a seam (inject the dependency, wrap the clock) with the smallest possible change; record it in DECISIONS if significant.

## 3. Change in small steps
- One transformation per step: extract function/module, rename, remove duplication, simplify conditionals, delete dead code.
- After each step: run the related tests. Red → revert that step; don't "keep patching until it passes".
- **Never mix** refactoring with behavior changes. Found a bug → `add` a separate bugfix feature.

## 4. Performance work: measure before, measure after
- Record a baseline (time, memory, query count) with a repeatable command; store the figures in the feature's `notes`.
- The verification command checks a threshold (e.g. a benchmark script that exits 1 if slower than the target).
- Don't optimize anything not measured to be slow.

## 5. Dead code removal / dependency upgrades
- Dead code: prove it is unused (grep, coverage, production logs) before deleting.
- Dependency upgrades: one package per feature, read the changelog, record in `docs/DECISIONS.md`.

## 6. Independent check, then pass-gate, then close
1. Have an independent reviewer — a fresh agent session that never saw your reasoning, or a human (in Claude Code
   retrofitted with `--claude`: the `harness-checker` subagent) — review the diff against
   `docs/harness/evaluator-rubric.md`. Fail → back to step 3.
2. `python scripts/harness.py verify R012` (runs the verification command + the project-wide check). Checker first: `passing` is one-way.
3. Update the module grade in `docs/QUALITY.md` (+ the Change history section).
4. Recurring review issue → add a rule to `.harness/rules.json`.

## Common optimization feature types

| Type | Example verification |
|---|---|
| Split a large file/function | characterization tests + `harness.py size <file> --max-lines N` |
| Remove duplication | characterization tests + a grep rule in rules.json banning the old pattern |
| Architectural boundaries | import rule in rules.json + tests |
| Performance | benchmark script with a threshold |
| Add types | strict type-check for that module |
| Raise test coverage | coverage command with a per-module threshold |
