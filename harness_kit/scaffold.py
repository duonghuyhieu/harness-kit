"""Retrofit: generate the harness file set for an existing project.

Safety principles:
- Never overwrite an existing file. On conflict, the proposed version is written to
  `.harness/proposals/<path>` for a human/agent to merge by hand.
- Never run project commands. Only `git init`/`git commit` when explicitly requested by flag.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import detect
from .runtime import DEFAULT_CONFIG, VERSION

TEMPLATES = Path(__file__).parent / "templates"
H = "python scripts/harness.py"

LEVELS = {
    "minimal": [
        ("AGENTS.md", "AGENTS.md"), ("CLAUDE.md", "CLAUDE.md"), ("PROGRESS.md", "PROGRESS.md"),
        ("init.sh", "init.sh"), ("init.ps1", "init.ps1"),
        ("docs/DECISIONS.md", "docs/DECISIONS.md"),  # the "why" log is core at every level (lecture 05)
    ],
    "standard": [
        ("docs/ARCHITECTURE.md", "docs/ARCHITECTURE.md"), ("docs/PRODUCT.md", "docs/PRODUCT.md"),
        ("docs/QUALITY.md", "docs/QUALITY.md"),
        ("docs/RELIABILITY.md", "docs/RELIABILITY.md"),
        ("docs/harness/clean-state-checklist.md", "docs/harness/clean-state-checklist.md"),
        ("docs/harness/session-handoff.md", "docs/harness/session-handoff.md"),
        ("docs/harness/evaluator-rubric.md", "docs/harness/evaluator-rubric.md"),
        ("docs/harness/sprint-contract.md", "docs/harness/sprint-contract.md"),
        ("docs/harness/legacy-refactor-playbook.md", "docs/harness/legacy-refactor-playbook.md"),
    ],
    "full": [
        ("docs/SECURITY.md", "docs/SECURITY.md"),
        ("docs/harness/goal-template.md", "docs/harness/goal-template.md"),
        ("docs/harness/loop-state-template.md", "docs/harness/loop-state-template.md"),
        ("docs/exec-plans/active/index.md", "docs/exec-plans/active/index.md"),
        ("docs/exec-plans/completed/index.md", "docs/exec-plans/completed/index.md"),
        ("docs/exec-plans/tech-debt-tracker.md", "docs/exec-plans/tech-debt-tracker.md"),
    ],
}
LEVEL_ORDER = ["minimal", "standard", "full"]

# The Claude Code layer is opt-in for `retrofit` (`--claude`) and the default for `harness install`: the core harness
# stays agent-agnostic. Everything under templates/claude/ is installed into .claude/ (v2: agents, skills, hooks,
# output style), tracked by a manifest with hashes so `update`/`uninstall` never touch files the user changed.
CLAUDE_PAYLOAD = TEMPLATES / "claude"
CLAUDE_DOCS = [
    # docs the agents and skills point to, so the Claude layer also works at the minimal level
    ("docs/harness/evaluator-rubric.md", "docs/harness/evaluator-rubric.md"),
    ("docs/harness/legacy-refactor-playbook.md", "docs/harness/legacy-refactor-playbook.md"),
    ("docs/harness/sprint-contract.md", "docs/harness/sprint-contract.md"),
    ("docs/harness/session-handoff.md", "docs/harness/session-handoff.md"),
    ("docs/harness/goal-template.md", "docs/harness/goal-template.md"),
    ("docs/harness/loop-state-template.md", "docs/harness/loop-state-template.md"),
    ("docs/QUALITY.md", "docs/QUALITY.md"),
]
CLAUDE_NOTES = (
    "- Slash commands (skills in `.claude/skills/`): `/hk-start` to clock in, `/hk-plan` to turn a request into\n"
    "  verifiable features, `/hk-implement` for one feature end to end, `/hk-review` and `/hk-verify` before anything\n"
    "  counts as done, `/hk-handoff` to clock out. `/hk-help` lists the rest.\n"
    "- Subagents (`.claude/agents/`): `hk-reviewer` is the independent, read-only checker — the maker never grades its\n"
    "  own work. Also `hk-planner`, `hk-maker`, `hk-debugger`, `hk-docs-keeper`, `hk-initializer`.\n"
    "- Hooks (`.claude/settings.json` → `.claude/hooks/harness_hook.py` → `scripts/harness.py`): the harness state is\n"
    "  injected at session start, hand edits of feature status are denied, writes outside the active feature's scope\n"
    "  ask first, and Stop blocks finishing with debug code or junk. `HARNESS_HOOKS=off` disables them for a session.\n"
)
MANIFEST = ".claude/harness-kit.json"
HOOK_EVENTS = [  # (event, matcher or None, dispatcher argument)
    ("SessionStart", "startup|resume|clear|compact", "session-start"),
    ("UserPromptSubmit", None, "prompt"),
    ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell", "guard"),
    ("PostToolUse", "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell", "activity"),
    ("PreCompact", None, "precompact"),
    ("Stop", None, "stop"),
    ("SessionEnd", None, "session-end"),
]
# v1 `--claude` items, removed by v2 when unchanged (sha256 of the v1 templates); replaced by hk-reviewer / hk-optimize
LEGACY_ITEMS = {
    ".claude/agents/harness-checker.md": "3fec520d6d03e47fa4b753d1bc48250689ca53dbf885d3785bcee8f924384983",
    ".claude/skills/harness-optimize/SKILL.md": "21976a6a542aaf4002d7c5118fc7e3d480f8acb88dadd3c66b24f28e81fe2b17",
}
KIT_ROOT = Path(__file__).resolve().parent.parent

COMMAND_LABELS = [("dev", "Run app (dev)"), ("test", "Test"), ("lint", "Lint"), ("typecheck", "Type-check"),
                  ("build", "Build"), ("e2e", "End-to-end")]


# ----------------------------------------------------------------- context ---

def _fmt_steps(steps: list[dict]) -> str:
    return "<br>".join(f"`{s['cmd']}`" + (f" (in `{s['cwd']}/`)" if s["cwd"] != "." else "") for s in steps)


def _layout_tree(root: Path) -> str:
    lines = []
    for child in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name in detect.SKIP_DIRS or (child.name.startswith(".") and child.name not in (".github",)):
            continue
        if child.is_dir():
            subs = [c.name + ("/" if c.is_dir() else "") for c in sorted(child.iterdir())
                    if c.name not in detect.SKIP_DIRS and not c.name.startswith(".")][:8]
            more = " …" if len(list(child.iterdir())) > 8 else ""
            lines.append(f"{child.name}/  " + (", ".join(subs) + more if subs else ""))
        else:
            lines.append(child.name)
    return "\n".join(lines[:40]) or "(empty)"


def build_context(inv: dict, level: str) -> dict:
    root = Path(inv["root"])
    today = _dt.date.today().isoformat()
    stacks = inv["stacks"]
    if stacks:
        parts = []
        for s in stacks:
            fw = f", {', '.join(s['frameworks'][:5])}" if s["frameworks"] else ""
            ver = f" {s['version_hint']}" if s.get("version_hint") else ""
            where = f" at `{s['dir']}/`" if s["dir"] != "." else ""
            parts.append(f"{s['language']}{ver} ({s['package_manager']}{fw}){where}")
        stack_summary = "; ".join(parts)
    else:
        stack_summary = "not detected <!-- HARNESS:FILL: language, version, frameworks -->"
    cmds = inv["commands"]
    rows = []
    for key, label in COMMAND_LABELS:
        if cmds.get(key):
            rows.append(f"| {label} | {_fmt_steps(cmds[key])} |")
        elif key in ("test", "dev"):
            rows.append(f"| {label} | _not configured — declare it in `.harness/config.json`_ |")
    modules = inv["modules"] or inv["source_dirs"]
    module_rows = "\n".join(f"| `{m}/` | ? | ? | ? |" for m in modules) or "| ? | ? | ? | ? |"
    module_rows += ("\n\n<!-- HARNESS:FILL: fill in the Responsibility / Entry point / Allowed dependencies columns "
                    "for each module (replace the ?), and add any missing modules. -->")
    quality_rows = "\n".join(f"| `{m}/` | - | - | - | - | - | - | - |" for m in modules) or "| - | - | - | - | - | - | - | - |"
    hotspot_rows = "\n".join(f"| `{h['file']}` | {h['lines']} | {h['commits']} | {h['score']} |"
                             for h in inv["hotspots"][:10]) or "| (no files ≥150 lines) | | | |"
    largest_rows = "\n".join(f"| `{f['file']}` | {f['lines']} |" for f in inv["largest_files"][:10]) or "| - | - |"
    rel_rows = []
    if cmds.get("dev"):
        rel_rows.append(f"| Run app | {_fmt_steps(cmds['dev'])} |")
    rel_rows.append("| Smoke (quick startup) | declare `commands.smoke` in `.harness/config.json` |")
    return {
        "project_name": inv["name"], "today": today, "h": H, "stack_summary": stack_summary,
        "command_rows": "\n".join(rows),
        "code_files": str(inv["code_files"]), "code_lines": f"{inv['code_lines']:,}",
        "test_files": str(inv["test_files"]), "layout_tree": _layout_tree(root),
        "module_rows": module_rows, "quality_rows": quality_rows, "hotspot_rows": hotspot_rows,
        "largest_rows": largest_rows, "reliability_rows": "\n".join(rel_rows),
    }


def render(text: str, ctx: dict) -> str:
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", v)
    return text


# ----------------------------------------------------------- generated JSON ---

def make_config(inv: dict, progress_file: str, feature_file: str) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["project"] = inv["name"]
    cfg["progress_file"] = progress_file
    cfg["feature_list"] = feature_file
    for key, steps in inv["commands"].items():
        cfg["commands"][key] = steps
    cfg["harness_version"] = VERSION
    return cfg


def make_features(inv: dict, legacy_entry: list[str]) -> dict:
    feats = [
        {"id": "H001", "priority": 1, "category": "harness",
         "behavior": "Verification baseline runs from a clean checkout: `./init.sh` completes setup and `check` passes "
                     "(fix the commands in .harness/config.json if detection was wrong).",
         "verification": f"{H} check", "status": "not_started", "depends_on": [], "evidence": None},
        {"id": "H002", "priority": 2, "category": "harness",
         "behavior": "The project has a real test command (commands.test) and at least one passing test.",
         "verification": f"{H} check --only test", "status": "not_started", "depends_on": ["H001"], "evidence": None},
        {"id": "H003", "priority": 3, "category": "harness",
         "behavior": "A smoke check covers the startup path (commands.smoke: import app / build / call health) and passes.",
         "verification": f"{H} check --only smoke", "status": "not_started", "depends_on": ["H001"], "evidence": None},
        {"id": "H004", "priority": 4, "category": "harness",
         "behavior": "Project knowledge lives in the repo: AGENTS.md and docs/ are fully filled in (no HARNESS:FILL left, "
                     "no broken links, AGENTS.md ≤ 200 lines).",
         "verification": f"{H} docs --strict", "status": "not_started", "depends_on": [], "evidence": None},
    ]
    if legacy_entry:
        feats.append({
            "id": "H005", "priority": 5, "category": "harness",
            "behavior": f"Merge legacy instructions ({', '.join(legacy_entry)}) into AGENTS.md (short router) + "
                        f"docs/<topic>.md; see the proposals in .harness/proposals/.",
            "verification": f"{H} docs --strict", "status": "not_started", "depends_on": [], "evidence": None})
    feats.append({
        "id": "H006", "priority": 6, "category": "harness",
        "behavior": "Optimization backlog: grade modules A–D (in docs/QUALITY.md when the level generates it) and create "
                    "at least 3 `refactor` features with measurable verification commands for low-scoring hotspots/modules.",
        "verification": f"{H} validate --require refactor:3", "status": "not_started", "depends_on": ["H004"],
        "evidence": None})
    return {
        "project": inv["name"],
        "last_updated": _dt.date.today().isoformat(),
        "rules": {"single_active_feature": True, "passing_requires_evidence": True,
                  "status_changes_only_via": f"{H} next|verify|block|unblock"},
        "status_legend": {"not_started": "Not started.", "active": "In progress (at most 1).",
                          "blocked": "Blocked, with a recorded reason.", "passing": "Verification passed, with evidence."},
        "features": feats,
    }


CODE_GLOBS = ["*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.java", "*.kt", "*.go", "*.cs", "*.php", "*.rb", "*.rs"]


def make_rules() -> dict:
    return {
        "_doc": "Machine-checkable architecture rules. include/exclude use fnmatch on relative POSIX paths. "
                "severity=error fails check; warn only warns. Recurring review findings -> add a rule here.",
        "rules": [
            {"id": "R001", "description": "Hard-coded secret in code", "severity": "warn",
             "include": CODE_GLOBS, "exclude": ["*test*", "*spec*", "*example*", "*fixture*"],
             "pattern": r"(?i)\b(api[_-]?key|secret|passwd|password|access[_-]?token)\b\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{12,}['\"]",
             "why": "Secrets in code leak through git, logs and build artifacts.",
             "fix": "Read from environment variables / a secret manager; add the key (without a value) to .env.example."},
            {"id": "R900", "description": "EXAMPLE layer boundary: UI must not access the DB directly", "severity": "error",
             "disabled": True, "include": ["src/ui/*", "src/components/*"], "exclude": [],
             "pattern": r"^\s*(import|from)\s+.*\b(db|database|repository|sqlalchemy|prisma)\b",
             "why": "UI must go through a service; direct DB access breaks the layer boundary and is hard to test.",
             "fix": "Move the query into a service/repository and call it through the service."},
        ],
    }


HARNESS_EOL = {"init.sh": "lf", "scripts/harness.py": "lf", "init.ps1": "crlf"}
GITATTRIBUTES = """# harness-kit: pin line endings of harness-owned scripts so they run regardless of core.autocrlf
init.sh text eol=lf
scripts/harness.py text eol=lf
init.ps1 text eol=crlf
"""


def proposal_name(rel: str) -> str:
    """Path under .harness/proposals/. A proposed .gitattributes must not be live (git would apply it to that subtree)."""
    return "gitattributes.proposed" if rel == ".gitattributes" else rel


def _attr_pattern_matches(pattern: str, path: str) -> bool:
    """gitattributes semantics: a pattern without '/' matches the basename at any depth; '**/' also matches the root."""
    if pattern.startswith("**/"):
        pattern = pattern[3:]
    if "/" not in pattern.rstrip("/"):
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)
    return fnmatch.fnmatch(path, pattern.lstrip("/"))


def _effective_eol_git(root: Path) -> dict[str, str | None] | None:
    """Ask git itself (all .gitattributes levels, info/attributes, core.attributesFile). None when git is unusable
    or `root` is not inside a work tree (also covers a target that is a subfolder of a larger repo)."""
    try:
        out = subprocess.run(["git", "check-attr", "text", "eol", "--", *HARNESS_EOL], cwd=root, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    attrs: dict[str, dict[str, str]] = {}
    for line in out.stdout.splitlines():
        path, attr, value = (part.strip() for part in line.rsplit(":", 2))
        attrs.setdefault(path, {})[attr] = value
    return {p: (a.get("eol") if a.get("text") != "unset" else None) for p, a in attrs.items()}


def _effective_eol_parsed(root: Path) -> dict[str, str | None]:
    """Fallback without git: root .gitattributes only; last matching rule wins; -text/binary/-eol/!eol clear the pin."""
    attrs = root / ".gitattributes"
    rules: list[tuple[str, str | None]] = []
    text = attrs.read_text(encoding="utf-8-sig", errors="replace") if attrs.exists() else ""
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        effect = "keep"
        for token in parts[1:]:
            if token.startswith("eol="):
                effect = token.split("=", 1)[1]
            elif token in ("-text", "binary", "-eol", "!eol"):
                effect = None
        if effect != "keep":
            rules.append((parts[0].strip('"'), effect))
    result: dict[str, str | None] = {}
    for path in HARNESS_EOL:
        result[path] = None
        for pattern, eol in rules:
            if _attr_pattern_matches(pattern, path):
                result[path] = eol
    return result


def line_endings_pinned(root: Path) -> bool:
    """True when every harness script resolves to its required eol (and is not marked non-text)."""
    effective = _effective_eol_git(root)
    if effective is None:
        effective = _effective_eol_parsed(root)
    return all(effective.get(path) == wanted for path, wanted in HARNESS_EOL.items())


def detect_python() -> str:
    """The command that starts Python >= 3.9 on this machine, as hooks will spell it (python / python3 / py -3).
    The Windows Store stub `python.exe` fails the version probe and is skipped."""
    import shutil
    import sys
    candidates = ["python", "py -3", "python3"] if os.name == "nt" else ["python3", "python"]
    for cand in candidates:
        parts = cand.split()
        exe = shutil.which(parts[0])
        if not exe:
            continue
        try:
            out = subprocess.run([exe, *parts[1:], "-c", "import sys; print(sys.version_info >= (3, 9))"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if out == "True":
            return cand
    return f'"{sys.executable}"'


def hook_command(python: str, event: str) -> str:
    return f'{python} "${{CLAUDE_PROJECT_DIR}}/.claude/hooks/harness_hook.py" {event}'


# Runtime subcommands that only read state or change it through the runtime's own rules. `try`, `verify`, `setup` and
# `loop` run a command taken from feature_list.json / .harness/config.json, which an agent can edit: they are left to
# the normal permission prompt, so an agent-written command never runs unseen. (`start`, `check`, `exit-check` run the
# configured project commands; edits of .harness/config.json are guarded with "ask".)
SAFE_RUNTIME_COMMANDS = ("start", "status", "next", "add", "block", "unblock", "handoff", "check", "exit-check",
                         "validate", "docs", "rules", "trace", "report", "failures", "fail", "size")


def make_claude_settings(inv: dict, python: str = "python", statusline: bool = True) -> dict:
    """Least-privilege permissions (lesson 02) + the v2 hooks (state injection, guard, activity, compaction snapshot,
    clean-state Stop gate) + a status line. Every hook goes through the fail-open dispatcher."""
    launchers = dict.fromkeys(("python", "python3", python))  # what agents type, plus the hooks' interpreter
    allow = [f"Bash({py} scripts/harness.py {sub}{rest})" for py in launchers
             for sub in SAFE_RUNTIME_COMMANDS for rest in ("", " *")]
    allow += ["Bash(./init.sh)", "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)"]
    for key in ("test", "lint", "typecheck", "build", "e2e"):
        for step in inv["commands"].get(key, []):
            cmd = step["cmd"].split("  #")[0].strip()
            allow += [f"Bash({cmd})", f"Bash({cmd} *)"]
    hooks: dict = {}
    for event, matcher, arg in HOOK_EVENTS:
        entry: dict = {"matcher": matcher} if matcher else {}
        entry["hooks"] = [{"type": "command", "command": hook_command(python, arg)}]
        if event == "SessionEnd":
            entry["hooks"][0]["timeout"] = 5
        hooks[event] = [entry]
    settings = {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {
            "allow": list(dict.fromkeys(allow)),
            "ask": ["Bash(rm -rf *)", "Bash(git reset --hard *)", "Bash(git clean *)", "Bash(git push *)"],
            "deny": ["Bash(git push --force *)", "Bash(git push -f *)",
                     "Read(./.env)", "Read(./.env.local)", "Read(./.env.production)"],
        },
        "hooks": hooks,
    }
    if statusline:  # relative path: the status line runs in the session's directory (no CLAUDE_PROJECT_DIR there)
        settings["statusLine"] = {"type": "command", "command": f"{python} .claude/hooks/harness_hook.py statusline",
                                  "padding": 0}
    return settings


# ------------------------------------------------------------------ writer ---

class Plan:
    def __init__(self, root: Path):
        self.root = root
        self.create: list[tuple[str, str]] = []     # (path, content)
        self.proposals: list[tuple[str, str]] = []  # (original path, content)
        self.skipped: list[str] = []
        self.notes: list[str] = []
        # --claude into an existing .claude/ (user decision 2026-10-09): additive merge, same-named harness items replaced
        self.replace: list[tuple[str, str]] = []    # existing harness-named file -> overwritten with ours
        self.replace_dirs: list[str] = []           # existing harness-named directory -> removed before writing ours
        self.merged: list[tuple[str, str]] = []     # existing file + our entries (user entries kept)
        self.removed: list[str] = []                # files inside a replaced directory, reported before deletion
        # v2 manifest (.claude/harness-kit.json): what the harness installed, with hashes, so updates never overwrite
        # a file the user changed and uninstall removes only unchanged harness files
        self.manifest = _read_manifest(root)
        self.force = False
        self.payload: dict[str, str] = {}           # .claude/ payload path -> sha256 of the content we install
        self.settings_added: dict = self.manifest.get("settings_added", {})
        self.delete: list[str] = []                 # files to delete (legacy/dropped harness items, unchanged)
        self.manifest_extra: dict = {}              # e.g. python, level: recorded in the manifest

    def add_harness_item(self, rel: str, content: str) -> None:
        """A file the harness owns by name under .claude/: replace a same-named one, otherwise create it.
        For a skill, the same-named skill directory is replaced as a whole. Anything unexpected at that path
        (a file where a directory should be, a link, read-only content) is left untouched and proposed instead."""
        parts = rel.split("/")
        owned_dir = "/".join(parts[:3]) if parts[:2] == [".claude", "skills"] and len(parts) > 3 else None
        blocked = _blocked_path(self.root, owned_dir, want_dir=True) if owned_dir else \
            _blocked_path(self.root, rel, want_dir=False)
        if blocked:
            self.proposals.append((rel, content))
            self.notes.append(f"{blocked}: left untouched; the harness version is in .harness/proposals/{rel}.")
            return
        self.payload[rel] = _sha(content)
        target = self.root / rel
        installed = self.manifest.get("files", {})
        if rel in installed or (owned_dir and any(f.startswith(owned_dir + "/") for f in installed)):
            # installed before (v2 manifest): replace only what the user has not changed; never delete their extras
            if not target.exists():
                self.create.append((rel, content))
            elif _sha_file(target) != _sha(content):
                if _sha_file(target) == installed.get(rel) or self.force:
                    self.replace.append((rel, content))
                else:
                    self.proposals.append((rel, content))
                    self.notes.append(f"{rel} was changed after it was installed: kept your version; the new harness "
                                      f"version is in .harness/proposals/{rel} (or update with --force).")
            return
        if owned_dir and (self.root / owned_dir).is_dir():
            files = [p for p in (self.root / owned_dir).rglob("*") if p.is_file()]
            if files == [target] and target.read_text(encoding="utf-8", errors="replace") == content:
                return  # already identical: nothing to do
            if owned_dir not in self.replace_dirs:
                self.replace_dirs.append(owned_dir)
                self.removed += sorted(p.relative_to(self.root).as_posix() for p in files)
            self.replace.append((rel, content))
        elif target.exists():
            if target.read_text(encoding="utf-8", errors="replace") != content:
                self.replace.append((rel, content))
        else:
            self.create.append((rel, content))

    def add(self, rel: str, content: str, propose_if_exists: bool = True) -> None:
        if (self.root / rel).exists():
            p = self.root / rel
            if p.is_file() and _sha_file(p) == _sha(content):
                return  # already what we would write: no proposal, no noise
            if propose_if_exists:
                self.proposals.append((rel, content))
            else:
                self.skipped.append(rel)
        else:
            self.create.append((rel, content))

    def apply(self) -> None:
        for rel_dir in self.replace_dirs:
            shutil.rmtree(self.root / rel_dir)
        for rel in self.delete:
            (self.root / rel).unlink(missing_ok=True)
            _prune_empty_dirs(self.root, (self.root / rel).parent)
        for rel, content in self.create + self.replace + self.merged:
            _write(self.root / rel, content)
        for rel, content in self.proposals:
            _write(self.root / ".harness" / "proposals" / proposal_name(rel), content)
        if self.payload:
            self.write_manifest(**self.manifest_extra)

    def write_manifest(self, **extra) -> None:
        """Record the installed payload: hashes of what is on disk now for files that hold our content (a file the user
        changed keeps its old hash, so it still counts as changed next time)."""
        files = {}
        old = self.manifest.get("files", {})
        for rel, sha in sorted(self.payload.items()):
            p = self.root / rel
            if p.is_file():
                files[rel] = sha if _sha_file(p) == sha else old.get(rel, sha)
        # no machine-specific kit path: the manifest is committed and shared by the team
        old_data = {k: v for k, v in self.manifest.items() if k != "kit_root"}
        data = {**old_data, "kit": "harness-kit", "version": VERSION, "files": files,
                "settings_added": self.settings_added, **extra}
        volatile = ("updated_at", "installed_at", "previous_version")
        if self.manifest and {k: v for k, v in data.items() if k not in volatile} == \
                {k: v for k, v in old_data.items() if k not in volatile}:
            return  # nothing changed: leave the file (and the git tree) alone
        data["updated_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        data.setdefault("installed_at", data["updated_at"])
        _write(self.root / MANIFEST, _as_json(data))

    def summary(self, dry_run: bool) -> str:
        verb = "Will create" if dry_run else "Created"
        out = [f"{verb} {len(self.create)} files:"] + [f"  + {p}" for p, _ in self.create]
        if self.proposals:
            out.append(f"Already exists — proposal written to .harness/proposals/ ({len(self.proposals)}):")
            out += [f"  ~ {p}" + (f" (as {proposal_name(p)})" if proposal_name(p) != p else "")
                    for p, _ in self.proposals]
        if self.merged:
            out.append("Merged into an existing file (your entries kept, harness entries added):")
            out += [f"  & {p}" for p, _ in self.merged]
        if self.replace:
            out.append("Replaced (same name as a harness item):")
            out += [f"  > {p}" for p in self.replace_dirs] + [f"  > {p}" for p, _ in self.replace
                                                              if not any(p.startswith(d + "/") for d in self.replace_dirs)]
            out += [f"    - removed old file: {p}" for p in self.removed]
        if self.skipped:
            out.append("Kept as is (already present):")
            out += [f"  = {p}" for p in self.skipped]
        if self.notes:
            out.append("Notes:")
            out += [f"  ! {n}" for n in self.notes]
        return "\n".join(out)


def _is_link(p: Path) -> bool:
    return p.is_symlink() or bool(getattr(os.path, "isjunction", lambda _: False)(p))


def _blocked_path(root: Path, rel: str, want_dir: bool) -> str | None:
    """Why `rel` cannot be safely (re)written by the harness, or None. Parents must be plain directories; the target
    must be absent or of the expected type, not a link, and writable throughout."""
    cur = root
    for part in rel.split("/")[:-1]:
        cur = cur / part
        if _is_link(cur) or (cur.exists() and not cur.is_dir()):
            return f"{cur.relative_to(root).as_posix()} is not a plain directory"
    target = root / rel
    if _is_link(target):
        return f"{rel} is a link"
    if not target.exists():
        return None
    if want_dir:
        if not target.is_dir():
            return f"{rel} is not a directory"
        for p in target.rglob("*"):
            if _is_link(p) or (p.is_file() and not os.access(p, os.W_OK)):
                return f"{rel} contains read-only or linked entries"
    elif not target.is_file():
        return f"{rel} is not a regular file"
    elif not os.access(target, os.W_OK):
        return f"{rel} is read-only"
    return None


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".ps1":  # Windows PowerShell 5.1 needs a BOM to read UTF-8
        path.write_text(content, encoding="utf-8-sig", newline="\r\n")
        return
    path.write_text(content, encoding="utf-8", newline="\n")
    if path.suffix == ".sh":
        try:
            path.chmod(0o755)
        except OSError:
            pass


def _sha(content: str) -> str:
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sha_file(p: Path) -> str | None:
    """Content hash with line endings normalized: a checkout under core.autocrlf (CRLF) is not a user change."""
    import hashlib
    try:
        return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


def _read_manifest(root: Path) -> dict:
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("files", {}), dict) else {}


def _prune_empty_dirs(root: Path, d: Path) -> None:
    """Remove empty directories up to (not including) .claude/ itself."""
    stop = (root / ".claude").resolve()
    d = d.resolve()
    while d != stop and stop in d.parents and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def claude_payload(python: str = "python") -> list[tuple[str, str]]:
    """(destination, rendered content) for every file of the Claude Code layer."""
    ctx = {"KIT_ROOT": KIT_ROOT.as_posix(), "PYTHON": python}
    out = []
    for src in sorted(CLAUDE_PAYLOAD.rglob("*")):
        if src.is_file() and "__pycache__" not in src.parts and src.suffix != ".pyc":
            rel = ".claude/" + src.relative_to(CLAUDE_PAYLOAD).as_posix()
            out.append((rel, render(src.read_text(encoding="utf-8"), ctx)))
    return out


def plan_claude_layer(plan: "Plan", inv: dict, python: str = "python", statusline: bool = True) -> None:
    """Agents, skills, hook dispatcher and output style under .claude/, the merged settings, the removal of unchanged
    v1 items and of files dropped from the kit since the last install."""
    payload = claude_payload(python)
    for rel, content in payload:
        plan.add_harness_item(rel, content)
    still_shipped = {rel for rel, _ in payload}  # a blocked item is still part of the kit: never "dropped"
    for rel, sha in LEGACY_ITEMS.items():
        p = plan.root / rel
        if p.is_file() and not _is_link(p):
            if _sha_file(p) == sha:
                plan.delete.append(rel)
                plan.notes.append(f"Removed the unchanged v1 item {rel} (replaced by hk-reviewer / hk-optimize).")
            else:
                plan.notes.append(f"{rel} is a v1 harness item you changed: kept; v2 uses hk-reviewer / hk-optimize.")
    for rel, sha in plan.manifest.get("files", {}).items():
        p = plan.root / rel
        if rel not in still_shipped and p.is_file():
            if _sha_file(p) == sha:
                plan.delete.append(rel)
                plan.notes.append(f"Removed {rel}: no longer part of harness-kit {VERSION}.")
            else:
                plan.notes.append(f"{rel} is no longer part of harness-kit but you changed it: kept.")
    _plan_claude_settings(plan, inv, python, statusline)


def _as_json(data: dict | list) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# AGENTS.md docs map: a row is rendered only when its target is generated (no broken links at any level)
DOCS_MAP = [
    ("docs/ARCHITECTURE.md", "Adding a module, changing layer boundaries, unsure where code belongs"),
    ("docs/PRODUCT.md", "You need expected user behavior / acceptance criteria"),
    ("docs/RELIABILITY.md", "Runtime debugging, logs, health checks, golden journeys"),
    ("docs/QUALITY.md", "Choosing what to optimize next (A–D grades per module, hotspots)"),
    ("docs/DECISIONS.md", "Before changing an approach that was already chosen"),
    ("docs/harness/", "Templates: sprint contract, evaluator rubric, handoff, clean-state checklist, refactor playbook"),
    ("docs/SECURITY.md", "Touching auth, secrets, external input, production commands"),
    ("docs/exec-plans/", "Work spanning multiple sessions (plans, tech debt)"),
]


def _level_context(dests: set[str]) -> dict:
    """Template text that depends on which files this retrofit actually generates."""
    def has(path: str) -> bool:
        return path in dests or (path.endswith("/") and any(d.startswith(path) for d in dests))

    rows = [f"| `{path}` | {why} |" for path, why in DOCS_MAP if has(path)]
    playbook = "docs/harness/legacy-refactor-playbook.md"
    return {
        "docs_map_rows": "\n".join(rows) + "\n" if rows else "",
        "playbook_ref": f" See `{playbook}`." if has(playbook) else "",
        "quality_step": ("Grade modules in `docs/QUALITY.md` and turn hotspots into optimization features."
                         if has("docs/QUALITY.md") else
                         "Turn the riskiest code into `refactor` features with measurable verification (re-run the "
                         "retrofit with --level standard to add docs/QUALITY.md with module grades and hotspots)."),
    }


def _plan_templates(plan: Plan, inv: dict, level: str, progress_file: str, claude: bool) -> None:
    """Rendered template files for every level up to `level` (+ the Claude Code files when opted in);
    an existing progress log replaces PROGRESS.md."""
    files = [f for lvl in LEVEL_ORDER[: LEVEL_ORDER.index(level) + 1] for f in LEVELS[lvl]]
    files += [f for f in (CLAUDE_DOCS if claude else []) if f not in files]
    ctx = build_context(inv, level)
    ctx["claude_notes"] = CLAUDE_NOTES if claude else ""
    ctx.update(_level_context({dest for _, dest in files}))
    for src, dest in files:
        if dest == "PROGRESS.md" and progress_file != "PROGRESS.md":
            plan.skipped.append(f"PROGRESS.md (using existing {progress_file})")
            continue
        plan.add(dest, render((TEMPLATES / src).read_text(encoding="utf-8"), ctx))


def _plan_feature_list(plan: Plan, inv: dict, legacy_entry: list[str], feature_file: str) -> None:
    """A legacy feature list is never touched: the seeded harness features become a proposal to merge."""
    content = _as_json(make_features(inv, legacy_entry))
    if (plan.root / feature_file).exists():
        plan.proposals.append((feature_file, content))
        plan.notes.append(f"{feature_file} already exists: kept as is; the proposed harness features are in "
                          f".harness/proposals/{feature_file} — merge by hand (the runtime also accepts the legacy schema).")
    else:
        plan.add(feature_file, content)


# Exactly the harness's own hook commands — v1's Stop hook and v2's dispatcher — recognized by what they run, after
# any launcher (python, py -3.12, python3.12, uv run python, /usr/bin/env python3, VAR=1 python, a quoted path), never
# a user command that merely mentions harness.py or hook-stop. A launcher may not contain shell operators, so
# `mine && python scripts/harness.py hook-stop` stays the user's.
_PY = r'[^;&|<>`]*?[\s"]'
_DIR = r'(\$\{CLAUDE_PROJECT_DIR\}/|\$CLAUDE_PROJECT_DIR/|\./)?'
HARNESS_STOP_RE = re.compile(rf'^{_PY}"?{_DIR}(scripts/harness\.py"?\s+hook-stop|\.claude/hooks/harness_hook\.py"?\s+stop)\s*$')
HARNESS_HOOK_RE = re.compile(rf'^{_PY}"?{_DIR}(scripts/harness\.py"?\s+hook-stop|\.claude/hooks/harness_hook\.py"?\s+[a-z-]+)\s*$')


class _DuplicateKey(ValueError):
    pass


def _no_duplicate_keys(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _DuplicateKey("duplicate key")  # re-serializing would silently drop a value
    return dict(pairs)


def _is_harness_hook(hook) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and bool(HARNESS_HOOK_RE.match(hook["command"]))


def _is_harness_stop_hook(hook) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and bool(HARNESS_STOP_RE.match(hook["command"]))


def _without_harness_hooks(entries: list) -> list:
    """Remove only the harness's own hook objects; keep every other hook, and the entry (with its matcher) if any remain."""
    kept = []
    for entry in entries:
        if not (isinstance(entry, dict) and isinstance(entry.get("hooks"), list)):
            kept.append(entry)  # unknown shape: a user entry, keep it as is
            continue
        hooks = [h for h in entry["hooks"] if not _is_harness_hook(h)]
        if len(hooks) == len(entry["hooks"]):
            kept.append(entry)
        elif hooks:
            kept.append({**entry, "hooks": hooks})
    return kept


def _is_harness_statusline(value) -> bool:
    return isinstance(value, dict) and "harness_hook.py" in str(value.get("command", ""))


def merge_claude_settings(existing: dict, ours: dict) -> tuple[dict | None, list[str]]:
    """Additive merge: keep every user key/entry, append our missing permission rules, replace only the harness's own
    hooks (v1 and v2 spellings) per event, and set the status line only when the user has none.
    Returns (merged or None when the structure cannot be merged safely, notes)."""
    merged = json.loads(json.dumps(existing))
    perms, hooks = merged.setdefault("permissions", {}), merged.setdefault("hooks", {})
    if not isinstance(perms, dict) or not isinstance(hooks, dict):
        return None, []
    for key in ("allow", "ask", "deny"):
        if not isinstance(perms.get(key, []), list):
            return None, []
    if any(not isinstance(hooks.get(event, []), list) for event in ours["hooks"]):
        return None, []
    notes, user_allow = [], {r for r in perms.get("allow", []) if isinstance(r, str)}
    for key, rules in ours["permissions"].items():
        current = perms.setdefault(key, [])
        for rule in rules:
            if key in ("ask", "deny") and rule in user_allow:  # would silently override the user's own allow
                notes.append(f"kept your allow rule {rule}; the harness '{key}' rule for it was not added")
            elif rule not in current:
                current.append(rule)
    for event, entries in ours["hooks"].items():
        hooks[event] = _without_harness_hooks(hooks.get(event, [])) + entries
    if "statusLine" in ours:
        if "statusLine" not in merged or _is_harness_statusline(merged["statusLine"]):
            merged["statusLine"] = ours["statusLine"]
        else:
            notes.append("kept your statusLine; the harness status line was not installed")
    merged.setdefault("$schema", ours["$schema"])
    return merged, notes


def strip_claude_settings(existing: dict, added_rules: dict) -> dict:
    """Inverse of the merge, for `uninstall`: drop the harness hooks and status line, and the permission rules the
    harness added (recorded in the manifest), keeping everything else exactly."""
    out = json.loads(json.dumps(existing))
    hooks = out.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks):
            if isinstance(hooks[event], list):
                hooks[event] = _without_harness_hooks(hooks[event])
                if not hooks[event]:
                    del hooks[event]
        if not hooks:
            del out["hooks"]
    if _is_harness_statusline(out.get("statusLine")):
        del out["statusLine"]
    perms = out.get("permissions")
    if isinstance(perms, dict):
        for key, rules in (added_rules or {}).items():
            if isinstance(perms.get(key), list):
                perms[key] = [r for r in perms[key] if r not in rules]
                if not perms[key]:
                    del perms[key]
        if not perms:
            del out["permissions"]
    return out


def _plan_claude_settings(plan: Plan, inv: dict, python: str = "python", statusline: bool = True) -> None:
    ours = make_claude_settings(inv, python, statusline)
    path = plan.root / ".claude" / "settings.json"
    blocked = _blocked_path(plan.root, ".claude/settings.json", want_dir=False)
    if not path.exists() and not blocked:
        plan.add(".claude/settings.json", _as_json(ours))
        plan.settings_added = ours["permissions"]
        return
    existing, notes = None, []
    if not blocked:
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_no_duplicate_keys)
        except (ValueError, OSError):
            existing = None
    merged = None
    if isinstance(existing, dict):
        merged, notes = merge_claude_settings(existing, ours)
    if merged is not None:  # remember which rules the harness itself added, so uninstall removes only those
        before = existing.get("permissions", {}) if isinstance(existing.get("permissions"), dict) else {}
        prior = _read_manifest(plan.root).get("settings_added", {})
        plan.settings_added = {k: [r for r in v if r not in before.get(k, []) or r in prior.get(k, [])]
                               for k, v in ours["permissions"].items()}
    if merged is None:
        plan.proposals.append((".claude/settings.json", _as_json(ours)))
        plan.notes.append(".claude/settings.json could not be merged safely (invalid JSON, duplicate keys, unexpected "
                          "structure or not a regular file): it was left untouched; add the entries from "
                          ".harness/proposals/.claude/settings.json by hand.")
    elif merged != existing:
        plan.merged.append((".claude/settings.json", _as_json(merged)))
        plan.notes.append("Merged the harness permissions, hooks and status line into the existing .claude/settings.json "
                          "(your entries were kept; JSON is re-indented)." + ("".join(f" Note: {n}." for n in notes)))


def _plan_gitattributes(plan: Plan) -> None:
    """Create .gitattributes, or propose the harness rules when an existing file does not pin them."""
    attrs = plan.root / ".gitattributes"
    if not attrs.exists():
        plan.add(".gitattributes", GITATTRIBUTES)
    elif not line_endings_pinned(plan.root):
        existing = attrs.read_text(encoding="utf-8", errors="replace")
        plan.proposals.append((".gitattributes", existing.rstrip("\n") + "\n\n" + GITATTRIBUTES))
        plan.notes.append(".gitattributes does not pin the harness scripts' line endings: under core.autocrlf, init.sh "
                          "and scripts/harness.py may be checked out with CRLF and fail in bash. Merge "
                          ".harness/proposals/gitattributes.proposed into .gitattributes, then run `git add --renormalize .`.")


def _policy_notes(inv: dict, legacy_entry: list[str]) -> list[str]:
    """What the retrofit could not fix by itself and the user/skill must act on."""
    notes = []
    if legacy_entry:
        notes.append(f"Legacy instructions found: {', '.join(legacy_entry)} → feature H005 requires merging them "
                     f"into AGENTS.md + docs/.")
    if not inv["is_git"]:
        notes.append("Project does not use git: the harness needs git for checkpoints/evidence. Use --git-init.")
    if not inv["stacks"]:
        notes.append("No stack detected: fill in commands in .harness/config.json by hand.")
    if not inv["commands"]["test"]:
        notes.append("No test command found: feedback is the weakest link — feature H002 must add one.")
    if not inv["has_gitignore"]:
        notes.append("Missing .gitignore in the project root.")
    return notes


HARNESS_GITIGNORE = ["trace.jsonl", "last-check.json", "session.json", "loop/", "runs/", "activity.jsonl",
                     "activity.jsonl.tmp", "hook-state.json", "compact-snapshot.md", "proposals/"]


def _plan_harness_gitignore(plan: "Plan") -> None:
    """Runtime state stays out of git; an existing file only gets the missing lines appended."""
    rel = ".harness/.gitignore"
    p = plan.root / rel
    if not p.exists():
        plan.add(rel, "\n".join(HARNESS_GITIGNORE) + "\n")
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    missing = [line for line in HARNESS_GITIGNORE if line not in text.splitlines()]
    if missing:
        plan.merged.append((rel, text.rstrip("\n") + "\n" + "\n".join(missing) + "\n"))


def _plan_runtime(plan: "Plan", upgrade_runtime: bool) -> None:
    """scripts/harness.py is a harness-managed file: install/update replace it (as `upgrade` does); a plain retrofit
    keeps an existing one and proposes the new version."""
    rel = "scripts/harness.py"
    content = (Path(__file__).parent / "runtime.py").read_text(encoding="utf-8")
    p = plan.root / rel
    if upgrade_runtime and p.is_file() and not _is_link(p) and _is_kit_runtime(p):
        if p.read_bytes() != content.encode("utf-8"):
            plan.replace.append((rel, content))
    else:
        if upgrade_runtime and p.is_file():
            plan.notes.append(f"{rel} exists and is not the harness-kit runtime: kept; the hooks need the runtime — "
                              f"move your file, then run install again (the runtime is in .harness/proposals/{rel}).")
        plan.add(rel, content)


def _is_kit_runtime(p: Path) -> bool:
    """A scripts/harness.py written by harness-kit (v1 or v2) carries this header; anything else is the user's file."""
    try:
        head = p.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        return False
    return "File managed by harness-kit" in head


def build_plan(root: Path, level: str = "standard", claude: bool = False, python: str = "python",
               statusline: bool = True, force: bool = False) -> tuple[Plan, dict]:
    """Everything `retrofit` would create or propose, without touching the disk. `claude` adds the Claude Code layer:
    agents, skills, hook dispatcher, output style, merged settings (permissions, hooks, status line) and the manifest;
    it also upgrades an existing runtime, which the hooks depend on."""
    root = root.resolve()
    inv = detect.inventory(root)
    plan = Plan(root)
    plan.force = force
    plan.manifest_extra = {"python": python, "level": level}
    progress_file = inv["progress_files"][0] if inv["progress_files"] else "PROGRESS.md"
    feature_file = "feature_list.json"
    legacy_entry = [e for e in inv["entry_files"] if (e != "AGENTS.md" or _line_count(root / e) > 200)
                    and not (e == "CLAUDE.md" and imports_agents_md(root / e))]

    _plan_templates(plan, inv, level, progress_file, claude)
    plan.add(".harness/config.json", _as_json(make_config(inv, progress_file, feature_file)))
    _plan_feature_list(plan, inv, legacy_entry, feature_file)
    plan.add(".harness/rules.json", _as_json(make_rules()))  # tiny, and AGENTS.md references it at every level
    if claude:
        plan_claude_layer(plan, inv, python, statusline)
    _plan_harness_gitignore(plan)
    _plan_runtime(plan, upgrade_runtime=claude)
    _plan_gitattributes(plan)
    plan.notes += _policy_notes(inv, legacy_entry)
    return plan, inv


def imports_agents_md(p: Path) -> bool:
    """CLAUDE.md that imports AGENTS.md (`@AGENTS.md`, as the harness writes it) is a router, not legacy instructions."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(line.strip().startswith("@AGENTS.md") for line in text.splitlines()[:20])


def _line_count(p: Path) -> int:
    try:
        return len(p.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")


def retrofit(root: Path, level: str = "standard", dry_run: bool = False, git_init: bool = False,
             commit: bool = False, claude: bool = False, python: str = "python", statusline: bool = True,
             force: bool = False) -> str:
    plan, inv = build_plan(root, level, claude, python, statusline, force)
    if dry_run:
        return plan.summary(dry_run=True)
    root = Path(inv["root"])
    if git_init and not inv["is_git"]:
        git(root, "init")
    plan.apply()
    out = plan.summary(dry_run=False)
    if commit:
        paths = [p for p, _ in plan.create] + [".harness"]
        git(root, "add", "--", *paths)
        res = git(root, "commit", "-m", "chore(harness): retrofit harness engineering scaffold\n\n"
                                        "Add AGENTS.md, feature_list.json, PROGRESS.md, docs/, scripts/harness.py, .harness/.")
        out += "\n" + ("Committed harness checkpoint." if res.returncode == 0 else f"Commit failed: {res.stderr.strip()}")
        if plan.replace or plan.merged:
            out += "\nFiles you already had (merged/replaced under .claude/) were NOT auto-committed: review and commit them."
    out += (f"\n\nNext steps:\n"
            f"  1. cd \"{root}\" && {H} start\n"
            f"  2. Complete H001…H006 with your coding agent (in Claude Code: "
            f"{'/hk-init' if claude else 'the /harness-retrofit skill'}) or by hand.\n"
            f"  3. Re-check readiness: harness audit \"{root}\"")
    return out


def upgrade(root: Path) -> str:
    """Update scripts/harness.py to the current runtime version (a harness-managed file)."""
    target = root / "scripts" / "harness.py"
    new = (Path(__file__).parent / "runtime.py").read_text(encoding="utf-8")
    if target.exists() and target.read_text(encoding="utf-8") == new:
        return "scripts/harness.py is already up to date."
    _write(target, new)
    return f"Updated scripts/harness.py to runtime {VERSION}."
