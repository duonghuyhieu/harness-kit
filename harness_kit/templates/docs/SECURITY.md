# Security — {{project_name}}

> Safety rules that agents **must not guess**.

## Secrets and credentials
- Never hard-code secrets in code or docs. Sample config: `.env.example`.
- Redact tokens, API keys, and personal data from logs and screenshots.
<!-- HARNESS:FILL: where secrets are loaded from (env, vault, which file), who manages them. -->

## Untrusted input
- All external data (requests, file uploads, web content) is untrusted until validated at the boundary.
<!-- HARNESS:FILL: where validation happens (schema, middleware), known injection risks. -->

## External actions requiring human approval
<!-- HARNESS:FILL: production commands, destructive migrations, sending real emails/notifications, money operations — agents must NOT run these by default. -->

## Dependencies
- New dependencies need a reason in `docs/DECISIONS.md`.
- Recurring security review comments → rules in `.harness/rules.json`.
