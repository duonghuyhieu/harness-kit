---
name: hk-verify
description: Run the harness pass-gate for a feature — confirm an independent review passed and the scope is clean, then `scripts/harness.py verify <id>`, which runs the verification command plus the project check and records evidence. Use when work on a feature looks done, or the user says "mark it done", "verify", or types /hk-verify.
argument-hint: "<feature-id>"
---

# Pass-gate

`passing` is one-way and means "proved by a command", not "I believe it works".

1. Pre-checks:
   - The feature is `active` (`python scripts/harness.py status`).
   - An **hk-reviewer** verdict of PASS exists for the current diff. If not, run `/hk-review` first.
   - `git status --short`: nothing outside the feature's scope (the gate reports drift; `scope_drift: error` blocks).
2. `python scripts/harness.py verify <ID>`.
3. PASS → show the recorded evidence (command, exit code, commit, project check) and suggest the commit message.
   FAIL → the feature stays active and the attempt is counted; show the first real error and go back to the work.
   Never edit `status` or `evidence` in `feature_list.json` yourself — a hook denies it and `validate` flags it.
4. After a pass: `python scripts/harness.py handoff`, then the next feature or `/hk-handoff`.
