# Architecture — {{project_name}}

> Top-level map of the system. Keep it short; per-module details live next to the code
> (`<module>/ARCHITECTURE.md`). Update it **in the same commit** as any boundary change.

## System shape

- Stack: {{stack_summary}}
- Size: {{code_files}} code files, ~{{code_lines}} lines, {{test_files}} test files.
<!-- HARNESS:FILL: runtime surfaces (web / API / CLI / worker / desktop), main user flows, external services (DB, queue, third-party APIs). -->

## Directory layout

```text
{{layout_tree}}
```

## Module map

| Module | Responsibility | Main entry point | Allowed dependencies |
|---|---|---|---|
{{module_rows}}

## Layer model and dependency rules

<!-- HARNESS:FILL: describe the project's actual dependency direction. Suggested default (OpenAI layered domain):
`Types -> Config -> Repo -> Service -> Runtime -> UI` — lower layers never depend on higher ones;
cross-cutting concerns (auth, logging, external API calls) go through explicit providers/adapters. -->

## Machine-enforced boundaries

The most important rules must have automated checks in `.harness/rules.json`
(run by `python scripts/harness.py rules`, included in `check`). Each rule has a WHY and a FIX.

## Hotspots (hard to change safely)

See the Hotspot section of `docs/QUALITY.md`. <!-- HARNESS:FILL: 2–3 areas agents are most likely to break, and why. -->

## When changing the architecture

1. Update this file if the module map or boundaries change.
2. Record the reason in `docs/DECISIONS.md`.
3. If the rule must hold long-term → add a check to `.harness/rules.json`.
