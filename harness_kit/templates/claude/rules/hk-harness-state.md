---
paths:
  - "feature_list.json"
  - ".harness/**"
  - "scripts/harness.py"
---

# Harness state (loaded when you touch these files)

- `feature_list.json`: `status`, `evidence`, `attempts` are written only by `python scripts/harness.py`
  (`next`, `verify`, `block`, `unblock`). Once a feature is active or passing, its `verification`, `scope` and `cwd`
  are fixed too. The PreToolUse hook denies hand edits; new features go in with `add`.
- `.harness/` holds runtime state (trace, last check, session, activity, snapshots): never edit it by hand.
  `.harness/config.json` is configuration: change it only when a feature asks for it (H001), and expect an approval
  prompt.
- `scripts/harness.py` is managed by harness-kit: change the kit and re-run the install command instead.
- Source of truth for the rules: `AGENTS.md`.
