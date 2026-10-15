@AGENTS.md

# Claude Code-specific notes

- The source of truth is `AGENTS.md` (imported above) — do not copy rules here.
- Independent review before `verify`: use a fresh general-purpose subagent with only the diff and the rubric
  (`docs/harness/evaluator-rubric.md`). This repo does not install the `--claude` guardrails (user decision 2026-10-09).
