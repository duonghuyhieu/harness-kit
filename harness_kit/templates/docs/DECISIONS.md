# Decision log

> Record **what, why, when**. Newest on top. Do not change an established approach
> without reading the related entry here.
>
> Template:
> ## YYYY-MM-DD: <decision>
> - Reason:
> - Rejected alternatives: (and why)
> - Constraints / consequences:
> - Expires when:

## {{today}}: Apply harness engineering to the project
- Reason: the legacy project lacked agent guidance, standard verification, and cross-session state; agents had to guess.
- Rejected alternatives: one large instruction file (it bloats, and rules in the middle get ignored).
- Constraints: `AGENTS.md` is a short router; standard commands live in `.harness/config.json`; feature status changes only via `scripts/harness.py`.
- Expires when: not applicable.

<!-- HARNESS:FILL: copy important architectural decisions that live outside the repo (Slack, Jira, people's heads) here — e.g. why this framework/DB was chosen, why an "odd" module exists. -->
