---
name: hk-plan
description: Turn a request into verifiable features in feature_list.json — each with an observable behavior, a runnable verification command, scope globs and dependencies — plus a sprint contract for larger work. Use when the user asks for a new feature, a change spanning several steps, a vague task ("add search"), or types /hk-plan.
argument-hint: "<request>"
---

# Plan as verifiable features

A task the agent has to guess about is a harness failure (instructions layer). Planning makes "done" checkable.

1. Read `AGENTS.md`, the docs-map entries the request touches, and `python scripts/harness.py status`.
2. For anything bigger than one session, or touching several modules, delegate exploration to the **hk-planner**
   subagent with the request; otherwise plan directly.
3. Each feature must have:
   - **behavior**: one observable outcome ("GET /api/search?q=x returns 20 results per page with highlighted snippets")
   - **verification**: one shell command that exits 0 only when that behavior works — prefer a test file written for
     the feature, or an end-to-end script for cross-component behavior; never "manually check"
   - **scope** globs (files it may change) and **out_of_scope**
   - **depends_on** and a **priority** when order matters; `--verify-check full` for cross-component work
4. Show the user the feature table and ask for confirmation of anything you assumed. Then register them:
   `python scripts/harness.py add --id <ID> --behavior "<...>" --verify "<cmd>" --scope <globs> --out-of-scope "<...>" [--depends <ids>] [--priority N]`
5. Larger work: copy `docs/harness/sprint-contract.md` into `docs/exec-plans/active/<name>.md` (or next to the
   feature) and fill goal, done criteria, verification, out of scope, risks.
6. `python scripts/harness.py validate` must pass.

Rules: one behavior per feature, sized for one session; no feature without a runnable verification command;
features are added with `add` — never by writing `status`/`evidence` by hand.
