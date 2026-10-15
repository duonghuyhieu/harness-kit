---
name: hk-graph
description: Decide whether a workflow needs graph engineering (several agent roles, branching, back-edges, shared state, human approval) instead of a single loop, and if so document it as nodes, edges, shared state and routing rules mapped onto the harness. Use when the user wants multi-agent orchestration, parallel agents, a pipeline of roles, or types /hk-graph.
argument-hint: "<workflow description>"
---

# Graph engineering

A graph is a generalization of a workflow whose nodes may be agents. It costs coordination; use one only when it pays.

## 1. Decide (need ≥ 3 of 5)
| Criterion | Question |
|---|---|
| Separable | Can the work be split into steps with clear inputs/outputs? |
| Branching / back-edges | Are there conditional routes or "go back and fix" edges? (the decisive one) |
| Valuable intermediate state | Is state between steps worth persisting and inspecting? |
| Acceptable per node | Can each node's output be checked by a machine or a rubric? |
| Benefit > coordination cost | Would a single loop clearly be worse? |

Fewer than 3 → recommend one loop (`/hk-loop`) or one feature at a time (`/hk-implement`).

## 2. Design (write `docs/harness/graph.md`)
- **Nodes**: role, model/agent (e.g. hk-planner, hk-maker, hk-reviewer), tools, write permissions (scope), budget.
- **Edges**: condition for each transition, including back-edges with the concrete feedback they carry and a
  maximum number of traversals.
- **Shared state** (design this first): what lives on disk (feature list, contracts, run logs, artifacts), who may
  write each part, and checkpoints at every edge.
- **Routing**: deterministic rules where possible; an LLM router only with a logged decision.
- **Human approval nodes**: before irreversible steps (merge, deploy, data changes).
- **Stop conditions**: success, attempt limits per back-edge, total budget.
- A mermaid diagram of the above.

## 3. Map onto the harness
The built-in loop is already a small graph: maker → reviewer → test gate → pass-gate → (human approval) → commit, with
a back-edge to the maker and "blocked after N attempts". Prefer extending that (features with `depends_on` for
fan-in, separate git worktrees for parallel makers) over a new framework. Every node's output must be verifiable;
parallel makers are limited by the human's review bandwidth — keep the number small.
