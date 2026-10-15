---
name: hk-loop
description: Run or design an autonomous loop — goal + machine verification + stop condition, with state on disk. Either the built-in maker → independent reviewer → test gate → pass-gate loop over feature_list.json (`scripts/harness.py loop`), or a metric goal loop (keep a change only if a number improves). Use when the user wants agents to work unattended, "run the backlog", "optimize until X", or types /hk-loop.
argument-hint: "[--features N] [--model M] | goal: <metric>"
---

# Loop engineering

A loop is safe only when **the system, not the agent, decides** whether a step succeeded and when to stop.

## A. Feature loop (built in)
1. Preconditions: the baseline is green (`check`), the features to run have verification commands and `--scope`,
   and the tree has no out-of-scope changes.
2. Always dry-run first and show the user the prompts and limits:
   `python scripts/harness.py loop --dry-run`
3. Run with explicit stop conditions and budget, e.g.
   `python scripts/harness.py loop --max-features 3 --max-attempts 3 --max-total-usd 5 --model sonnet`
   Add `--commit` only if the user pre-approved commits; without it the loop stops after one feature for approval.
4. Afterwards: `python scripts/harness.py report` — the theory checklist (WIP=1, scope, separate maker/reviewer,
   read-only reviewer, pass-gate order, feedback loop, real tests, harness integrity, stop conditions) is computed
   from the run log, not from what the agents claimed. Relay ✔/⚠/✘ lines; investigate every ✘ with `/hk-diagnose`.

## B. Metric goal loop (design)
For "make X faster / smaller / better" with a number:
1. Copy `docs/harness/goal-template.md` and `docs/harness/loop-state-template.md` (if present) and fill:
   the goal, ONE verification command that prints the metric, the direction, a guard command (tests must stay green),
   the scope, the budget (iterations, time, money), and the anchors the loop must not rewrite (fixtures, the metric
   script, the test data).
2. One small change per iteration, measure, keep it only if the metric improves and the guard passes, otherwise revert.
   Log every iteration (iteration, metric, delta, kept, description) in the loop-state file.
3. Stop on: goal reached, budget spent, or N iterations without improvement. Report the curve and the kept changes.

Watch for Goodhart: if the metric improves while the guard or the reviewer disagrees, stop and report — the metric is
being gamed or no longer measures the goal.
