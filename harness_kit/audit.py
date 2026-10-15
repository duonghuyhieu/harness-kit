"""Audit: score how "harness-ready" a project is (read-only, changes nothing).

Five subsystems (lesson 02): Instructions, Tools, Environment, State, Feedback — 5 checks each,
plus the Fresh Session Test (lesson 03) and a clean-state scan (lesson 12).
"""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from . import detect
from .runtime import FILL_MARKER, LAYERS, Harness, load_failures, validate_features


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _check(name: str, ok: bool, fix: str) -> dict:
    return {"name": name, "ok": bool(ok), "fix": fix}


def _entry_file(root: Path, inv: dict) -> Path | None:
    entry = next((root / e for e in ("AGENTS.md", "CLAUDE.md") if (root / e).exists()), None)
    if entry is None and inv["entry_files"]:
        entry = root / inv["entry_files"][0]
    return entry


def _instruction_checks(root: Path, inv: dict) -> list[dict]:
    entry = _entry_file(root, inv)
    text = _read(entry) if entry else ""
    lines = len(text.splitlines())
    verify_re = (r"(?i)(pytest|npm (run )?test|yarn test|pnpm test|go test|cargo test|"
                 r"mvn|gradle|dotnet test|make check|harness\.py check|verify)")
    return [
        _check("Agent instruction file exists (AGENTS.md/CLAUDE.md)", entry is not None,
               "Create a ~100-line AGENTS.md: overview, standard commands, hard constraints, docs map."),
        _check("Instruction file ≤ 200 lines (a router, not an encyclopedia)", entry is not None and lines <= 200,
               f"Split {entry.name if entry else 'the entry file'} ({lines} lines) into a router + docs/<topic>.md."),
        _check("Lists verification commands", re.search(verify_re, text),
               "State the test/lint/type-check/full-check commands explicitly in the instruction file."),
        _check("Points to topic docs in docs/, no unfilled placeholders", "docs/" in text and FILL_MARKER not in text,
               "Add a 'Docs map' section linking docs/<topic>.md + when to read each, and fill in every "
               f"{FILL_MARKER} placeholder (run /hk-init)."),
        _check("Architecture doc exists", bool(inv["architecture_files"]) or any(root.glob("*/**/ARCHITECTURE.md")),
               "Write docs/ARCHITECTURE.md: module map, dependency direction, boundaries."),
    ]


def _tool_checks(root: Path, inv: dict) -> list[dict]:
    cmds = inv["commands"]
    pkg_scripts = detect._read_json(root / "package.json").get("scripts") or {}
    has_check = ((root / ".harness" / "config.json").exists() or "check" in pkg_scripts
                 or re.search(r"^check\s*:", _read(root / "Makefile"), re.M))
    return [
        _check("Setup command detected", cmds["setup"], "Declare the dependency install command."),
        _check("Test command", cmds["test"], "Add a test runner and a test command."),
        _check("Static checks (lint and/or type-check)", cmds["lint"] or cmds["typecheck"],
               "Add a linter (ruff/eslint/golangci/...) or type-checking (mypy/pyright/tsc --noEmit/...)."),
        _check("Agent permissions scoped (least privilege)",
               (root / ".claude" / "settings.json").exists() or (root / ".codex").exists(),
               "Scope the agent's permissions in its own config (allow the verification commands, deny force-push "
               "and reading .env); for Claude Code, `harness retrofit --claude` generates it (opt-in)."),
        _check("Single overall verification command (check)", has_check,
               "Create one entry point that runs all verification (scripts/harness.py check or make check)."),
    ]


def _environment_checks(inv: dict) -> list[dict]:
    stacks = inv["stacks"]
    return [
        _check("Stack detected", stacks, "Add a standard manifest (pyproject.toml, package.json, ...)."),
        _check("Lockfile present", any(s["lockfile"] for s in stacks), "Pin dependency versions with a lockfile."),
        _check("Runtime version pinned", inv["runtime_pins"] or any(s.get("version_hint") for s in stacks),
               "Add .python-version / .nvmrc / engines / toolchain."),
        _check("Init script exists (init.sh/Makefile/...)", inv["init_scripts"],
               "Create init.sh: install → verify baseline → print the run-app command."),
        _check("Reproducible environment (container or .env.example)", inv["containers"] or inv["has_env_example"],
               "Add .env.example (and a Dockerfile/devcontainer if needed)."),
    ]


def _feature_list_status(root: Path, inv: dict) -> tuple[bool, str]:
    missing = "Create feature_list.json: each entry has id, behavior, verification (command), status."
    if not (inv["feature_files"] and inv["feature_files"][0].endswith(".json")):
        return False, missing
    h = Harness(root)
    h.cfg["feature_list"] = inv["feature_files"][0]
    try:
        errs = validate_features(h)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return False, f"feature list is unreadable: {exc}"
    if errs:
        return False, "Fix the feature list: " + "; ".join(errs[:3])
    return True, missing


def _state_checks(root: Path, inv: dict) -> list[dict]:
    progress = root / inv["progress_files"][0] if inv["progress_files"] else None
    progress_text = _read(progress) if progress else ""
    feature_ok, feature_msg = _feature_list_status(root, inv)
    return [
        _check("Uses git", inv["is_git"], "git init — checkpoints and evidence require commits."),
        _check("Progress file exists (PROGRESS.md)", progress is not None,
               "Create PROGRESS.md: Current state / Done / In progress / Issues / Next steps."),
        _check("Progress has 'next steps'",
               re.search(r"(?i)(next step|what's next|next best|b\u01b0\u1edbc ti\u1ebfp theo)", progress_text),
               "Add a Next steps section so the next session knows where to start."),
        _check("Valid feature list (machine-readable)", feature_ok, feature_msg),
        _check("Decision log exists", inv["decision_files"], "Create docs/DECISIONS.md (what, why, when)."),
    ]


def _feedback_checks(root: Path, inv: dict) -> list[dict]:
    arch_checked = ((root / ".harness" / "rules.json").exists() or any(root.glob("scripts/check-arch*"))
                    or any((root / f).exists() for f in (".dependency-cruiser.js", "importlinter.ini", ".importlinter")))
    return [
        _check("Test files exist", inv["test_files"] > 0, "Write tests, starting with characterization tests for hotspots."),
        _check("Test/code file ratio ≥ 10%", inv["code_files"] and inv["test_files"] / max(inv["code_files"], 1) >= 0.10,
               f"Currently {inv['test_files']}/{inv['code_files']} — increase coverage of critical modules."),
        _check("CI exists", inv["ci"], "Run check in CI so evidence does not depend on a personal machine."),
        _check("End-to-end layer exists", inv["commands"]["e2e"], "Add at least one e2e flow for the golden journey."),
        _check("Architecture boundaries machine-checked", arch_checked,
               "Turn architecture rules into automated checks (.harness/rules.json, import-linter, dependency-cruiser)."),
    ]


def subsystem_checks(root: Path, inv: dict) -> dict[str, list[dict]]:
    """The 5 checks of each subsystem (lesson 02). Each builder is independent; order defines the report."""
    return {
        "Instructions": _instruction_checks(root, inv),
        "Tools": _tool_checks(root, inv),
        "Environment": _environment_checks(inv),
        "State": _state_checks(root, inv),
        "Feedback": _feedback_checks(root, inv),
    }


def fresh_session_test(root: Path, inv: dict) -> list[dict]:
    return [
        _check("What is this system?", inv["entry_files"] or inv["has_readme"], "AGENTS.md / README"),
        _check("How is it organized?", inv["architecture_files"], "docs/ARCHITECTURE.md"),
        _check("How do I run it?", inv["init_scripts"] or inv["commands"]["dev"], "init.sh / dev command"),
        _check("How do I verify it?", inv["commands"]["test"], "test/check command"),
        _check("Where are we now?", inv["progress_files"] or inv["feature_files"], "PROGRESS.md / feature_list.json"),
    ]


STALE_PATTERNS = ["*.tmp", "*.bak", "*.swp", "*~", "*.orig", "*.rej"]
SESSION_LEFTOVERS = ["WIP.md", "IN_PROGRESS.md", "scratch.*", "debug.ts", "debug.js", "temp.*", "test-manual.*"]


def cleanliness(root: Path, inv: dict) -> list[dict]:
    issues: list[dict] = []
    todo = 0
    for p in detect.walk_files(root):
        rel = p.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(p.name, g) for g in STALE_PATTERNS):
            issues.append({"severity": "warning", "what": f"temporary file: {rel}"})
        elif any(fnmatch.fnmatch(p.name, g) for g in SESSION_LEFTOVERS):
            issues.append({"severity": "info", "what": f"unfinished session leftover: {rel}"})
        elif p.suffix == ".log" and re.match(r"(src|lib|app)/", rel):
            issues.append({"severity": "warning", "what": f"log in source tree: {rel}"})
        if p.suffix in detect.CODE_EXTS:
            todo += len(re.findall(r"\b(TODO|FIXME|HACK|XXX)\b", _read(p)))
    for d in ("src/dist", "src/build", "lib/dist", "lib/build"):
        if (root / d).is_dir():
            issues.append({"severity": "critical", "what": f"build output in source tree: {d}"})
    if not inv["has_gitignore"]:
        issues.append({"severity": "warning", "what": "missing .gitignore"})
    if inv["is_git"]:
        tracked = detect.git(root, "ls-files", ".env", ".env.local", ".env.production", ".env.staging").split()
        for t in tracked:
            issues.append({"severity": "critical", "what": f"secret file tracked by git: {t}"})
    if todo:
        issues.append({"severity": "info", "what": f"{todo} TODO/FIXME/HACK markers in code (feature list candidates)"})
    return issues[:60]


def effective_commands(root: Path, inv: dict) -> dict:
    """Detected commands, overridden by whatever the project declared in .harness/config.json."""
    cmds = dict(inv["commands"])
    cfg = root / ".harness" / "config.json"
    if cfg.exists():
        try:
            declared = json.loads(cfg.read_text(encoding="utf-8")).get("commands", {})
        except ValueError:
            declared = {}
        cmds.update({k: v for k, v in declared.items() if v})
    return cmds


def run_audit(root: Path) -> dict:
    root = root.resolve()
    inv = detect.inventory(root)
    inv["commands"] = effective_commands(root, inv)
    subs = subsystem_checks(root, inv)
    scores = {k: sum(c["ok"] for c in v) for k, v in subs.items()}
    fst = fresh_session_test(root, inv)
    entry_fills = 0
    for p in [root / "AGENTS.md", *(root / "docs").rglob("*.md")] if (root / "docs").exists() else [root / "AGENTS.md"]:
        entry_fills += _read(p).count(FILL_MARKER)
    # observed failures (lesson 01): real evidence beats the structural score when choosing the bottleneck
    failures = load_failures(Harness(root))
    failure_counts = {layer: sum(1 for f in failures if f.get("layer") == layer) for layer in LAYERS}
    structural = min(scores, key=scores.get)
    observed = max(failure_counts, key=failure_counts.get) if failures else None
    return {
        "project": inv["name"], "root": inv["root"],
        "score": round(sum(scores.values()) / 25 * 100),
        "scores": scores, "checks": subs,
        "bottleneck": observed.capitalize() if observed else structural,
        "structural_bottleneck": structural,
        "failure_counts": failure_counts if failures else {},
        "fresh_session_test": fst,
        "cleanliness": cleanliness(root, inv),
        "unfilled_markers": entry_fills,
        "inventory": {k: inv[k] for k in ("stacks", "commands", "code_files", "code_lines", "test_files",
                                          "hotspots", "modules", "entry_files", "progress_files", "feature_files",
                                          "is_git", "commit_count", "last_commit")},
    }


def to_markdown(r: dict) -> str:
    inv = r["inventory"]
    out = [f"# Harness audit — {r['project']}", "",
           f"**Total score: {r['score']}/100** · bottleneck: **{r['bottleneck']}** · "
           f"{inv['code_files']} code files (~{inv['code_lines']} lines), {inv['test_files']} test files, "
           f"{inv['commit_count']} commits", ""]
    out += ["## Five subsystems", "", "| Subsystem | Score | Bar |", "|---|---|---|"]
    for k, v in r["scores"].items():
        out.append(f"| {k} | {v}/5 | {'█' * v}{'░' * (5 - v)} |")
    out += ["", "## Check details", ""]
    for k, checks in r["checks"].items():
        out.append(f"### {k}")
        for c in checks:
            out.append(f"- [{'x' if c['ok'] else ' '}] {c['name']}" + ("" if c["ok"] else f" — **fix:** {c['fix']}"))
        out.append("")
    if r["failure_counts"]:
        out += ["## Observed failures by layer", "",
                "From `.harness/failures.jsonl` (logged with `scripts/harness.py fail`). This outranks the structural score.", "",
                "| Layer | Failures |", "|---|---|"]
        for layer, n in sorted(r["failure_counts"].items(), key=lambda kv: -kv[1]):
            out.append(f"| {layer} | {n} |")
        out.append("")
    passed = sum(c["ok"] for c in r["fresh_session_test"])
    out += [f"## Fresh Session Test ({passed}/5)", "",
            "Can a brand-new agent session answer these by reading only the repo?", ""]
    for c in r["fresh_session_test"]:
        out.append(f"- [{'x' if c['ok'] else ' '}] {c['name']} _(source: {c['fix']})_")
    out += ["", "## Detected stack and commands", ""]
    for s in inv["stacks"] or []:
        out.append(f"- **{s['language']}** ({s['package_manager']}) at `{s['dir']}`"
                   + (f" — {', '.join(s['frameworks'])}" if s["frameworks"] else ""))
    if not inv["stacks"]:
        out.append("- (not detected)")
    for key, steps in inv["commands"].items():
        if steps:
            out.append(f"  - {key}: " + ", ".join(f"`{s['cmd']}`" + (f" @{s['cwd']}" if s['cwd'] != '.' else "") for s in steps))
    if inv["hotspots"]:
        out += ["", "## Hotspots (lines × (1 + commits in last 12 months))", "",
                "| File | Lines | Commits | Score |", "|---|---|---|---|"]
        for h in inv["hotspots"][:10]:
            out.append(f"| `{h['file']}` | {h['lines']} | {h['commits']} | {h['score']} |")
    if r["cleanliness"]:
        out += ["", "## Clean state", ""]
        for i in r["cleanliness"]:
            out.append(f"- **{i['severity']}**: {i['what']}")
    if r["unfilled_markers"]:
        out += ["", f"_{r['unfilled_markers']} `HARNESS:FILL` placeholders still unfilled in AGENTS.md/docs._"]
    out += ["", "## Next priorities", ""]
    order = sorted(r["scores"], key=r["scores"].get)
    n = 1
    for k in order:
        for c in r["checks"][k]:
            if not c["ok"] and n <= 6:
                out.append(f"{n}. [{k}] {c['fix']}")
                n += 1
    if n == 1:
        out.append("The harness is structurally complete. Measure real impact with before/after agent sessions on representative tasks.")
    out += ["", "> The structural score is an indicator, not causal evidence: confirm the bottleneck with failure logs from real agent sessions."]
    return "\n".join(out) + "\n"


def to_json(r: dict) -> str:
    return json.dumps(r, ensure_ascii=False, indent=2, default=str)
