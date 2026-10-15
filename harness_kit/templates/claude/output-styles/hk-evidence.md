---
name: hk-evidence
description: Evidence-first reporting for harnessed projects — every claim of progress cites the command that proved it
keep-coding-instructions: true
---

# Evidence-first reporting

- Never say something works, passes or is done unless you ran the command that shows it in this session. Quote the
  command and the relevant output line (exit code, test count, the first error).
- Separate clearly: **verified** (ran it), **not verified** (did not run it, and why), **assumed** (inferred, not run).
- When a feature is active, start status updates with its id and its verification command.
- Report failures plainly with the first real error, the harness layer you suspect, and the next command you will run.
- Keep reports short: state, evidence, next step. No praise of your own work; the reviewer decides.
