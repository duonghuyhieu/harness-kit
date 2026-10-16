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

## 2026-10-15: The kit lives in its own repository (harness-kit), separate from the research
- Reason: user decision — "only the application, like claudekit-main with .claude, not the research". The research
  (knowledge base, paper, reports) stays in `D:/harness-v2`.
- Consequences: this repo holds only what is installed or needed to build and test it.

## 2026-10-15: v2 — the Claude Code layer is the default of `install`, tracked by a manifest
- Reason: user request for a claudekit-like system that installs into `.claude/`. The analysis of ClaudeX
  (all-or-nothing settings copy, no manifest/uninstall, relative hook paths, an `allow` decision used to inject hints,
  hooks writing to fields Claude Code ignores) set the requirements.
- How: `harness install` = retrofit + `.claude/` payload (`harness_kit/templates/claude/`: 6 agents, 16 `hk-*` skills,
  a hook dispatcher, an output style) + semantic settings merge + `<project>/.claude/harness-kit.json` (sha256 per file,
  line endings normalized). `update` replaces unchanged files only; `uninstall` removes unchanged files and the
  harness settings entries (matched by command, plus the permission rules recorded as added).
- Hooks: one dispatcher (`<project>/.claude/hooks/harness_hook.py`) loads `scripts/harness.py` in-process and runs
  `hook-<event>`; fail-open; only Stop may exit 2; PreToolUse answers only deny/ask, never allow. Shell-form commands
  with `${CLAUDE_PROJECT_DIR}` (verified on Claude Code 2.1.287); the status line uses a relative path because it has
  no project-dir variable.
- Verified end to end with `claude -p` (2026-10-15): SessionStart context reached the model; an Edit of a feature's
  `status` was denied under `--permission-mode acceptEdits`.
- Rejected: copying `settings.json` whole (claudekit); one script per hook (process cost, duplicated config);
  publishing as a Claude Code plugin only (the user asked for files under `.claude/`; a plugin can come later).
- `retrofit` stays agent-agnostic (no `.claude/` unless `--claude`), so Codex/Cursor users get the core harness.
- Expires when: Claude Code changes the hook contract (re-check `doctor` and the dispatcher tests).

## 2026-10-10: Loop runs are logged per run, and the theory checklist is computed from the log
- Reason: user question — "how do I know it runs according to the theory?" The trace alone recorded only failures and no
  agent actions.
- How: `stream-json --verbose` transcripts (every tool call, session id, permission mode) in `.harness/runs/<id>/`.
  `events.jsonl` records every step, including passed gates; the hook appends its decisions via `HARNESS_RUN`.
  `report` derives held/enforced/violated per lesson from those facts.
- Rejected: asking the reviewer to self-report compliance (agents overclaim, lesson 09).
- Constraints: run logs are local (git-ignored, they hold code and paths), pruned to `loop_keep_runs` (run-named,
  non-link directories only); excluded from the integrity snapshot because the harness writes them during sessions.
- Learned in the independent review (Fail, then fixed):
  - The log on disk can be rewritten by code the maker wrote, so the report is built from the harness's in-memory
    events, and a changed file is flagged.
  - Refused tool calls (CLI `permission_denials`, or hook errors) are not counted as run. A real run had shown a
    reviewer "running verify" when that call had been refused.
  - A run with no end record is never "held".
  - The feedback row derives rejections from the log and compares full prompts.
  - Re-review: a finished `report.md` could be rewritten by project code run later. The report's sha256 is now anchored
    on the feature in the git-tracked `feature_list.json` (`loop_runs`, last 10), and `report` verifies it. With
    `--commit`, the commit happens after anchoring, so it includes the anchor.
- Allowlist additions from the first logged real run: `harness.py start` (AGENTS.md tells agents to clock in) and
  `cd *` (agents prefix `cd <root> &&`; every other part of a compound command must still be allowed).

## 2026-10-09: The loop drives the `claude` CLI, not the Agent SDK
- Reason: user decision ("no key needed, run the claude CLI"). `claude -p` uses the existing login, and a subprocess keeps
  the runtime stdlib-only (the SDK would be a Python dependency). Verified on this machine (CLI 2.1.280): JSON output,
  `--json-schema`, `--max-budget-usd`, and a `PreToolUse` hook passed via `--settings` that blocks out-of-scope writes.
- Consequences: one `claude -p` session per role and attempt (fresh context = maker/checker separation); guardrails via
  `--settings`, never `.claude/`; no auto-commit by default (human approval node).
- Learned in real runs: prompt rules alone did not stop a reviewer from passing tests the runner never executes →
  mechanical test-collection gate; a reviewer denied a command must not count against the maker → retry once, then stop
  as a `tools` failure.
- Learned in the independent review (Fail, then fixed):
  - A hook is best effort. `acceptEdits` auto-approves `rm`/`mv`/`sed` and wrappers slip past regexes. So sessions use
    `dontAsk`, and the real gate is a post-session integrity snapshot and restore of harness-owned files plus a scope
    check.
  - The restore covers all of `.harness/` and `.claude/` (a maker could otherwise drop the hook config or add local
    settings), and out-of-scope changes are reverted by the harness, since the maker is not allowed to.
  - The test-count baseline is persisted per feature, so a rerun cannot launder an uncollected test. The gate also
    rejects a lower count (deleted tests), and it proves each new test file adds tests: the count is taken again with
    that file hidden.
  - The prompt goes on stdin.
  - There is no `HARNESS_ROOT` env override; the hook gets `--root`.

## 2026-10-09: `--claude` extends an existing `.claude/` instead of proposing
- Reason: user decision — "projects that already have .claude: only add to it, delete nothing, except items with the
  same name as ours".
- How: .claude/settings.json is merged additively (user keys and rules kept, missing harness rules appended, only the
  harness's own `hook-stop` Stop entry replaced); agents/harness-checker.md and `skills/harness-optimize/` are replaced
  by name; everything else in `.claude/` is untouched. Invalid JSON → untouched + proposal. Idempotent.
- Rejected: proposals for `.claude/` items (the user would have to merge by hand every time).
- Constraint: this is the only exception to "retrofit never overwrites a user file", and only with `--claude`.

## 2026-10-09: Claude Code guardrails are opt-in (`retrofit --claude`)
- Reason: user decision. The default harness should be agent-agnostic and not change how Claude Code behaves in a
  project (permissions prompts, a Stop hook at the end of every turn) unless explicitly asked.
- Rejected alternatives: deleting the guardrails entirely (loses a working, tested option); keeping them on by default.
- Consequences: `retrofit` writes no `.claude/` unless `--claude`; review-before-verify is described agent-agnostically
  (fresh session or human); `hook-stop` stays in the runtime; this repo removed its own `.claude/`.
- Expires when: the user asks to turn them back on by default.

## 2026-10-07: Apply harness engineering to the project
- Reason: the legacy project lacked agent guidance, standard verification, and cross-session state; agents had to guess.
- Rejected alternatives: one large instruction file (it bloats, and rules in the middle get ignored).
- Constraints: `AGENTS.md` is a short router; standard commands live in `.harness/config.json`; feature status changes only via `scripts/harness.py`.
- Expires when: not applicable.

## 2026-10-07: All user-facing text in English
- Reason: user decision; skills and templates are consumed by agents across teams.
- Constraint: `runtime.py` keeps a Vietnamese regex alternative only to read older PROGRESS.md files.

## 2026-10-07: PROGRESS.md and `active` status instead of the course's claude-progress.md / in_progress
- Reason: lessons 02/03/05 and `audit-harness.sh` use PROGRESS.md; lessons 07/08 use `active`.
- Rejected: forcing one schema — the runtime reads aliases (`state`, `in_progress`, `done`, `passes`) so course-format lists still work.

## 2026-10-07: `verification` is a runnable command, not a list of manual steps
- Reason: the pass-gate must be executed by the harness, not judged by the agent (lesson 08).

## 2026-10-07: Stop hook does only cheap checks
- Reason: Stop fires at the end of every assistant turn; running tests there would make every turn slow.
- Consequence: tests/build are enforced by `verify` and `exit-check`, not the hook.

## 2026-10-15 — 2.0.1: fixes from three independent reviews (code, end-to-end install, docs)

- Harness hooks are recognized by the dispatcher path after any launcher (`py`, `py -3.12`, `uv run python`…):
  before, `--python py` hooks were duplicated on update and left behind on uninstall, pointing at a deleted dispatcher
  (exit 2 blocks every prompt). Uninstall now keeps the dispatcher while any harness hook is still registered.
- Hook stdin is decoded as UTF-8 from bytes (Windows used the ANSI code page: the guard missed non-ASCII paths).
- The pass-gate is closed against its own inputs: `verification`, `verify_check`, `scope`, `cwd`, `activated_commit`
  of an active or passing feature are locked; duplicate ids are denied; a broken feature list can be repaired.
- The permission allow list no longer covers `harness.py *`: `try`, `verify`, `setup`, `loop` run commands from
  agent-editable files and go through the normal prompt. Edits of the harness configuration are guarded with `ask`.
- `install` never replaces a `scripts/harness.py` that lacks the harness-kit header; re-running `install` on an
  installed project is an `update` (no proposals for identical files, no bogus legacy note, manifest untouched when
  nothing changed, no machine-specific kit path in committed files); postinstall runs only the first time and never
  in CI. install.ps1 runs in a script block and survives a Python probe that writes to stderr.

## 2026-10-15 — 2.1.0: the remaining requirements and review leftovers

- R1: path-scoped rules in `.claude/rules/` (harness state files, tests) load only when those files are touched.
- R5: a failed hook is visible (`systemMessage`), not only on stderr; `"guard_failure": "closed"` is the opt-in
  fail-closed guard (the dispatcher writes the deny itself, so it does not depend on `onFailure`, 2.1.295+).
- R9: `--diff` prints a unified diff of every existing file a plan changes. Updates keep entries the user removed
  (rules, hooks, status line: recorded as `settings_offered` in the manifest), retire rules an older version added
  (2.0.0's `Bash(python scripts/harness.py *)`), and `--force` restores everything. Uninstall removes the `$schema`
  key the harness added.
- R11: the Stop hook blocks once when the active feature has files newer than its last recorded verification
  (`try` now leaves a trace record).
- R13: every injected context (SessionStart, UserPromptSubmit) and every guard decision is traced with the session
  id and a content hash; the trace rotates; `trace --grep`.
- R16: state files are written atomically (temporary file + rename, retried on Windows sharing errors).
- R18: every key the runtime reads has a default; `validate` checks unknown keys, types and enum values; a test pins
  that the code reads no key without a default. Per-hook switches (`hooks_off`); turning off `guard`/`stop` is logged.
- R21: `doctor` compares the Claude Code version with the one the hooks were verified on (2.1.287) and names the
  `onFailure` minimum (2.1.295).
- Instruction files (AGENTS.md, CLAUDE.md, `.claude/agents|skills|rules|output-styles/`) ask before an edit
  (`instruction_guard`): instructions an agent writes are input to review. `python -c`/`node -e` one-liners that
  write a state file are denied (heuristic).
- PowerShell twins of every Bash rule: on Windows with Git Bash, a Bash deny rule alone turns the PowerShell tool off.
- Kept by design: `next` while a feature is active prints it and exits 0 (an idempotent "what am I on"); the status
  line keeps its relative path (Claude Code documents no project-dir variable for it; from a subdirectory it shows
  an error instead of the harness line — listed in Known Issues).


## 2026-10-15 — 2.1.1: version check at install, CI, .git protected

- R21: the install message warns when Claude Code is older than the version the hooks were verified on (2.1.287).
- R19: GitHub Actions runs the unit tests on Ubuntu, Windows and macOS with Python 3.10 and 3.12.
- R3: `Edit(./.git/**)` is denied: git internals change only through git.

## 2026-10-15 — 2.1.2: Python 3.10 reads TOML again

- Python 3.10 has no `tomllib`: the kit silently skipped `pyproject.toml` / `Cargo.toml`, so detection was weaker
  than on 3.11+ although 3.10 is the documented minimum. Found by the new CI (Ubuntu, Python 3.10). A small
  stdlib TOML reader is the fallback; a test pins it against `tomllib` on pyproject, Cargo and toolchain files.

## 2026-10-16 — 2.1.3: a red baseline from future features is explained, not just failed

- Found in experiment 2 (multi-session): tests written up front for later features kept the project check red, so
  `verify` could never pass the first feature, and WIP=1 plus the scope guard forbade fixing it inside that feature;
  the agent blocked, forced and unblocked features in a loop. `verify` now says when its own verification passed,
  which product features have not started, and what to do (pending tests or leave them out of `commands.test`).

## 2026-10-16 — 2.1.4: gates that reach agents who skip the workflow; reviewers that can run the check

- Experiment 2 with sonnet: in 3 of 4 harness runs the agent never ran `next`/`verify`; the next session found the
  feature still not_started and redid it, so part 3 was never started. The Stop hook now blocks once when files in a
  not-started feature's scope changed. Re-run with 2.1.4: the agent used the workflow in 4/4 runs (features passing
  0.75/3 → 2.75/3).
- Experiment 4 (`harness.py loop`): reviewers were denied the active feature's own verification command and returned
  FAIL. The loop now allows exactly that command (and its flag variants) for both roles.

## 2026-10-16 — 2.1.5: scope checks ignore generated files; agents are told not to prefix `cd`

- Experiment 4, batch 2: the loop committed `__pycache__` (the task repo had no .gitignore); the next test run changed
  the .pyc and `_loop_drift` blocked two correct features as "changed files outside the scope", which stopped the
  dependent features. Bytecode, `.class` and files under SKIP_DIRS are now never scope drift.
- A reviewer prefixed its commands with `cd /d "…" &&` and was denied: both prompts now say they run in the project root.

## 2026-10-16 — 2.2.0: gate integrity (V014–V017)

Source: a survey of 17 open-source harness repositories (research repo, `reports/`), and the pre-registered
baseline-vs-harness replication E2 (haiku, n = 20 per arm).
- V014: `add`, `verify` and `validate` refuse a verification command that cannot fail (only prints / cd / exit 0,
  `|| true`, a trailing `; exit 0`, a pipe into tail/tee without pipefail). Deterministic patterns only — a guardrail,
  not a proof (`python -c "assert True"` still passes). `trivial_verification: warn` for projects that need it.
  Test fixtures that used `python -c pass` as a verification now use a real assertion.
- V015: the guard denies hook bypasses in every configuration (switching checks off is the user's action) and asks
  before edits that weaken the checker. Edits are judged by their changed lines (sections of pyproject/setup.cfg,
  script keys of package.json, assertion and skip counts in test files), so ordinary edits of those files pass.
- V016: `verify` records a content hash of the scope files; status/SessionStart/exit-check list stale passing
  features. A content hash, not a commit, because the feature's own commit lands after `verify`. Projects with broad,
  shared scopes (this repo) see many stale features after any change: that is accurate, `check --full` clears it.
- V017: E2 found the Stop gate never fired in 20/20 harness runs: `Harness.git()` strips its output, so the first
  porcelain line (" M" + path) lost its leading space and `[3:]` cut the first letter of the path — outside the
  scope — and the one-file change of every run was
  missed. The unit tests never had an unstaged modification as the first line. `dirty_paths()` reads porcelain
  unstripped, lists untracked files one by one, and skips generated files. Lesson: a gate needs an end-to-end test
  through the real CLI, not only unit tests of its parts.

## 2026-10-16 — 2.2.1: SessionStart names the next action instead of asking for a clock-in (V018)

- Experiment 5b (harness-v2 session "harness-v2-e3", 8-turn sessions): told "clock in with `start`", harness agents
  re-ran `start` and re-read PROGRESS.md / feature_list.json every session — 26% of their tool calls (as measured by
  the ceremony script in the harness-v2 research repo, experiments/long-task; an earlier ad-hoc count said 31%) — edited the code 4× less and
  scored 26.9% vs 52.7% hidden tests (Mann–Whitney p = 0.02; retracted, see below). The injected block already is the clock-in:
  it now says so and names one next action (`next_action()`: continue the active feature, or `next --id <id>`). The
  AGENTS.md template says steps 1–2 are done when the hook injected the state. Patch written by that session, applied
  here (its test now uses a non-trivial verification, which 2.2.0 requires).
- **Correction (same day):** the 5b *scores* did not replicate. 5b's harness arm had installed an in-progress working
  tree of the kit; experiment 5c on frozen kits (n = 4 per arm, 10 × 8-turn sessions) found no detectable difference
  in correctness (plain 45.2%, 2.2.0 47.6%, 2.2.0 + this change 50.0%; Mann–Whitney p ≈ 0.77), and the 5b scores are
  retracted as an artefact. What 5c did measure: clock-in ceremony fell from 24% to 16% of tool calls and code edits
  per session rose from 0.42 to 0.60 (plain: 0.53). The change is kept for that reason — less wasted work in short
  sessions — not for a measured gain in correctness.

## 2026-10-16 — 2.3.0: review gate, incident ledger, handoff gate (V019–V021)

- Evidence that motivates gates over prose: the pre-registered E2b + E2c batches (harness-v2, haiku, n ≈ 40 per arm)
  found kit 2.2.0 better than a plain repository on the primary outcome (fully correct +30% [+8%, +48%]), after E2
  with the broken 2.1.5 Stop gate found no difference. One task, one model; the mechanism is not identified.
- V019: the review gate reads verdicts from **SubagentStop**, not PostToolUse. A probe on Claude Code 2.1.287 showed
  that a subagent the model starts in the background returns only `isAsync`/`agentId` to PostToolUse; the reviewer's
  final text arrives in `SubagentStop.last_assistant_message` with `agent_type`. The fingerprint hashes the content of
  every file changed since activation (not a commit), so a commit between review and verify does not void the review,
  but any edit does. Default `warn` in DEFAULT_CONFIG (projects updated from an older kit may not have the hook yet);
  Claude Code installs write `require`. The loop records its own reviewer's pass.
- V020: incidents (no-review, an exhausted Stop gate, a session ending unverified or without a handoff) are shown once,
  at the next session start (harmonist's "PROTOCOL-EXHAUSTED" banner, survey round 3).
- V021: the handoff gate uses PROGRESS.md's mtime against the session start, not a session stamp in the file —
  simpler, and the existing exit-check already relies on it. Rationalization detection only warns
  (`systemMessage`), because phrases like "pre-existing issue" are sometimes true.

## 2026-10-16 — 2.3.1: fixes from an independent review of 2.2–2.3 (V022)

An independent, read-only reviewer (a fresh agent that did not write the code) reviewed 9627446..HEAD and returned
`VERDICT: REJECT` with one critical and five major findings, most reproduced. Every reproduced finding is now a
regression test in `ReviewFixTests`; all 12 failed on 2.3.0 before the fix.
- Critical: the agent could forge an APPROVE by piping a payload into `scripts/harness.py hook-subagent-stop`. The
  guard now denies running hook commands by hand, and SubagentStop records an APPROVE only when the payload carries an
  existing subagent transcript (Claude Code always sends one). Lesson: a hook command is an API, and the agent can
  reach it through Bash unless the guard says otherwise.
- A background review could approve content edited while it ran: an `hk-reviewer` launch (PreToolUse on the Agent
  tool) records the fingerprint the review starts from; an APPROVE of different content becomes UNCERTAIN. A
  reviewer prompt that dictates the verdict is refused.
- State files are protected by basename after `cd .harness`, through `.harness/./x`, heredoc scripts and .NET
  `[IO.File]` writes. Bypass detection: `--no-veri…` prefixes, line continuations, variables, aliases, `sh -lc`,
  `eval`, `pwsh -NoProfile -Command`, PowerShell backticks, per-segment hooksPath reads, `HUSKY=0`, `SKIP=`, writes in
  `.git/hooks`. Narrowed: disableAllHooks only in a settings file, CLAUDE_CONFIG_DIR only when assigned, package.json
  only for test/lint scripts and checker keys (not dependency versions).
- `trivial_verification`: `CI=true npm test` is a real check (the env-assignment pattern was unanchored); `; echo …`,
  `& exit 0`, `( … || true)` and `sh -c '…; exit 0'` now count as masking. The 2.2.0 test that called `pytest; echo
  done` valid was wrong and is corrected.
- A configured `scope_guard: deny` is no longer weakened to ask by another concern. The review fingerprint follows the
  feature's scope (dot-folders included), ignores test artefacts, and uses the empty tree when a feature has no base
  commit. The handoff gate counts only commits made during the session. `dirty_paths` uses `-z` (non-ASCII paths). A
  truncated trace line no longer drops the SessionStart injection. Fewer warnings ("should work" removed) and one
  exhausted-gate incident per session and problem set.
- Not fixed, documented: the maker still writes the reviewer's prompt (only an explicit verdict in it is refused); the
  shell guard remains a guardrail, not a parser (the gates are `verify` and the review record).

**Review round 2** (same reviewer, 584b061): `VERDICT: REJECT` — 13 of 15 findings fixed, but the forge was still
possible (`python -c "…harness.main(['hook-subagent-stop'])"` plus any existing file as "transcript"), two overlapping
reviewers were paired wrongly, and the new scope filter ignored tracked changes outside the scope (an assertion removed
from a test after the approval). Fixes, each with a regression test in `ReviewRound2Tests` (4 tests, all failing on
584b061):
- **Verdicts are authenticated, not only commands blocked.** A second probe (Claude Code 2.1.287) showed that at
  SubagentStop the subagent's transcript already holds its first user message — exactly the Agent tool's `prompt` —
  and its final message. An APPROVE now counts only if the transcript lies in Claude Code's projects folder
  (`~/.claude/projects`, or `$CLAUDE_CONFIG_DIR/projects`), its last message carries the same verdict, and its prompt
  matches a recorded hk-reviewer launch for the feature that no earlier verdict used (oldest first; every verdict uses
  up its launch; launches expire after 6 h). The guard denies agent writes into that folder, any command that names a
  hook subcommand, and executing the dispatcher with an event (reading or staging it is fine).
- Every changed tracked file counts in the review fingerprint again; only untracked files are limited to the scope.
- Fewer false positives: `--no-verify` only on git subcommands that run hooks (or through a variable used by one),
  copying *out of* `.git/hooks`, `rename` as an argument, scripts that only read state files, reviewer prompts that
  offer APPROVE among other verdicts, a trailing `echo` after `set -e`.
- **Threat model, stated plainly:** the review record is evidence against *skipping* a review or approving lazily. It
  is not a security boundary against an agent that writes code to defeat it — an agent that can run arbitrary code on
  the machine can forge any local evidence. The gates make that a deliberate, multi-step act, and the incident ledger
  and trace make it visible; the boundary for adversarial agents is the OS sandbox and the human review of the diff.

**Review round 3** (fa05e40): `VERDICT: REJECT` on one major issue — with identical prompts (the normal case: the
hk-review skill passes only the feature id), "oldest unused launch first" paired a reviewer that finished early with
the other reviewer's launch, so an APPROVE of older content could cover the current content. Within the threat model
the reviewer found no cheap single-step way to pass `verify` without a real review; outside it, a one-line
`python -c` call of `record_review` could write an APPROVE. V024, with `ReviewRound3Tests` (8 tests; 6 fail on
fa05e40, the other 2 pin the finish order the old pairing already handled):
- A launch is paired with its transcript **by time**: the transcript's first message carries a timestamp (a third probe
  on Claude Code 2.1.287 confirmed it, and that a project CLAUDE.md arrives as `attachment` entries, not before the
  prompt), so its own launch is the last matching one recorded before it began. Any finish order works, an abandoned
  launch (denied, crashed) is never chosen, and a reviewer continued with SendMessage (its launch already used) is
  reported as such. A verdict is recorded under the fingerprint of the content its review *started* on, so a late
  verdict of an older review neither covers nor overwrites the verdict on the current content.
- `review_status` counts an APPROVE only if it names a recorded launch (hook) or an existing run folder (loop); the
  guard denies `-c`/heredoc/stdin scripts that import the runtime.
- `isMeta` entries are skipped and block content is accepted when matching the prompt; quoted prose is no longer
  scanned for hook names outside scripts; Get-Content is read-only; a variable holding a state path counts only when
  the script writes through it; only a top-level `set -e` exempts a trailing `echo` (not `$ErrorActionPreference`).
- The hk-review skill says to launch a new reviewer after fixes, never to continue one with SendMessage.

**Review round 4** (8890ff1): **`VERDICT: APPROVE`** — no critical or major issue within the threat model; both finish
orders, abandoned launches, continued reviewers and the one-line forge confirmed fixed. Minor findings, fixed in V025
with `ReviewRound4Tests` (5 tests, all failing on 8890ff1):
- A continued reviewer's verdict (SendMessage follow-up) is recorded for the log only and can neither count nor cancel
  an approval; the status message points to a fresh launch.
- A transcript without timestamps pairs only when exactly one unused launch matches (picking the latest re-opened the
  overlap race); two identical launches in one turn on the same content are paired with each other, not reported as
  "continued".
- An APPROVE counts only if its launch is for the same feature, carries the same fingerprint, and is named by no other
  verdict.
- The runtime-import rule reads only the `-c` payload or heredoc body, and needs both an import of the runtime and a
  call that writes its records — `python -c "import harness_kit"` and `python -m pytest -c pytest.ini` (this repo's own
  development) are fine; an obfuscated `__import__('har'+'ness')` that calls `record_review` is not.
- **Upgrade note:** an APPROVE recorded by 2.3.0 (no launch record) no longer counts: a feature that was active during
  the upgrade needs one fresh hk-reviewer run; `verify` says so.

**Review round 5** (e114450, confirmation): **`VERDICT: APPROVE`** — five of six round-4 findings confirmed fixed; the
0.05 s slack is unchanged and safe as long as Claude Code stamps the prompt after PreToolUse returns, as the probe
showed. V026 adopts its conservative suggestion: a continued reviewer can never approve, but its REJECT still counts;
and the no-timestamp message says whether several or no unused launches matched.

Still out of the model, recorded for honesty: a determined agent can forge evidence with code the guard does not
recognise (the reviewer showed obfuscation that would also need a forged launch record); the boundary for that is the
OS sandbox and a human reading the diff.
