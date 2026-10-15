---
paths:
  - "tests/**"
  - "test/**"
  - "**/*test*.*"
  - "**/*spec*.*"
---

# Tests (loaded when you touch test files)

- Never delete, skip, weaken or loosen a test to make a check pass; a red test you cannot fix is recorded in
  PROGRESS.md → Known Issues and the feature is blocked with `python scripts/harness.py block <id> --reason "..."`.
- A new test must be collected by the project's own test command (see `.harness/config.json` → `commands.test`):
  a test that command does not run does not count.
- Done is `python scripts/harness.py verify <id>` passing after an independent review (hk-reviewer), not a test run
  you report yourself.
