"""Install the harness and its Claude Code layer into a project (claudekit-style, but manifest-tracked).

- install:   v1 retrofit (agent-agnostic files, never overwritten) + the .claude/ layer (agents, skills, hooks,
             output style, merged settings) + a manifest with a sha256 per installed file.
- update:    re-render only the harness-managed parts (the .claude/ layer, scripts/harness.py, .harness/.gitignore);
             files the user changed are kept and the new version goes to .harness/proposals/.
- uninstall: remove the unchanged .claude/ files the manifest lists and the harness entries in settings.json.
             Project content (AGENTS.md, feature list, docs/, PROGRESS.md) stays: it belongs to the project now.
- doctor:    read-only health check of an installation.
- global:    skills, agents and the output style into ~/.claude (no hooks: they need a project runtime).
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import detect, scaffold
from .runtime import VERSION

GLOBAL_MANIFEST = "harness-kit.json"
GLOBAL_PARTS = ("agents/", "skills/", "output-styles/")


def _python(python: str | None) -> str:
    return python or scaffold.detect_python()


def install(root: Path, level: str = "standard", python: str | None = None, statusline: bool = True,
            force: bool = False, dry_run: bool = False, git_init: bool = False, commit: bool = False) -> str:
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"ERROR: {root} is not a directory.")
    manifest = scaffold._read_manifest(root)
    if manifest:  # installed before: re-running the install command is the documented way to update
        py = python or manifest.get("python") or _python(None)
        out = update(root, python=py, statusline=statusline, force=force, dry_run=dry_run)
        return out if dry_run else out + _done(root, py, verb="updated")
    py = _python(python)
    out = scaffold.retrofit(root, level=level, dry_run=dry_run, git_init=git_init, commit=commit, claude=True,
                            python=py, statusline=statusline, force=force)
    out = _condense(out.split("\n\nNext steps:")[0])
    if dry_run:
        return out
    return out + _done(root, py, verb="installed")


def kit_command(sub: str, root: Path, py: str = "python") -> str:
    """How to run a kit command on this machine (from the kit folder the install ran from)."""
    return f"{py} \"{(scaffold.KIT_ROOT / 'harness.py').as_posix()}\" {sub} \"{root.as_posix()}\""


def _read_settings(root: Path):
    try:
        return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _done(root: Path, py: str, verb: str) -> str:
    settings = _read_settings(root)
    ours = isinstance(settings, dict) and scaffold._is_harness_statusline(settings.get("statusLine"))
    parts = "the hooks, the status line and the /hk-* commands" if ours else \
        "the hooks and the /hk-* commands (your own status line was kept)"
    out = (f"\n\nDONE — harness-kit {VERSION} is {verb} and active in {root}\n"
           f"  Just open this folder in Claude Code: {parts} work right away (hooks run with `{py}`).\n"
           f"  Optional: type /hk-init once to fill in the project knowledge (AGENTS.md, docs/) and the "
           f"verification baseline; /hk-help shows every command.\n"
           f"  Health check any time: {kit_command('doctor', root, py)}\n"
           f"  Update later: run the same install command again.")
    claude_md = root / "CLAUDE.md"
    if claude_md.is_file() and not scaffold.imports_agents_md(claude_md):
        out += ("\n  NOTE: your CLAUDE.md does not import AGENTS.md, so Claude Code does not see the harness rules: "
                "add the line `@AGENTS.md` at the top of CLAUDE.md.")
    return out


def _condense(summary: str) -> str:
    """One line per payload folder instead of one per skill/agent file."""
    out, seen = [], set()
    for line in summary.splitlines():
        m = re.match(r"^(\s+[+>~=] )\.claude/(skills|agents)/", line)
        if m:
            key = (m.group(1), m.group(2))
            if key not in seen:
                seen.add(key)
                n = sum(1 for l in summary.splitlines() if l.startswith(f"{m.group(1)}.claude/{m.group(2)}/"))
                out.append(f"{m.group(1)}.claude/{m.group(2)}/ ({n} files)")
            continue
        out.append(line)
    return "\n".join(out)


def update(root: Path, python: str | None = None, statusline: bool = True, force: bool = False,
           dry_run: bool = False) -> str:
    """Refresh the harness-managed parts only; never touches project content."""
    root = root.resolve()
    manifest = scaffold._read_manifest(root)
    if not manifest:
        raise SystemExit(f"ERROR: no {scaffold.MANIFEST} in {root}.\nFIX: install first: {kit_command('install', root)}")
    py = python or manifest.get("python") or _python(None)
    plan = scaffold.Plan(root)
    plan.force = force
    plan.manifest_extra = {"python": py, "previous_version": manifest.get("version")}
    scaffold.plan_claude_layer(plan, detect.inventory(root), py, statusline)
    scaffold._plan_harness_gitignore(plan)
    scaffold._plan_runtime(plan, upgrade_runtime=True)
    if dry_run:
        return plan.summary(dry_run=True)
    plan.apply()
    return plan.summary(dry_run=False) + f"\n\nUpdated harness-kit {manifest.get('version')} -> {VERSION}."


def _has_harness_hooks(settings: dict) -> bool:
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return False
    return any(scaffold._is_harness_hook(h) for entries in hooks.values() if isinstance(entries, list)
               for e in entries if isinstance(e, dict) and isinstance(e.get("hooks"), list) for h in e["hooks"])


def _unchanged(p: Path, sha: str) -> bool:
    return p.is_file() and not p.is_symlink() and scaffold._sha_file(p) == sha


def uninstall(root: Path, dry_run: bool = False) -> str:
    root = root.resolve()
    manifest = scaffold._read_manifest(root)
    if not manifest:
        raise SystemExit(f"ERROR: no {scaffold.MANIFEST} in {root}: nothing to uninstall.")
    removed, kept = [], []
    for rel, sha in sorted(manifest.get("files", {}).items()):
        p = root / rel
        if _unchanged(p, sha):
            removed.append(rel)
        elif p.exists():
            kept.append(rel)
    lines = []
    settings_p = root / ".claude" / "settings.json"
    new_settings = None
    if settings_p.is_file():
        try:
            current = json.loads(settings_p.read_text(encoding="utf-8-sig"))
        except ValueError:
            current = None
            lines.append("! .claude/settings.json is not valid JSON: left untouched; remove the harness hooks by hand.")
        if isinstance(current, dict):
            new_settings = scaffold.strip_claude_settings(current, manifest.get("settings_added", {}))
    dispatcher = ".claude/hooks/harness_hook.py"
    if dispatcher in removed and settings_p.is_file() and (new_settings is None or _has_harness_hooks(new_settings)):
        # hooks that still point at the dispatcher would fail (exit 2 blocks every prompt): keep it until they are gone
        removed.remove(dispatcher)
        lines.append(f"! Kept {dispatcher}: .claude/settings.json still registers harness hooks. Remove them, then "
                     f"delete the file.")
    if dry_run:
        lines = [f"Would remove {len(removed)} unchanged harness file(s):"] + [f"  - {r}" for r in removed] + lines
    else:
        for rel in removed:
            (root / rel).unlink()
            scaffold._prune_empty_dirs(root, (root / rel).parent)
        if new_settings is not None:
            if set(new_settings) - {"$schema"}:
                scaffold._write(settings_p, scaffold._as_json(new_settings))
            else:
                settings_p.unlink()
            lines.append("Removed the harness hooks, status line and permission rules from .claude/settings.json "
                         "(your entries were kept).")
        (root / scaffold.MANIFEST).unlink(missing_ok=True)
        lines = [f"Removed {len(removed)} unchanged harness file(s) from .claude/."] + lines
    if kept:
        lines.append(f"Kept {len(kept)} file(s) you changed: " + ", ".join(kept))
    lines.append("Project files (AGENTS.md, CLAUDE.md, PROGRESS.md, feature_list.json, docs/, scripts/harness.py, "
                 ".harness/) were not touched: they are project content now. Delete them by hand if you want.")
    return "\n".join(lines)


def install_global(dest: Path | None = None, dry_run: bool = False) -> str:
    """Skills, agents and the output style for every project (~/.claude). Hooks stay project-level."""
    dest = (dest or Path.home() / ".claude").resolve()
    man_p = dest / GLOBAL_MANIFEST
    try:
        manifest = json.loads(man_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    old = manifest.get("files", {}) if isinstance(manifest.get("files"), dict) else {}
    files, written, kept = {}, [], []
    for rel, content in scaffold.claude_payload("python"):
        sub = rel[len(".claude/"):]
        if not sub.startswith(GLOBAL_PARTS):
            continue
        target = dest / sub
        sha = scaffold._sha(content)
        if target.exists() and scaffold._sha_file(target) != sha and sub in old and scaffold._sha_file(target) != old[sub]:
            kept.append(sub)
            files[sub] = old[sub]
            continue
        files[sub] = sha
        if not (target.exists() and scaffold._sha_file(target) == sha):
            written.append(sub)
            if not dry_run:
                scaffold._write(target, content)
    if not dry_run:
        data = {"kit": "harness-kit", "version": VERSION, "scope": "global", "kit_root": scaffold.KIT_ROOT.as_posix(),
                "files": files}
        scaffold._write(man_p, scaffold._as_json(data))
    out = [f"{'Would install' if dry_run else 'Installed'} {len(written)} file(s) into {dest} (skills, agents, output style)."]
    if kept:
        out.append(f"Kept {len(kept)} file(s) you changed: " + ", ".join(kept))
    out.append("Hooks and the status line are per project: run the install command inside each project.")
    return "\n".join(out)


def _run(cmd: list[str], cwd: Path, payload: dict) -> tuple[int, str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd), "HARNESS_NO_TRACE": "1"}
    try:
        proc = subprocess.run(cmd, cwd=cwd, input=json.dumps(payload), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, proc.stdout + proc.stderr


def doctor(root: Path) -> tuple[int, str]:
    """Read-only checks. Returns (exit code, report): 0 healthy, 1 problems found."""
    root = root.resolve()
    rows: list[tuple[str, str, str]] = []

    def add(status: str, item: str, detail: str = "") -> None:
        rows.append((status, item, detail))

    manifest = scaffold._read_manifest(root)
    if not manifest:
        add("FAIL", "manifest", f"{scaffold.MANIFEST} missing — install: {kit_command('install', root)}")
    else:
        add("OK" if manifest.get("version") == VERSION else "WARN", "kit version",
            f"installed {manifest.get('version')}, kit {VERSION}" + ("" if manifest.get("version") == VERSION
                                                                       else " — re-run the install command"))
        files = manifest.get("files", {})
        missing = [r for r in files if not (root / r).exists()]
        changed = [r for r, sha in files.items() if (root / r).exists() and scaffold._sha_file(root / r) != sha]
        add("OK" if not missing else "FAIL", "installed files", f"{len(files)} tracked, {len(missing)} missing"
            + (f": {', '.join(missing[:5])}" if missing else ""))
        if changed:
            add("WARN", "changed files", f"{len(changed)} changed by you (kept on update): {', '.join(changed[:5])}")
    py = (manifest.get("python") if manifest else None) or "python"
    exe = shutil.which(py.strip('"').split()[0]) if not py.startswith('"') else py.strip('"')
    add("OK" if exe else "FAIL", "python for hooks", f"`{py}` -> {exe or 'not found on PATH'}")
    rt = root / "scripts" / "harness.py"
    if rt.is_file():
        same = rt.read_text(encoding="utf-8") == (Path(__file__).parent / "runtime.py").read_text(encoding="utf-8")
        add("OK" if same else "WARN", "runtime", "scripts/harness.py " + ("matches the kit" if same else
                                                                          "differs from the kit — re-run the install command"))
    else:
        add("FAIL", "runtime", "scripts/harness.py missing")
    settings_p = root / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        settings = None
        add("FAIL", "settings.json", f"unreadable: {exc}")
    if isinstance(settings, dict):
        hooks = settings.get("hooks", {}) if isinstance(settings.get("hooks"), dict) else {}
        missing_events = [ev for ev, _, _ in scaffold.HOOK_EVENTS
                          if not any(scaffold._is_harness_hook(h) for e in hooks.get(ev, []) if isinstance(e, dict)
                                     for h in e.get("hooks", []) if isinstance(e.get("hooks"), list))]
        add("OK" if not missing_events else "FAIL", "hooks registered",
            "all events" if not missing_events else "missing: " + ", ".join(missing_events))
        sl = settings.get("statusLine")
        add("OK" if scaffold._is_harness_statusline(sl) else "INFO", "status line",
            "harness status line" if scaffold._is_harness_statusline(sl) else "not the harness one (yours is kept)")
        deny = settings.get("permissions", {}).get("deny", []) if isinstance(settings.get("permissions"), dict) else []
        add("OK" if "Read(./.env)" in deny else "WARN", "secret protection",
            "Read(./.env) denied" if "Read(./.env)" in deny else "no deny rule for .env")
    claude_md = root / "CLAUDE.md"
    if claude_md.is_file():
        ok = scaffold.imports_agents_md(claude_md)
        add("OK" if ok else "WARN", "CLAUDE.md", "imports AGENTS.md" if ok else
            "does not import AGENTS.md: Claude Code never sees the harness rules — add `@AGENTS.md` at the top")
    agents_md = root / "AGENTS.md"
    if agents_md.is_file():
        ok = "scripts/harness.py" in agents_md.read_text(encoding="utf-8", errors="replace")
        add("OK" if ok else "WARN", "AGENTS.md", "routes to the harness runtime" if ok else
            "has no harness instructions: merge .harness/proposals/AGENTS.md into it")
    dispatcher = root / ".claude" / "hooks" / "harness_hook.py"
    if dispatcher.is_file() and exe:
        cmd = [sys.executable, str(dispatcher)]
        code, out = _run(cmd + ["statusline"], root, {"context_window": {"used_percentage": 10}})
        add("OK" if code == 0 and out.startswith("HK") else "FAIL", "status line smoke", out.strip()[:100])
        code, out = _run(cmd + ["session-start"], root, {"source": "resume", "session_id": "doctor"})
        try:
            ctx = json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
            ok = code == 0 and "Harness state" in ctx
        except (ValueError, KeyError, IndexError):
            ok = False
        add("OK" if ok else "FAIL", "session-start smoke", "context injected" if ok else out.strip()[:200])
    elif not dispatcher.is_file():
        add("FAIL", "hook dispatcher", ".claude/hooks/harness_hook.py missing")
    claude = shutil.which("claude")
    if claude:
        try:
            ver = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            ver = "?"
        add("INFO", "claude CLI", ver or "?")
    else:
        add("INFO", "claude CLI", "not on PATH (only needed for `harness.py loop`)")
    width = max(len(r[1]) for r in rows)
    report = "\n".join(f"[{s:4}] {i.ljust(width)}  {d}" for s, i, d in rows)
    bad = sum(1 for s, _, _ in rows if s == "FAIL")
    return (1 if bad else 0), report + ("\n\nHEALTHY" if not bad else f"\n\n{bad} problem(s) — see FAIL lines")
