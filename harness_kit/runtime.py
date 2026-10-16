#!/usr/bin/env python3
# File managed by harness-kit (updated via `harness upgrade`), not part of the project's code:
# ruff: noqa
# flake8: noqa
# mypy: ignore-errors
# pylint: skip-file
"""Harness runtime: the tool that operates the harness inside a project.

This file is standalone (Python 3.9+ standard library only) and is copied by
harness-kit into the project as `scripts/harness.py`. Agents and users run:

    python scripts/harness.py start            # clock in: read state, pick work
    python scripts/harness.py check [--full]   # verify: rules, docs, lint, typecheck, test (+build, e2e, regression)
    python scripts/harness.py status           # overview of feature list, VCR, last check
    python scripts/harness.py next             # activate the next feature (WIP=1)
    python scripts/harness.py verify F001      # pass-gate: run the verification command, record evidence
    python scripts/harness.py block F001 --reason "..."
    python scripts/harness.py add --id F010 --behavior "..." --verify "pytest tests/test_x.py"
    python scripts/harness.py validate         # validate the feature_list.json schema
    python scripts/harness.py docs [--strict]  # check AGENTS.md, broken links, unfilled placeholders, stale docs
    python scripts/harness.py rules            # run the architecture rules in .harness/rules.json
    python scripts/harness.py handoff          # update the auto-generated block in PROGRESS.md
    python scripts/harness.py exit-check       # check for a clean state before ending the session
    python scripts/harness.py trace [-n 20]    # show the harness activity log

Feature state may only be changed through this script (never edit `status`/`evidence` by hand).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import difflib
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VERSION = "2.3.1"
STATES = ("not_started", "active", "blocked", "passing")
STATUS_ALIASES = {"not_started": "not_started", "todo": "not_started", "planned": "not_started", "fail": "not_started",
                  "active": "active", "in_progress": "active", "blocked": "blocked",
                  "passing": "passing", "pass": "passing", "done": "passing", "true": "passing", "false": "not_started"}
FILL_MARKER = "HARNESS:FILL"
AUTO_START = "<!-- harness:auto:start -->"
AUTO_END = "<!-- harness:auto:end -->"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "coverage",
             "target", "bin", "obj", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "vendor", ".gradle"}

DEFAULT_CONFIG = {
    "version": 1,
    "project": "",
    "entry_file": "AGENTS.md",
    "progress_file": "PROGRESS.md",
    "feature_list": "feature_list.json",
    "rules_file": ".harness/rules.json",
    "max_entry_lines": 200,
    "step_timeout_sec": 1800,
    "commands": {"setup": [], "dev": [], "smoke": [], "lint": [], "typecheck": [], "test": [], "build": [], "e2e": []},
    "check": ["rules", "docs", "lint", "typecheck", "test"],
    "full_check": ["rules", "docs", "lint", "typecheck", "test", "build", "e2e", "regression"],
    "debug_patterns": {
        "error": [r"\bconsole\.log\(", r"^\s*debugger;?\s*$", r"\bbreakpoint\(\)", r"pdb\.set_trace\(",
                  r"^\s*import pdb", r"binding\.pry", r"\bvar_dump\(", r"\bdd\("],
        "warn": [r"System\.out\.println\(", r"\bTODO\b", r"\bFIXME\b", r"\bHACK\b"],
    },
    "stale_artifacts": ["*.tmp", "*.bak", "*.swp", "*~", "*.orig", "*.rej", "scratch.*", "debug.*",
                        "temp.*", "WIP.md", "IN_PROGRESS.md", "test-manual.*"],
    # "quick" or "full": which project-wide check `verify` runs after the feature's own command (lessons 09-10)
    "verify_check": "quick",
    # changes outside the active feature's `scope` globs: "warn" or "error" (lesson 07)
    "scope_drift": "warn",
    "scope_exempt": ["PROGRESS.md", "feature_list.json", "AGENTS.md", "CLAUDE.md", "docs/*", ".harness/*",
                     ".claude/*", "scripts/harness.py", "init.sh", "init.ps1"],
    "max_hard_constraints": 15,
    # globs skipped by the leftover-debug-code scan (e.g. tests that contain debug strings as fixtures)
    "debug_exempt": [],
    # interactive Claude Code hooks (v2): writes outside the active feature's scope -> "ask" | "deny" | "off"
    "scope_guard": "ask",
    # edits of instruction files (AGENTS.md, CLAUDE.md, .claude/agents|skills|rules) -> "ask" | "off"
    "instruction_guard": "ask",
    # a crash of the guard hook: "open" (allow, with a visible warning) or "closed" (deny the tool call)
    "guard_failure": "open",
    # verification commands that can never fail (echo, true, exit 0, `|| true`, `| tail`): "refuse" or "warn"
    "trivial_verification": "refuse",
    # edits that weaken the checker (lint/test/CI config; assertions removed or skips added in tests): "ask" | "off"
    "checker_guard": "ask",
    # `verify` needs an hk-reviewer APPROVE on the current change: "require" (Claude Code installs), "warn" or "off"
    "review_gate": "warn",
    # dispatcher events switched off for this project, e.g. ["prompt"]; switching off "guard" or "stop" is logged
    "hooks_off": [],
    "activity_max_lines": 2000,
    "trace_max_lines": 5000,
    "loop_timeout_sec": 3600,
    "loop_keep_runs": 30,
    "harness_version": "",
}
# keys a project config may hold although no default exists for them
CONFIG_EXTRA_KEYS = {"regression"}

LAYERS = ("instructions", "tools", "environment", "state", "feedback")


def atomic_write(p: Path, text: str) -> None:
    """Write through a temporary file and a rename (R16): a crash or a parallel hook never sees half a file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:  # Windows: another process holds the file open for a moment
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, p)


def rotate_jsonl(p: Path, keep: int) -> None:
    """Keep an append-only log bounded: past `keep` lines, keep the newest half (atomic rename)."""
    try:
        if p.stat().st_size <= keep * 160:  # cheap size test first
            return
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > keep:
            atomic_write(p, "\n".join(lines[-keep // 2:]) + "\n")
    except OSError:
        pass  # another process is rotating it: try again next time

CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".java", ".kt", ".go",
                 ".rs", ".cs", ".php", ".rb", ".swift", ".scala"}
MISSING_TOOL_RE =re.compile(r"No module named|command not found|is not recognized as an internal or external command|"
                             r"not found: |ENOENT|Cannot find module|could not determine executable")

STEP_HINTS = {
    "lint": "Fix the lint violations in your code. Do not disable rules or add ignores unless the reason is recorded in DECISIONS.md.",
    "typecheck": "Fix the type errors at the reported locations. Do not use any/type: ignore to work around them.",
    "test": "Run the test command on its own to see details. Fix the code, not the tests — unless a test is clearly wrong and you record why.",
    "build": "A broken build = not done. Read the first compile error and fix it first.",
    "e2e": "The end-to-end flow is broken: check the boundaries between components (paths, schemas, environment config).",
    "smoke": "The standard startup path does not run. The next session will not be able to start — fix it before ending.",
}


# ------------------------------------------------------------------ utils ---

def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def find_root(start: Path | None = None) -> Path:
    here = Path(__file__).resolve().parent
    if (here.parent / ".harness" / "config.json").exists():
        return here.parent
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".harness" / "config.json").exists():
            return p
    return cur


class Harness:
    def __init__(self, root: Path):
        self.root = root
        self.hdir = root / ".harness"
        cfg_path = self.hdir / "config.json"
        cfg = dict(DEFAULT_CONFIG)
        if cfg_path.exists():
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.update(loaded)
            cfg["commands"] = {**DEFAULT_CONFIG["commands"], **loaded.get("commands", {})}
        self.cfg = cfg

    # ---------------------------------------------------------- paths ---
    def path(self, key: str) -> Path:
        return self.root / self.cfg[key]

    # ---------------------------------------------------------- trace ---
    def trace(self, cmd: str, result: str, **extra) -> None:
        if os.environ.get("HARNESS_NO_TRACE") == "1":  # `doctor` smoke-tests the hooks without leaving a record
            return
        self.hdir.mkdir(exist_ok=True)
        entry = {"ts": now_iso(), "cmd": cmd, "result": result, **extra}
        p = self.hdir / "trace.jsonl"
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        rotate_jsonl(p, int(self.cfg.get("trace_max_lines", 5000)) if isinstance(getattr(self, "cfg", None), dict)
                     else 5000)

    # ------------------------------------------------------------ git ---
    def git(self, *args: str) -> str:
        try:
            out = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60)
            return out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def dirty_paths(self) -> list[str]:
        """Paths of `git status --porcelain`. Not through `git()`: its strip() removed the leading space of the first
        line, so ' M src/x.py' became 'M src/x.py' and `[3:]` read 'rc/x.py' — the Stop gate then missed the most
        common unverified change (found by the baseline-vs-harness experiment, 2026-10-16)."""
        try:  # -z: NUL-separated and unquoted, so non-ASCII and spaces survive (2.3.1)
            out = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"], cwd=self.root,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return []
        if out.returncode != 0:
            return []
        paths, fields, i = [], out.stdout.split("\0"), 0
        while i < len(fields):  # "XY path" entries; a rename/copy is followed by one more field: the old path
            entry = fields[i]
            i += 1
            if len(entry) > 3 and entry[2] == " ":
                if entry[0] in "RC":
                    i += 1
                rel = entry[3:]
                if rel and not is_generated(rel):
                    paths.append(rel)
        return paths

    def head(self) -> str:
        return self.git("rev-parse", "--short", "HEAD")

    def no_commit_label(self) -> str:
        return "no commits yet" if self.git("rev-parse", "--is-inside-work-tree") == "true" else "no git"

    # ------------------------------------------------------- features ---
    def load_features(self) -> list[dict]:
        """Read the feature list. Accepts a plain array or {"features": [...]} (the course template),
        a `status` or `state` field, and status aliases (in_progress, done, pass, passes)."""
        p = self.path("feature_list")
        self._container: dict | None = None
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            self._container = data
            data = data.get("features", [])
        for f in data:
            raw = f.get("status", f.get("state"))
            if raw is None and "passes" in f:
                raw = "passing" if f.get("passes") else "not_started"
            f["status"] = STATUS_ALIASES.get(str(raw).lower().replace("-", "_"), raw) if raw is not None else None
            f.pop("state", None)
            if not f.get("behavior"):
                f["behavior"] = f.get("user_visible_behavior") or f.get("description") or f.get("title") or f.get("name")
        return data

    def save_features(self, features: list[dict]) -> None:
        p = self.path("feature_list")
        if getattr(self, "_container", None) is not None:
            self._container["features"] = features
            self._container["last_updated"] = _dt.date.today().isoformat()
            payload = self._container
        else:
            payload = features
        atomic_write(p, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def find_feature(features: list[dict], fid: str) -> dict:
        for f in features:
            if f.get("id") == fid:
                return f
        raise SystemExit(f"ERROR: no feature '{fid}' in the feature list.\nFIX: run `status` to see the list of ids.")

    # ------------------------------------------------------- commands ---
    def run_shell(self, cmd: str, cwd: str = ".", timeout: int | None = None) -> tuple[int, str, float]:
        if os.name == "nt" and cmd.startswith("./"):
            cmd = ".\\" + cmd[2:]
        # re-invoke this runtime with the exact interpreter that is running (python/python3/py differ across machines)
        cmd = re.sub(r"(^|&&\s*|;\s*)python3? (scripts/harness\.py)", lambda m: f'{m.group(1)}"{sys.executable}" {m.group(2)}', cmd)
        env = dict(os.environ)
        env.setdefault("CI", "true")  # avoid watch mode / interactive prompts
        start = time.time()
        try:
            proc = subprocess.run(cmd, shell=True, cwd=self.root / cwd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env,
                                  timeout=timeout or self.cfg["step_timeout_sec"])
            output = (proc.stdout or "") + (proc.stderr or "")
            code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = f"{exc.stdout or ''}{exc.stderr or ''}\nTIMEOUT after {exc.timeout}s"
            code = 124
        except OSError as exc:
            output, code = str(exc), 127
        return code, output, time.time() - start


def tail(text: str, n: int = 40) -> str:
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-n:])


# ------------------------------------------------------------------ rules ---

def run_rules(h: Harness) -> tuple[bool, list[str]]:
    rp = h.root / h.cfg["rules_file"]
    if not rp.exists():
        return True, ["(no architecture rules yet — add them to .harness/rules.json when a review issue recurs)"]
    rules = json.loads(rp.read_text(encoding="utf-8"))
    if isinstance(rules, dict):
        rules = rules.get("rules", [])
    msgs: list[str] = []
    ok = True
    files = [p for p in _walk(h.root)]
    for rule in rules:
        if rule.get("disabled"):
            continue
        try:
            pattern = re.compile(rule["pattern"], re.M)
        except (KeyError, re.error) as exc:
            msgs.append(f"[{rule.get('id', '?')}] invalid rule: {exc}")
            ok = False
            continue
        include = rule.get("include", ["*"])
        exclude = rule.get("exclude", [])
        severity = rule.get("severity", "error")
        hits = 0
        for p in files:
            rel = p.relative_to(h.root).as_posix()
            if not any(fnmatch.fnmatch(rel, g) for g in include) or any(fnmatch.fnmatch(rel, g) for g in exclude):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits += 1
                if hits <= 20:
                    msgs.append(f"ERROR [{rule.get('id')}]: {rule.get('description', '')} at {rel}:{line}\n"
                                f"  WHY: {rule.get('why', '')}\n  FIX: {rule.get('fix', '')}")
        if hits and severity == "error":
            ok = False
    return ok, msgs


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            yield Path(dirpath) / f


# ------------------------------------------------------------------- docs ---

LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)|`([\w./-]+\.(?:md|json|sh|ps1|py|txt|toml|yaml|yml))`")


def run_docs(h: Harness, strict: bool = False) -> tuple[bool, list[str]]:
    msgs: list[str] = []
    ok = True
    entry = h.path("entry_file")
    if not entry.exists():
        return False, [f"ERROR: {h.cfg['entry_file']} is missing.\n  WHY: the agent has no map of the project.\n"
                       f"  FIX: run `harness retrofit` or write an entry file of ~100 lines."]
    lines = entry.read_text(encoding="utf-8").splitlines()
    limit = int(h.cfg["max_entry_lines"])
    if len(lines) > limit:
        ok = False
        msgs.append(f"ERROR: {entry.name} is {len(lines)} lines long (> {limit}).\n  WHY: a huge instruction file eats context"
                    f" and rules in the middle get ignored.\n  FIX: move detailed content to docs/<topic>.md and keep only links.")
    elif len(lines) > 120:
        msgs.append(f"WARNING: {entry.name} is {len(lines)} lines long — aim for ~100 lines.")
    # hard constraints: at most N, each with a source (lesson 04)
    section = re.search(r"^## Hard constraints.*?$(.*?)(?=^## |\Z)", "\n".join(lines), re.M | re.S | re.I)
    if section:
        bullets = [b for b in re.findall(r"^\s*- (.+)$", section.group(1), re.M)]
        cap = int(h.cfg.get("max_hard_constraints", 15))
        if len(bullets) > cap:
            ok = False
            msgs.append(f"ERROR: {entry.name} has {len(bullets)} hard constraints (> {cap}).\n"
                        f"  WHY: when everything is a hard rule, nothing is; priorities blur.\n"
                        f"  FIX: keep only non-negotiable rules; move guidelines to docs/<topic>.md.")
        for b in bullets:
            if not re.search(r"\bsource:", b, re.I):
                msgs.append(f"{'ERROR' if strict else 'WARNING'}: hard constraint without a source: \"{b[:70]}\"\n"
                            f"  FIX: append (source: <incident/decision>; expires when: <condition>).")
                if strict:
                    ok = False
    # broken links in the harness files
    md_files = [entry, h.path("progress_file")] + sorted((h.root / "docs").rglob("*.md")) if (h.root / "docs").exists() \
        else [entry, h.path("progress_file")]
    fills = 0
    for md in md_files:
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        fills_here = text.count(FILL_MARKER)
        fills += fills_here
        if fills_here:
            msgs.append(f"{'ERROR' if strict else 'WARNING'}: {md.relative_to(h.root).as_posix()} still has {fills_here} "
                        f"unfilled `{FILL_MARKER}` placeholder(s).")
        for m in LINK_RE.finditer(text):
            target = m.group(1) or m.group(2)
            if not target or target.startswith(("http://", "https://", "mailto:")) or "*" in target or "<" in target:
                continue
            cand = [(md.parent / target), (h.root / target)]
            if not any(c.exists() for c in cand) and (m.group(1) or "/" in target):
                ok = False
                msgs.append(f"ERROR: broken link `{target}` in {md.relative_to(h.root).as_posix()}.\n"
                            f"  WHY: stale docs are worse than no docs.\n"
                            f"  FIX: correct the path or remove the line.")
    if fills and strict:
        ok = False
    # stale module docs: the module has many commits since the doc was last edited
    for doc in list(h.root.glob("*/**/ARCHITECTURE.md")) + list(h.root.glob("*/**/CONSTRAINTS.md")):
        if any(part in SKIP_DIRS for part in doc.parts) or doc.parent == h.root / "docs":
            continue
        last = h.git("log", "-1", "--format=%H", "--", doc.relative_to(h.root).as_posix())
        if not last:
            continue
        n = h.git("rev-list", "--count", f"{last}..HEAD", "--", doc.parent.relative_to(h.root).as_posix())
        if n.isdigit() and int(n) > 10:
            msgs.append(f"WARNING: {doc.relative_to(h.root).as_posix()} may be stale ({n} commits to the module "
                        f"since the doc was last updated).")
    return ok, msgs


# ------------------------------------------------------------------ check ---

def run_regression(h: Harness) -> tuple[bool, list[str]]:
    msgs, ok = [], True
    feats, cache, rebased = h.load_features(), {}, []
    for f in feats:
        if f.get("status") == "passing" and isinstance(f.get("verification"), str) and f["verification"].strip():
            code, out, _ = h.run_shell(f["verification"], f.get("cwd", "."))
            if code != 0:
                ok = False
                msgs.append(f"ERROR: regression in {f['id']} ({f.get('behavior', '')}) — a verification command that used to pass now fails.\n"
                            f"  FIX: fix the regression before doing anything else.\n{tail(out, 15)}")
                continue
            ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else None
            fp = scope_fingerprint(h, f, cache) if ev is not None else None
            if fp and ev.get("scope_sha256") != fp:  # passed again on the changed scope: no longer stale
                ev["scope_sha256"], ev["reverified_at"] = fp, now_iso()
                rebased.append(f["id"])
    if rebased:
        h.save_features(feats)
        msgs.append(f"re-verified on the current code: {', '.join(rebased)}")
    return ok, msgs


def cmd_check(h: Harness, args) -> int:
    steps = args.only or (h.cfg["full_check"] if args.full else h.cfg["check"])
    results = []
    overall = True
    print(f"== harness check ({'full' if args.full else 'quick'}) @ {h.head() or h.no_commit_label()} ==")
    for step in steps:
        t0 = time.time()
        if step == "rules":
            ok, msgs = run_rules(h)
        elif step == "docs":
            ok, msgs = run_docs(h)
        elif step == "regression":
            ok, msgs = run_regression(h)
        else:
            cmds = h.cfg["commands"].get(step, [])
            if not cmds and args.only:
                print(f"[FAIL] {step}: requested but commands.{step} is not configured in .harness/config.json")
                results.append({"step": step, "status": "fail", "messages": ["not configured"]})
                overall = False
                continue
            if not cmds:
                print(f"[SKIP] {step}: not configured in .harness/config.json")
                if step == "test":
                    print("       WARNING: no test command = the agent grades its own 'done'. Adding tests is a priority.")
                results.append({"step": step, "status": "skip"})
                continue
            ok, msgs = True, []
            for c in cmds:
                code, out, dur = h.run_shell(c["cmd"], c.get("cwd", "."))
                if code != 0:
                    ok = False
                    hint = STEP_HINTS.get(step, "Read the output below.")
                    if MISSING_TOOL_RE.search(out):
                        hint = ("The tool is not available in the environment — this is an ENVIRONMENT error, not a code error. "
                                "Run `python scripts/harness.py setup`; if it is still missing, add the tool to the project's "
                                "dev dependencies or fix the command in .harness/config.json.")
                    msgs.append(f"ERROR: `{c['cmd']}` (cwd={c.get('cwd', '.')}) exited with code {code} after {dur:.1f}s.\n"
                                f"  FIX: {hint}\n{tail(out)}")
        dur = time.time() - t0
        status = "pass" if ok else "fail"
        overall &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {step} ({dur:.1f}s)")
        for m in msgs:
            print("   " + m.replace("\n", "\n   "))
        results.append({"step": step, "status": status, "duration_s": round(dur, 1),
                        "messages": msgs[:10]})
    record = {"ts": now_iso(), "commit": h.head(), "mode": "full" if args.full else "quick",
              "ok": overall, "steps": results}
    h.hdir.mkdir(exist_ok=True)
    atomic_write(h.hdir / "last-check.json", json.dumps(record, ensure_ascii=False, indent=2))
    h.trace("check", "pass" if overall else "fail", mode=record["mode"],
            steps={r["step"]: r["status"] for r in results})
    print("== RESULT:", "PASS" if overall else "FAIL", "==")
    return 0 if overall else 1


# --------------------------------------------------------------- features ---

def validate_features(h: Harness) -> list[str]:
    errs: list[str] = []
    try:
        feats = h.load_features()
    except ValueError as exc:
        return [f"feature list is not valid JSON: {exc}"]
    ids = set()
    for i, f in enumerate(feats):
        where = f"item #{i} ({f.get('id', '?')})"
        for key in ("id", "behavior", "status"):
            if not isinstance(f.get(key), str) or not f.get(key):
                errs.append(f"{where}: missing string field '{key}'")
        if f.get("id") in ids:
            errs.append(f"{where}: duplicate id")
        ids.add(f.get("id"))
        if f.get("status") not in STATES:
            errs.append(f"{where}: status '{f.get('status')}' is not one of {STATES}")
        if not f.get("verification"):
            errs.append(f"{where}: missing 'verification' (verification command) — the feature has no proof of completion")
        elif isinstance(f.get("verification"), str) and f.get("status") != "passing" and \
                h.cfg.get("trivial_verification", "refuse") == "refuse" and trivial_verification(f["verification"]):
            errs.append(f"{where}: verification `{f['verification']}` cannot fail "
                        f"({trivial_verification(f['verification'])})")
        if f.get("status") == "passing" and not f.get("evidence"):
            errs.append(f"{where}: passing but has no evidence — only `verify` may mark a feature passing")
    for f in feats:
        for dep in f.get("depends_on", []) or []:
            if dep not in ids:
                errs.append(f"{f.get('id')}: depends_on '{dep}' does not exist")
    actives = [f["id"] for f in feats if f.get("status") == "active"]
    if len(actives) > 1:
        errs.append(f"WIP violation: {len(actives)} features are active ({', '.join(actives)}). Only 1 is allowed.")
    return errs


def stats(feats: list[dict]) -> dict:
    s = {k: sum(1 for f in feats if f.get("status") == k) for k in STATES}
    activated = s["passing"] + s["active"] + s["blocked"]
    s["total"] = len(feats)
    s["vcr"] = round(s["passing"] / activated, 2) if activated else 1.0
    return s


def cmd_validate(h: Harness, args) -> int:
    errs = validate_features(h)
    try:
        raw_cfg = json.loads((h.hdir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw_cfg = {}
    errs += [f"config: {e}" for e in config_problems(raw_cfg if isinstance(raw_cfg, dict) else {})]
    for req in args.require or []:
        cat, _, n = req.partition(":")
        have = sum(1 for f in h.load_features() if f.get("category") == cat)
        if have < int(n or 1):
            errs.append(f"need at least {n} feature(s) with category '{cat}', found {have}.\n  FIX: use `add --category {cat}` "
                        f"with a --verify command that measures the goal.")
    for e in errs:
        print("ERROR:", e)
    print("feature list is valid." if not errs else f"{len(errs)} error(s).")
    return 0 if not errs else 1


def cmd_status(h: Harness, args) -> int:
    feats = h.load_features()
    s = stats(feats)
    print(f"Project: {h.cfg.get('project') or h.root.name}   commit: {h.head() or '-'}   "
          f"branch: {h.git('rev-parse', '--abbrev-ref', 'HEAD') or '-'}")
    print(f"Features: {s['passing']}/{s['total']} passing | active {s['active']} | blocked {s['blocked']} | "
          f"not_started {s['not_started']} | VCR {s['vcr']}")
    stale = stale_features(h, feats)
    if stale:
        print(_stale_line(stale))
    for f in feats:
        if f.get("status") in ("active", "blocked"):
            extra = f" — reason: {f.get('blocked_reason')}" if f.get("status") == "blocked" else ""
            print(f"  [{f['status'].upper()}] {f['id']}: {f.get('behavior')}{extra}")
            print(f"      verify: {f.get('verification')}")
    lc = h.hdir / "last-check.json"
    if lc.exists():
        rec = json.loads(lc.read_text(encoding="utf-8"))
        print(f"Last check: {'PASS' if rec['ok'] else 'FAIL'} ({rec['mode']}) at {rec['ts']}"
              + (f" @ {rec['commit']}" if rec.get("commit") else ""))
    else:
        print("Last check: never run — run `python scripts/harness.py check`.")
    errs = validate_features(h)
    if errs:
        print(f"WARNING: feature list has {len(errs)} error(s) — run `validate`.")
    return 0


def _deps_ok(f: dict, feats: list[dict]) -> bool:
    passing = {x["id"] for x in feats if x.get("status") == "passing"}
    return all(d in passing for d in f.get("depends_on", []) or [])


def cmd_next(h: Harness, args) -> int:
    feats = h.load_features()
    active = [f for f in feats if f.get("status") == "active"]
    if active:
        f = active[0]
        print(f"A feature is already active (WIP=1): {f['id']} — {f.get('behavior')}\n  verify: {f.get('verification')}\n"
              f"Finish it (`verify {f['id']}`) or `block {f['id']} --reason ...` before starting new work.")
        return 0
    s = stats(feats)
    if s["vcr"] < 1.0 and not args.force:
        print(f"BLOCKED: VCR = {s['vcr']} < 1.0 (some features are blocked). Resolve the blockers or document them in PROGRESS.md, "
              f"then run `next --force` if a blocker is outside your control.")
        return 1
    target = None
    if args.id:
        target = h.find_feature(feats, args.id)
        if target.get("status") != "not_started":
            raise SystemExit(f"ERROR: {args.id} is {target.get('status')}; only not_started features can be activated.")
    else:
        for f in sorted(feats, key=lambda x: (x.get("priority", 100), feats.index(x))):
            if f.get("status") == "not_started" and _deps_ok(f, feats):
                target = f
                break
    if not target:
        print("No eligible not_started feature left (all done or waiting on dependencies).")
        return 0
    if not _deps_ok(target, feats):
        raise SystemExit(f"ERROR: {target['id']} depends on {target.get('depends_on')}, which are not passing yet.")
    target["status"] = "active"
    target["activated_at"] = now_iso()
    target["activated_commit"] = h.git("rev-parse", "HEAD") or ""
    h.save_features(feats)
    h.trace("next", "activated", feature=target["id"])
    print(f"ACTIVE: {target['id']} — {target.get('behavior')}\n  verify: {target.get('verification')}")
    if target.get("out_of_scope"):
        print(f"  DO NOT touch: {target['out_of_scope']}")
    print("Work on this feature only. When done, run `verify " + target["id"] + "`.")
    return 0


GENERATED_SUFFIXES = (".pyc", ".pyo", ".class")


def is_generated(rel: str) -> bool:
    """Build/test by-products (bytecode, caches): never someone's work, never a scope violation — even when an earlier
    commit tracked them (found in experiment 4: committed __pycache__ blocked correct features)."""
    return rel.endswith(GENERATED_SUFFIXES) or any(part in SKIP_DIRS for part in rel.split("/")[:-1])


def changed_files(h: Harness, base: str) -> list[str]:
    """Files changed since `base` (committed, staged, unstaged) plus untracked files."""
    names = h.git("diff", "--name-only", base or "HEAD").splitlines()
    for rel in h.git("ls-files", "--others", "--exclude-standard").splitlines():
        if not any(part in SKIP_DIRS or part.startswith(".") for part in rel.split("/")[:-1]):
            names.append(rel)
    return sorted(set(n for n in names if n and not is_generated(n)))


def scope_drift(h: Harness, f: dict) -> list[str]:
    """Changed files outside the feature's declared `scope` globs (empty when no scope is declared)."""
    scope = f.get("scope") or []
    if isinstance(scope, str):
        scope = [scope]
    if not scope:
        return []
    allowed = list(scope) + list(h.cfg.get("scope_exempt", []))
    return [n for n in changed_files(h, f.get("activated_commit", ""))
            if not any(fnmatch.fnmatch(n, g) for g in allowed)]


# A verification command proves nothing when it cannot fail. Deterministic patterns only (a guardrail, not a proof:
# `python -c "assert True"` still passes); found in the survey of open-source harnesses, 2026-10-16.
# neutral: changes directory or environment only. An assignment is neutral only when the whole segment is assignments:
# `CI=true npm test` runs a check (2.3.1, independent review: the unanchored pattern made it "neutral").
_NEUTRAL_SEG = re.compile(r"^(?:(?:cd|pushd|popd|set-location|sl)(?:\s.*)?|set\s+-[a-z]+.*|export\s+\w+=\S*"
                          r"|(?:\w+=\S*\s*)+)$", re.I)
_ENV_PREFIX = re.compile(r"^(?:\w+=\S*\s+)+")
_NESTED_PAYLOAD = re.compile(r"^(?:(?:ba|z|da)?sh|cmd|pwsh|powershell)(?:\.exe)?(?:\s+-\w+)*?\s+(?:-\w*c|/c|-Command)\s+"
                             r"([\"'])(.*)\1$", re.I | re.S)
_NOOP_CODE = r"(?:pass|print\([^)]*\)|console\.log\([^)]*\))"
_TRIVIAL_SEG = re.compile(
    r"^(?:true|:|exit(?:\s+/b)?(?:\s+0)?|\$true|(?:echo|printf|write-host|write-output|rem)(?:\s.*)?"
    r"|cmd(?:\.exe)?\s+/c\s+exit(?:\s+/b)?\s+0"
    r"|[\"']?(?:[^\s\"']*[\\/])?(?:python3?|py|node)(?:\.exe)?[\"']?(?:\s+-[^\sce]\S*)*\s+-[ce]\s+"
    r"(?P<q>[\"']?)\s*" + _NOOP_CODE + r"?\s*(?:;\s*" + _NOOP_CODE + r"\s*)*(?P=q))$", re.I)
_MASKING_PIPE = re.compile(r"\|\s*(tee|tail|head|cat|less|more|sort|uniq|out-host|out-string|select-object)\b", re.I)


def _split_segments(cmd: str) -> list[tuple[str, str]]:
    """(separator before, segment) pairs, splitting on && || ; | & and newlines outside quotes (a single `&` runs the
    next command anyway: in sequence on cmd.exe, in the background in sh; `2>&1`, `&>` are redirections). Grouping
    parentheses are dropped."""
    out, buf, sep, quote, i = [], "", "", "", 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            quote = "" if c == quote else quote
            buf += c
        elif c in "'\"":
            quote, buf = c, buf + c
        elif cmd.startswith(("&&", "||"), i):
            out.append((sep, buf.strip()))
            sep, buf = cmd[i:i + 2], ""
            i += 1
        elif c in ";|\n" or (c == "&" and not buf.endswith(">") and not cmd.startswith("&>", i)):
            out.append((sep, buf.strip()))
            sep, buf = ("\n" if c == "\n" else c), ""
        else:
            buf += c
        i += 1
    out.append((sep, buf.strip()))
    return [(s, seg.strip("() \t")) for s, seg in out if seg.strip("() \t")]


def _is_trivial_seg(seg: str) -> bool:
    return bool(_TRIVIAL_SEG.match(_ENV_PREFIX.sub("", seg) or seg))


def trivial_verification(cmd: str) -> str | None:
    """Why `cmd` can pass without checking anything, or None. Catches placeholders (only echo/true/exit 0/`python -c
    pass`), failures masked by `|| true`, by a trailing `; exit 0` / `; echo` / `& exit 0` (the last command's exit code
    is the result), the same inside `sh -c '…'`, and exit codes swallowed by a pipe into tail/tee."""
    segs = _split_segments(cmd.strip())
    if not segs:
        return "it is empty"
    real = [seg for _, seg in segs if not _NEUTRAL_SEG.match(seg) and not _is_trivial_seg(seg)]
    if not real:
        return "it only prints, changes directory or exits 0, so it passes whatever the code does"
    # `set -e` at the top level stops at the failing check, so a trailing echo cannot mask it. Not PowerShell's
    # $ErrorActionPreference: it does not stop on a native command's exit code (2.3.1, review round 3).
    errexit = any(re.match(r"set\s+(?:-\w*e\w*|-o\s+errexit)\b", seg) for _, seg in segs[:-1])
    for k, (sep, seg) in enumerate(segs):
        if sep == "||" and _is_trivial_seg(seg):
            return f"`|| {seg}` turns every failure into a pass"
        if k == len(segs) - 1 and sep in (";", "\n", "&") and _is_trivial_seg(seg) and not (errexit and sep != "&"):
            return f"the trailing `{seg}` replaces the exit code of the check before it"
        nested = _NESTED_PAYLOAD.match(seg)
        if nested:
            inner = trivial_verification(nested.group(2))
            if inner:
                return f"inside `{seg[:60]}`: {inner}"
    if _MASKING_PIPE.search(cmd) and "pipefail" not in cmd:
        return "a pipe into a pager or filter (tail, tee, head...) returns that program's exit code, not the check's"
    return None


def _trivial_problem(h: Harness, fid: str, ver: str) -> str | None:
    why = trivial_verification(ver)
    if not why:
        return None
    return (f"{fid}'s verification command `{ver}` cannot fail: {why}.\n  WHY: a check that cannot fail proves nothing "
            f"(the pass-gate would accept any code).\n  FIX: use a command that exits non-zero when the behavior is wrong "
            f"— the tests or script for this feature, without `|| true` or a pipe into tail/tee "
            f"(`set -o pipefail` keeps the exit code).")


def _assertion_free_tests(h: Harness, ver: str, cwd: str) -> list[str]:
    """Test files named by the command that contain no assertion at all (a warning: the heuristic can miss styles)."""
    out = []
    for tok in re.findall(r"[\w./\\-]+", ver):
        rel = tok.replace("\\", "/")
        if not TEST_FILE_RE.search(rel):
            continue
        p = (h.root / cwd / rel)
        if p.is_file() and not re.search(ASSERT_RE, p.read_text(encoding="utf-8", errors="replace")):
            out.append(rel)
    return out


def scope_fingerprint(h: Harness, f: dict, cache: dict) -> str | None:
    """sha256 over the feature's scope files (path + content; harness bookkeeping in `scope_exempt` left out), or None
    without a scope or a git repository. `cache` is shared across features within one call: each file is hashed once.
    A content hash, not a commit: the feature's own commit lands after `verify`, and must not make it look changed."""
    globs = _scope_globs(f)
    if not globs:
        return None
    files = cache.get("\0files")
    if files is None:
        names = h.git("ls-files", "-co", "--exclude-standard").splitlines()
        exempt = list(h.cfg.get("scope_exempt", []))
        files = cache["\0files"] = sorted({n for n in names if n and not is_generated(n) and (h.root / n).is_file()
                                           and not any(fnmatch.fnmatch(n, g) for g in exempt)})
    if not files:
        return None
    digest = hashlib.sha256()
    for rel in files:
        if any(fnmatch.fnmatch(rel, g) for g in globs):
            if rel not in cache:
                try:
                    cache[rel] = hashlib.sha256((h.root / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                except OSError:
                    cache[rel] = "unreadable"
            digest.update(f"{rel}\0{cache[rel]}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


def stale_features(h: Harness, feats: list[dict] | None = None) -> list[str]:
    """Passing features whose scope files changed since their verification last passed (`verify` or `check --full`)."""
    cache: dict = {}
    out = []
    for f in feats if feats is not None else h.load_features():
        ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
        if f.get("status") == "passing" and ev.get("scope_sha256"):
            if scope_fingerprint(h, f, cache) not in (None, ev["scope_sha256"]):
                out.append(f["id"])
    return out


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's empty tree: the base when a feature has no commit
TEST_ARTEFACTS = (".coverage", "coverage.xml", "junit*.xml", "*.log", ".pytest_cache/*", "htmlcov/*", "nosetests.xml")


def review_fingerprint(h: Harness, f: dict) -> str:
    """Content hash of what the feature changed: files changed since its activation commit (committed or not, so a
    commit between review and verify changes nothing). Every changed *tracked* file counts — a test or conftest edited
    outside the scope after the review voids it (2.3.1 review, round 2); *untracked* files count only inside the scope
    (dot-folders included), or all of them without a scope. Harness bookkeeping (`scope_exempt`), generated files and
    test artefacts (`.coverage`, `junit.xml`…) never count, so running the tests after a review does not void it."""
    exempt = list(h.cfg.get("scope_exempt", []))
    scope = _scope_globs(f)
    base = f.get("activated_commit") or EMPTY_TREE
    tracked = set(h.git("-c", "core.quotepath=false", "diff", "--name-only", base).splitlines())
    untracked = set(h.git("-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard").splitlines())
    digest = hashlib.sha256()
    for rel in sorted(n for n in tracked | untracked if n and not is_generated(n)):
        if any(fnmatch.fnmatch(rel, g) for g in exempt) or any(fnmatch.fnmatch(rel, g) or
                                                               fnmatch.fnmatch(rel.rsplit("/", 1)[-1], g)
                                                               for g in TEST_ARTEFACTS):
            continue
        if rel not in tracked and scope and not any(fnmatch.fnmatch(rel, g) for g in scope):
            continue
        p = h.root / rel
        try:
            blob = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if p.is_file() else "deleted"
        except OSError:
            blob = "unreadable"
        digest.update(f"{rel}\0{blob}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


VERDICTS = {"APPROVE": "APPROVE", "PASS": "APPROVE", "REJECT": "REJECT", "FAIL": "REJECT", "UNCERTAIN": "UNCERTAIN"}


def parse_verdict(text: str) -> str:
    """The reviewer's verdict: the last `VERDICT: APPROVE|REJECT|UNCERTAIN` line (PASS/FAIL accepted), else the line
    under a `## Verdict` heading. No readable verdict is UNCERTAIN — never an approval."""
    lines = [l.strip().strip("*`_ ") for l in str(text or "").splitlines()]
    for line in reversed(lines):
        m = re.match(r"^verdict\W*(approve|reject|uncertain|pass|fail)\b", line, re.I)
        if m and "|" not in line:
            return VERDICTS[m.group(1).upper()]
    for i, line in enumerate(lines):
        if re.match(r"^#+\s*verdict\s*$", line, re.I):
            nxt = next((l for l in lines[i + 1:] if l), "")
            m = re.match(r"^(approve|reject|uncertain|pass|fail)\b", nxt, re.I)
            if m and "|" not in nxt:
                return VERDICTS[m.group(1).upper()]
    return "UNCERTAIN"


def record_review(h: Harness, f: dict | None, verdict: str, source: str, **extra) -> dict:
    rec = {"ts": now_iso(), "epoch": time.time(), "feature": f["id"] if f else None, "verdict": verdict,
           "fingerprint": review_fingerprint(h, f) if f else None, "source": source}
    rec.update(extra)  # `fingerprint` in extra: the content a review saw, when that is not the current content
    h.hdir.mkdir(exist_ok=True)
    with (h.hdir / "reviews.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    h.trace("review", verdict, feature=rec["feature"], source=source)
    return rec


def _read_jsonl(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def review_status(h: Harness, f: dict) -> tuple[bool, str]:
    """(approved for the current content, explanation)."""
    every = _read_jsonl(h.hdir / "reviews.jsonl")
    launches = {r.get("id") for r in every if r.get("kind") == "start"}
    recs = [r for r in every if r.get("feature") == f["id"] and r.get("kind") != "start"]
    if not recs:
        return False, f"no hk-reviewer verdict has been recorded for {f['id']}"
    fp = review_fingerprint(h, f)
    current = [r for r in recs if r.get("fingerprint") == fp]

    def backed(r: dict) -> bool:
        """An APPROVE written by the SubagentStop hook names its recorded launch; one written by `loop` names its run
        (2.3.1, review round 3: a record without either was not written by the gate, e.g. by a script)."""
        if r.get("source") == "loop reviewer":
            return bool(r.get("run")) and (h.hdir / "runs" / str(r["run"])).is_dir()
        return bool(r.get("start")) and r.get("start") in launches
    if current and current[-1].get("verdict") == "APPROVE" and backed(current[-1]):
        return True, f"approved by {current[-1].get('source', 'a reviewer')} at {current[-1].get('ts')}"
    if current and current[-1].get("verdict") == "APPROVE":
        return False, "the latest APPROVE is not backed by a recorded hk-reviewer launch or loop run"
    if current:
        note = f" ({current[-1]['note']})" if current[-1].get("note") else ""
        return False, f"the latest review of the current change is {current[-1].get('verdict')}{note}"
    note = f"; {recs[-1]['note']}" if recs[-1].get("note") else ""
    return False, (f"the code changed after the last review ({recs[-1].get('verdict')} at {recs[-1].get('ts')}{note}): "
                   f"a review covers only the exact content it saw")


def record_incident(h: Harness, kind: str, detail: str, **extra) -> None:
    """A gate that was bypassed or exhausted: shown at the next session start (V020). The same incident in the same
    session is recorded once (a Stop gate that lets every turn end would otherwise log it per turn)."""
    h.hdir.mkdir(exist_ok=True)
    rec = {"ts": now_iso(), "epoch": time.time(), "kind": kind, "detail": detail[:500], **extra}
    last = (_read_jsonl(h.hdir / "incidents.jsonl") or [{}])[-1]
    if all(last.get(k) == rec.get(k) for k in ("kind", "detail", "session")) and rec.get("session") is not None:
        return
    with (h.hdir / "incidents.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    h.trace("incident", kind, detail=detail[:200])


def _review_gate(h: Harness, f: dict, args) -> bool:
    """True when `verify` may continue. `review_gate`: require | warn | off; `--no-review <reason>` overrides
    `require` and is recorded as an incident."""
    mode = h.cfg.get("review_gate", "warn")
    if mode == "off":
        return True
    ok, why = review_status(h, f)
    if ok:
        print(f"== review: {why}")
        return True
    reason = getattr(args, "no_review", None)
    if reason:
        record_incident(h, "no-review", f"verify {f['id']} without an approved review: {reason}", feature=f["id"])
        print(f"WARNING: {why}; verifying without it (--no-review: {reason}) — recorded in .harness/incidents.jsonl.")
        return True
    if mode == "warn":
        print(f"WARNING: {why}. Run the hk-reviewer subagent on the change before `verify` "
              f"(review_gate: warn — `require` makes this an error).")
        return True
    h.trace("verify", "fail", feature=f["id"], reason="no approved review")
    print(f"ERROR: {why}.\n  WHY: done means an independent review approved exactly this change (maker–checker).\n"
          f"  FIX: run the hk-reviewer subagent on {f['id']} and fix what it reports until it ends with "
          f"`VERDICT: APPROVE`, then verify again. Only if a review is impossible: `verify {f['id']} --no-review "
          f"\"<reason>\"` (recorded as an incident).")
    return False


def _stale_line(stale: list[str]) -> str:
    more = f" (+{len(stale) - 8} more)" if len(stale) > 8 else ""
    return (f"Stale: {len(stale)} passing feature(s) changed since they last passed — {', '.join(stale[:8])}{more}; "
            f"`check --full` re-runs their verifications")


def _require_verifiable(f: dict, h: Harness | None = None) -> str:
    """The verification command of an active feature; anything else is a domain error."""
    if f.get("status") != "active":
        raise SystemExit(f"ERROR: {f['id']} is {f.get('status')}. Only an active feature can be verified (run `next --id {f['id']}`).")
    ver = f.get("verification")
    if not isinstance(ver, str) or not ver.strip():
        raise SystemExit(f"ERROR: {f['id']} has no automated verification command.\n  FIX: add a 'verification' field with a "
                         f"shell command that exits 0 when the behavior is correct (test, e2e script, curl...).")
    if h is not None:
        problem = _trivial_problem(h, f["id"], ver)
        if problem and h.cfg.get("trivial_verification", "refuse") == "refuse":
            h.trace("verify", "fail", feature=f["id"], reason="trivial verification")
            raise SystemExit(f"ERROR: {problem}\n  (block {f['id']}, unblock it, then correct its verification.)")
        if problem:
            print(f"WARNING: {problem}")
    return ver


def _record_failed_attempt(h: Harness, feats: list[dict], f: dict, code: int, dur: float) -> int:
    """The feature stays active; count the attempt and nudge a change of approach after 3."""
    f.setdefault("attempts", 0)
    f["attempts"] += 1
    h.save_features(feats)
    h.trace("verify", "fail", feature=f["id"], exit_code=code, duration_s=round(dur, 1))
    print(f"[FAIL] {f['id']} is still active (attempt {f['attempts']}). Fix it and verify again."
          + (" Tried ≥3 times: change your approach, or `block` it and record the reason." if f["attempts"] >= 3 else ""))
    return 1


def _check_scope(h: Harness, f: dict) -> tuple[list[str], bool]:
    """Report changes outside the feature's scope; returns (drift, blocked)."""
    drift = scope_drift(h, f)
    if not drift:
        return drift, False
    blocking = h.cfg.get("scope_drift") == "error"
    print(f"{'ERROR' if blocking else 'WARNING'}: {len(drift)} changed file(s) outside "
          f"{f['id']}'s scope {f.get('scope')}: {', '.join(drift[:10])}\n"
          f"  WHY: work outside the active feature is unverified scope creep (WIP=1).\n"
          f"  FIX: revert those changes, or `add` them as a separate feature.")
    if blocking:
        h.trace("verify", "fail", feature=f["id"], reason="scope drift", files=drift[:20])
    return drift, blocking


def _run_project_check(h: Harness, f: dict, ver: str, skip: bool) -> tuple[bool, str]:
    """Project-wide check after the feature's own command (lessons 09-10).
    Returns (ok, value recorded as evidence.project_check: "skipped" or the check level)."""
    level = f.get("verify_check") or h.cfg.get("verify_check", "quick")
    if skip:
        return True, "skipped"
    if "harness.py check" in ver:  # the feature's own command already was the project check
        return True, level
    print(f"== verification command passed; running the project-wide {level} check to rule out regressions ==")
    if cmd_check(h, argparse.Namespace(full=(level == "full"), only=None)) != 0:
        h.trace("verify", "fail", feature=f["id"], reason="project check failed")
        print(f"[FAIL] {f['id']} was not moved to passing because the project-wide check failed.")
        pending = [x["id"] for x in h.load_features() if x.get("status") == "not_started"
                   and x.get("category") != "harness"]  # product features; H00x are the setup baseline
        if pending:
            # found in a multi-session experiment: tests written up front for later features keep the baseline red,
            # so no feature can ever pass, and WIP=1 + the scope guard forbid fixing it inside this feature
            print(f"  NOTE: {f['id']}'s own verification passed. {len(pending)} feature(s) have not started yet "
                  f"({', '.join(pending[:5])}). If the failing tests belong to them, the baseline is red by design and "
                  f"no feature can pass: mark those tests pending (skip until their feature is implemented) or leave "
                  f"them out of commands.test. Do not implement another feature inside {f['id']} (WIP=1).")
        return False, level
    return True, level


def _mark_passing(h: Harness, feats: list[dict], f: dict, ver: str, out: str, dur: float, project_check: str,
                  drift: list[str]) -> int:
    f["status"] = "passing"
    f["evidence"] = {"verified_at": now_iso(), "commit": h.head() or None, "command": ver,
                     "exit_code": 0, "duration_s": round(dur, 1), "project_check": project_check,
                     "output_tail": tail(out, 8)}
    fp = scope_fingerprint(h, f, {})
    if fp:
        f["evidence"]["scope_sha256"] = fp
    if drift:
        f["evidence"]["out_of_scope_changes"] = drift[:20]
    h.save_features(feats)
    h.trace("verify", "pass", feature=f["id"], duration_s=round(dur, 1))
    print(f"[PASS] {f['id']} → passing. Commit the work, update PROGRESS.md, then run `next`.")
    return 0


def cmd_verify(h: Harness, args) -> int:
    """Pass-gate (lesson 08): only a successful verification command (+ project check) moves a feature to passing."""
    feats = h.load_features()
    f = h.find_feature(feats, args.id)
    if f.get("status") == "passing":
        print(f"{f['id']} is already passing (state transitions are one-way). Use `check --full` to run regressions.")
        return 0
    ver = _require_verifiable(f, h)
    if not _review_gate(h, f, args):
        return 1
    print(f"== verify {f['id']}: {ver}")
    code, out, dur = h.run_shell(ver, f.get("cwd", "."))
    print(tail(out, 30))
    if code != 0:
        return _record_failed_attempt(h, feats, f, code, dur)
    for rel in _assertion_free_tests(h, ver, f.get("cwd", ".")):
        print(f"WARNING: {rel} is named by the verification command but contains no assertion: a test that asserts "
              f"nothing passes whatever the code does.")
    drift, blocked = _check_scope(h, f)
    if blocked:
        return 1
    ok, project_check = _run_project_check(h, f, ver, args.skip_check)
    if not ok:
        return 1
    return _mark_passing(h, feats, f, ver, out, dur, project_check, drift)


def cmd_block(h: Harness, args) -> int:
    feats = h.load_features()
    f = h.find_feature(feats, args.id)
    if f.get("status") == "passing":
        raise SystemExit("ERROR: cannot block a feature that is already passing.")
    f["status"] = "blocked"
    f["blocked_reason"] = args.reason
    h.save_features(feats)
    h.trace("block", "blocked", feature=f["id"], reason=args.reason)
    print(f"{f['id']} → blocked: {args.reason}. Record the blocker in PROGRESS.md under 'Known Issues'.")
    return 0


def cmd_unblock(h: Harness, args) -> int:
    feats = h.load_features()
    f = h.find_feature(feats, args.id)
    if f.get("status") != "blocked":
        raise SystemExit(f"ERROR: {f['id']} is not blocked.")
    f["status"] = "not_started"
    f.pop("blocked_reason", None)
    # a fresh start: the next activation records a new base commit (a stale one made every later commit look like
    # scope drift to `loop` — found building harness-tutorial, 2026-10-15)
    f.pop("activated_commit", None)
    f.pop("activated_at", None)
    h.save_features(feats)
    h.trace("unblock", "ok", feature=f["id"])
    print(f"{f['id']} → not_started.")
    return 0


def cmd_add(h: Harness, args) -> int:
    feats = h.load_features()
    if any(f.get("id") == args.id for f in feats):
        raise SystemExit(f"ERROR: id {args.id} already exists.")
    problem = _trivial_problem(h, args.id, args.verify) if args.verify else None
    if problem and h.cfg.get("trivial_verification", "refuse") == "refuse":
        raise SystemExit(f"ERROR: {problem}")
    if problem:
        print(f"WARNING: {problem}")
    item = {"id": args.id, "category": args.category, "behavior": args.behavior, "verification": args.verify,
            "status": "not_started", "depends_on": args.depends or [], "evidence": None}
    if args.out_of_scope:
        item["out_of_scope"] = args.out_of_scope
    if args.scope:
        item["scope"] = args.scope
    if args.verify_check:
        item["verify_check"] = args.verify_check
    if args.priority is not None:
        item["priority"] = args.priority
    feats.append(item)
    h.save_features(feats)
    h.trace("add", "ok", feature=args.id)
    print(f"Added {args.id}.")
    return 0


# ---------------------------------------------------------------- session ---

def cmd_start(h: Harness, args) -> int:
    """Clock in: re-establish context within a few minutes."""
    session = {"started_at": now_iso(), "head": h.git("rev-parse", "HEAD"), "ts": time.time()}
    h.hdir.mkdir(exist_ok=True)
    atomic_write(h.hdir / "session.json", json.dumps(session, indent=2))
    h.trace("start", "ok")
    print("== SESSION START ==")
    print(f"Read in order: {h.cfg['entry_file']} → {h.cfg['progress_file']} → docs/DECISIONS.md")
    prog = h.path("progress_file")
    if prog.exists():
        text = prog.read_text(encoding="utf-8")
        # the Vietnamese heading alternative is kept for backward compatibility with older progress files
        m = re.search(r"^## (Next Steps|Bước tiếp theo).*?(?=^## |\Z)", text, re.M | re.S)
        if m:
            print("\n" + m.group(0).strip())
    print("\nRecent commits:\n" + (h.git("log", "--oneline", "-5") or f"({h.no_commit_label()})"))
    dirty = h.git("status", "--porcelain")
    if dirty:
        print(f"\nWARNING: the working tree has uncommitted changes (commit or record them before new work):\n"
              f"{dirty[:800]}")
    print()
    cmd_status(h, args)
    if not args.no_check:
        print()
        rc = cmd_check(h, argparse.Namespace(full=False, only=None))
        if rc != 0:
            print("\nThe repo is NOT in a consistent state. First task: make check pass (or document it in PROGRESS.md).")
    print("\nNext: `python scripts/harness.py next` (WIP=1).")
    return 0


def _changed_added_lines(h: Harness, base: str) -> list[tuple[str, str]]:
    diff = h.git("diff", base, "--unified=0", "--no-color") if base else h.git("diff", "HEAD", "--unified=0", "--no-color")
    out, cur = [], ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and not line.startswith("+++") and cur:
            out.append((cur, line[1:]))
    # new files not yet tracked by git are absent from the diff: scan their full contents
    for rel in h.git("ls-files", "--others", "--exclude-standard").splitlines():
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.split("/")[:-1]):
            continue
        p = h.root / rel
        if p.suffix.lower() in CODE_SUFFIXES and p.is_file() and p.stat().st_size < 1_000_000:
            out += [(rel, l) for l in p.read_text(encoding="utf-8", errors="ignore").splitlines()]
    return out


def debug_findings(h: Harness, base: str) -> tuple[list[str], list[str]]:
    errors, warns = [], []
    pats = h.cfg["debug_patterns"]
    for path, line in _changed_added_lines(h, base):
        if path.endswith((".md", ".json", "harness.py")) or any(fnmatch.fnmatch(path, g) for g in h.cfg.get("debug_exempt", [])):
            continue
        if any(re.search(p, line) for p in pats.get("error", [])):
            errors.append(f"leftover debug code: {path}: {line.strip()[:100]}")
        elif any(re.search(p, line) for p in pats.get("warn", [])):
            warns.append(f"{path}: {line.strip()[:100]}")
    return errors, warns


def junk_findings(h: Harness) -> list[str]:
    out = []
    for p in _walk(h.root):
        if any(fnmatch.fnmatch(p.name, g) for g in h.cfg["stale_artifacts"]):
            out.append(f"temporary/junk file: {p.relative_to(h.root).as_posix()}")
    tracked_env = h.git("ls-files", ".env", ".env.local", ".env.production")
    if tracked_env:
        out.append(f"secret file tracked by git: {tracked_env.replace(chr(10), ', ')}")
    return out


def _exit_check_verification(h: Harness, args, problems: list[str], warnings: list[str]) -> None:
    """1-2: the project check and the standard startup path (smoke)."""
    if not args.no_run:
        if cmd_check(h, argparse.Namespace(full=args.full, only=None)) != 0:
            problems.append("check does not pass — the session is not done.")
    for c in h.cfg["commands"].get("smoke", []):
        code, out, _ = h.run_shell(c["cmd"], c.get("cwd", "."), timeout=300)
        if code != 0:
            problems.append(f"standard startup path is broken: `{c['cmd']}`\n{tail(out, 10)}")
    if not h.cfg["commands"].get("smoke"):
        warnings.append("commands.smoke (quick startup command) is not configured — cannot verify the startup path.")


def _exit_check_state(h: Harness, problems: list[str], warnings: list[str]) -> dict:
    """3-4: feature list validity and progress recorded during the session. Returns the session record."""
    problems += [f"feature list: {e}" for e in validate_features(h)]
    sess_p = h.hdir / "session.json"
    session = json.loads(sess_p.read_text(encoding="utf-8")) if sess_p.exists() else {}
    prog = h.path("progress_file")
    if not prog.exists():
        problems.append(f"{h.cfg['progress_file']} is missing.")
    elif session and prog.stat().st_mtime < session.get("ts", 0):
        problems.append(f"{h.cfg['progress_file']} was not updated this session (run `handoff`, then write "
                        f"Done / In Progress / Known Issues / Next Steps).")
    if not session:
        warnings.append("no .harness/session.json — next time, start the session with `start`.")
    stale = stale_features(h)
    if stale:
        warnings.append(_stale_line(stale))
    return session


def _exit_check_hygiene(h: Harness, session: dict, problems: list[str], warnings: list[str]) -> None:
    """5-7: debug code in added lines, junk files, tracked secrets, scope drift, uncommitted work."""
    errs_dbg, warns_dbg = debug_findings(h, session.get("head", ""))
    problems += errs_dbg + junk_findings(h)
    warnings += warns_dbg
    for f in h.load_features():
        if f.get("status") == "active":
            drift = scope_drift(h, f)
            if drift:
                (problems if h.cfg.get("scope_drift") == "error" else warnings).append(
                    f"{len(drift)} changed file(s) outside {f['id']}'s scope: {', '.join(drift[:10])}")
    dirty = [rel for rel in h.dirty_paths() if not rel.startswith(".harness/") or rel.startswith(".harness/config")]
    if dirty:
        warnings.append(f"{len(dirty)} uncommitted file(s) — commit the verified work, or record the WIP in PROGRESS.md.")


def cmd_exit_check(h: Harness, args) -> int:
    """Clean-state check (lesson 12): build/test pass, progress recorded, no junk, startup works."""
    problems: list[str] = []
    warnings: list[str] = []
    print("== SESSION EXIT CHECK ==")
    _exit_check_verification(h, args, problems, warnings)
    session = _exit_check_state(h, problems, warnings)
    _exit_check_hygiene(h, session, problems, warnings)
    prog = h.path("progress_file")
    if prog.exists() and FILL_MARKER in prog.read_text(encoding="utf-8", errors="replace"):
        warnings.append(f"{h.cfg['progress_file']} still has {FILL_MARKER} placeholders: write Done / In Progress / "
                        f"Known Issues / Next Steps by hand — the next session starts from them")
    for w in warnings:
        print("WARNING:", w)
    for p in problems:
        print("ERROR:", p)
    ok = not problems
    h.trace("exit-check", "pass" if ok else "fail", problems=len(problems), warnings=len(warnings))
    print("== CLEAN STATE ==" if ok else f"== NOT CLEAN: {len(problems)} problem(s) ==")
    return 0 if ok else 1


def cmd_handoff(h: Harness, args) -> int:
    prog = h.path("progress_file")
    feats = h.load_features()
    s = stats(feats)
    lc = h.hdir / "last-check.json"
    check_line = "never run"
    if lc.exists():
        rec = json.loads(lc.read_text(encoding="utf-8"))
        failed = [r["step"] for r in rec["steps"] if r["status"] == "fail"]
        check_line = (f"{'PASS' if rec['ok'] else 'FAIL ' + ', '.join(failed)} ({rec['mode']}, {rec['ts']}"
                      + (f", @{rec['commit']})" if rec.get("commit") else ")"))
    active = [f"{f['id']} — {f.get('behavior')}" for f in feats if f.get("status") == "active"]
    blocked = [f"{f['id']} — {f.get('blocked_reason', '')}" for f in feats if f.get("status") == "blocked"]
    block = "\n".join([
        AUTO_START,
        f"_Auto-updated by `harness.py handoff` at {now_iso()}_",
        f"- Branch / commit: `{h.git('rev-parse', '--abbrev-ref', 'HEAD') or '-'}` @ `{h.git('log', '-1', '--pretty=%h %s') or '-'}`",
        f"- Last check: {check_line}",
        f"- Features: {s['passing']}/{s['total']} passing, VCR {s['vcr']}",
        f"- Active: {', '.join(active) or 'none'}",
        f"- Blocked: {', '.join(blocked) or 'none'}",
        f"- Uncommitted changes: {len(h.git('status', '--porcelain').splitlines())} file(s)",
        AUTO_END,
    ])
    if prog.exists():
        text = prog.read_text(encoding="utf-8")
        if AUTO_START in text and AUTO_END in text:
            text = re.sub(re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END), lambda _: block, text, flags=re.S)
        else:
            text = text.rstrip() + "\n\n## Current State\n" + block + "\n"
    else:
        text = f"# Progress\n\n## Current State\n{block}\n"
    atomic_write(prog, text)
    h.trace("handoff", "ok")
    print(block)
    print(f"\nUpdated {h.cfg['progress_file']}. Now write the Done / In Progress / Known Issues / "
          f"Next Steps sections yourself, and record new decisions in docs/DECISIONS.md.")
    return 0


def cmd_setup(h: Harness, args) -> int:
    dev = h.cfg["commands"].get("dev", [])
    if args.print_dev:
        for c in dev:
            print(f"    (cwd={c.get('cwd', '.')}) {c['cmd']}")
        if not dev:
            print("    (commands.dev is not configured in .harness/config.json)")
        return 0
    if args.run_dev:
        if not dev:
            raise SystemExit("ERROR: commands.dev is not configured.")
        c = dev[0]
        return subprocess.call(c["cmd"], shell=True, cwd=h.root / c.get("cwd", "."))
    steps = h.cfg["commands"].get("setup", [])
    if not steps:
        print("commands.setup is not configured — skipping the install step.")
    for c in steps:
        print(f"-> (cwd={c.get('cwd', '.')}) {c['cmd']}")
        code, out, dur = h.run_shell(c["cmd"], c.get("cwd", "."))
        print(tail(out, 15))
        if code != 0:
            h.trace("setup", "fail", cmd=c["cmd"])
            print(f"ERROR: setup failed (code {code}).\n  FIX: check the runtime version "
                  f"(.python-version/.nvmrc), network, and lockfile; fix the command in .harness/config.json if it is wrong.")
            return 1
    h.trace("setup", "ok")
    return 0


def cmd_size(h: Harness, args) -> int:
    """Measurable metric for refactor goals: a file's line count must not exceed the threshold."""
    ok = True
    for f in args.files:
        path_part, _, func = f.partition("::")
        p = h.root / path_part
        if not p.exists():
            print(f"ERROR: {path_part} not found")
            ok = False
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if func:  # `file.py::name` or `file.py::Class.method` — Python only (uses ast)
            import ast
            if p.suffix != ".py":
                print(f"ERROR: {f}: function-level size is only supported for Python files.")
                ok = False
                continue
            node, scope = None, ast.parse(text)
            for part in func.split("."):
                node = next((n for n in ast.iter_child_nodes(scope)
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
                if node is None:
                    break
                scope = node
            if node is None:
                print(f"ERROR: {func} not found in {path_part}")
                ok = False
                continue
            n = node.end_lineno - node.lineno + 1
        else:
            n = len(text.splitlines())
        passed = n <= args.max_lines
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] {f}: {n} lines (limit {args.max_lines})")
    return 0 if ok else 1


def load_failures(h: Harness) -> list[dict]:
    p = h.hdir / "failures.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def cmd_fail(h: Harness, args) -> int:
    """Diagnostic loop (lesson 01): attribute every agent failure to one harness layer, then fix that layer."""
    h.hdir.mkdir(exist_ok=True)
    entry = {"ts": now_iso(), "layer": args.layer, "note": args.note, "feature": args.feature, "commit": h.head()}
    with (h.hdir / "failures.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    h.trace("fail", "logged", layer=args.layer, feature=args.feature)
    fixes = {"instructions": "clarify AGENTS.md / docs (missing convention, vague task, buried rule)",
             "tools": "fix or add a command in .harness/config.json, or a permission",
             "environment": "fix init.sh / dependencies / runtime version pin",
             "state": "improve PROGRESS.md, DECISIONS.md or the feature list",
             "feedback": "add a test, rule in .harness/rules.json, or a sharper verification command"}
    print(f"Logged failure in layer '{args.layer}'. Fix that layer, not the model: {fixes[args.layer]}.")
    return 0


def cmd_failures(h: Harness, args) -> int:
    fails = load_failures(h)
    if not fails:
        print("No failures logged. Log them with `fail --layer <layer> --note ...` when an agent run goes wrong.")
        return 0
    counts = {layer: sum(1 for f in fails if f.get("layer") == layer) for layer in LAYERS}
    for layer, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{layer:13} {n:3}  {'█' * n}")
    worst = max(counts, key=counts.get)
    print(f"Bottleneck by observed failures: {worst}. Recent:")
    for f in fails[-5:]:
        print(f"  {f['ts']} [{f['layer']}] {f.get('feature') or '-'}: {f['note']}")
    return 0


def stop_problems(h: Harness) -> list[str]:
    """What the Stop gate blocks on. Cheap, deterministic checks only (no test run)."""
    sess_p = h.hdir / "session.json"
    session = json.loads(sess_p.read_text(encoding="utf-8")) if sess_p.exists() else {}
    problems = [e for e in validate_features(h)
                if "WIP violation" in e or "no evidence" in e or "not valid JSON" in e]
    problems += debug_findings(h, session.get("head", ""))[0]
    problems += junk_findings(h)
    if h.cfg.get("scope_drift") == "error":
        for f in h.load_features():
            if f.get("status") == "active" and scope_drift(h, f):
                problems.append(f"changes outside {f['id']}'s scope: {', '.join(scope_drift(h, f)[:5])}")
    problems += unverified_changes(h)
    problems += handoff_missing(h, session)
    return problems


def _trace_since(h: Harness, since: float) -> list[dict]:
    """Trace entries written at or after `since` (epoch seconds; the trace stores ISO times to the second)."""
    out, p = [], h.hdir / "trace.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]:
        try:
            e = json.loads(line)
            t = _dt.datetime.fromisoformat(e.get("ts", "")).timestamp()
        except (ValueError, TypeError, AttributeError):
            continue
        if t >= since - 1:
            out.append(e)
    return out


def handoff_missing(h: Harness, session: dict) -> list[str]:
    """V021: the session finished work (a verify passed, or a commit was made) but PROGRESS.md was not updated since
    the session started — the next session would start from a stale handoff."""
    ts = float(session.get("ts") or 0) if isinstance(session, dict) else 0.0
    prog = h.path("progress_file")
    if not ts or not prog.exists() or prog.stat().st_mtime >= ts:
        return []
    verified = [e.get("feature") for e in _trace_since(h, ts) if e.get("cmd") == "verify" and e.get("result") == "pass"]
    committed = False
    if session.get("head") and h.git("rev-parse", "HEAD") not in ("", session.get("head")):
        # only commits made during this session: checking out a branch with older commits is not new work (2.3.1)
        times = h.git("log", "--format=%ct", f"{session['head']}..HEAD").split()
        committed = any(t.isdigit() and int(t) >= ts - 1 for t in times)
    if not verified and not committed:
        return []
    what = f"verified {', '.join(sorted(set(filter(None, verified))))}" if verified else "made commits"
    return [f"this session {what} but {h.cfg['progress_file']} was not updated: hand off before finishing "
            f"(/hk-handoff, or `python scripts/harness.py handoff` then write Done / Next Steps), so the next session "
            f"starts from the real state"]


RATIONALIZATIONS = [
    (r"\bpre-?existing\s+(issue|failure|bug|problem|error|test failure)s?\b", "calls a failure pre-existing"),
    (r"\b(skip(?:ped|ping)?|disabl(?:ed|ing))\s+(?:the\s+|a\s+|some\s+|these\s+)?(?:failing\s+)?tests?\b",
     "mentions skipping or disabling tests"),
    (r"\b(?:unrelated|not related)\s+to\s+(?:my|the|this|these)\s+changes?\b", "declares a failure unrelated to the change"),
]
TESTS_PASS_CLAIM = r"\b(?:all\s+(?:the\s+)?)?tests?\s+(?:now\s+)?(?:pass(?:es|ed|ing)?|are\s+passing|succeed(?:ed)?)\b"


def stop_warnings(h: Harness, payload: dict) -> list[str]:
    """V021, warn-only (ECC's rationalization detector): phrases that often hide unfinished work, and a claim that the
    tests pass when no harness check ran this session. Shown to the user; they never block."""
    text = str(payload.get("last_assistant_message") or "")
    if not text:
        return []
    out = [why for pattern, why in RATIONALIZATIONS if re.search(pattern, text, re.I)]
    session = _read_json(h.hdir / "session.json", {})
    ts = float(session.get("ts") or 0) if isinstance(session, dict) else 0.0
    if ts and re.search(TESTS_PASS_CLAIM, text, re.I):
        checked = any(e.get("cmd") in ("verify", "try", "check") for e in _trace_since(h, ts))
        rec = _read_json(h.hdir / "last-check.json", {})
        try:
            checked = checked or _dt.datetime.fromisoformat(rec.get("ts", "")).timestamp() >= ts - 1
        except (ValueError, TypeError, AttributeError):
            pass
        if not checked:
            out.append("says the tests pass, but no `verify`/`try`/`check` ran in this session")
    return out


def cmd_hook_stop(h: Harness, args) -> int:
    """Claude Code Stop hook. Cheap, deterministic checks only (no test run): exit 2 + stderr blocks the stop."""
    if os.environ.get("HARNESS_STOP_HOOK", "on").lower() in ("off", "0", "false"):
        return 0
    payload = {}
    try:
        raw = _stdin_text()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}
    problems = stop_problems(h)
    if payload.get("stop_hook_active"):  # already continued once because of this hook: never loop
        if problems:  # the gate let the turn end with problems still open: visible at the next session start
            record_incident(h, "stop-gate-exhausted", "; ".join(problems)[:500], session=payload.get("session_id"))
        return 0
    h.trace("hook-stop", "block" if problems else "allow", problems=len(problems), session=payload.get("session_id"))
    if not problems:
        warnings = stop_warnings(h, payload)
        if warnings:
            h.trace("hook-stop", "warn", warnings=warnings, session=payload.get("session_id"))
            print(json.dumps({"systemMessage": "harness-kit: the final message " + "; ".join(warnings) +
                              ". Check the work before trusting it."}, ensure_ascii=False))
        return 0
    sys.stderr.write("Harness Stop hook: the repo is not in a clean state.\n")
    for p in problems[:15]:
        sys.stderr.write(f"- {p}\n")
    sys.stderr.write("FIX: resolve these (remove debug code/junk, fix the feature list via scripts/harness.py), "
                     "then run `python scripts/harness.py exit-check` before finishing.\n")
    return 2


def _last_verification_epoch(h: Harness, fid: str) -> float:
    """When the active feature's verification last ran with a recorded result: `try`/`verify` in the trace, or a
    project check. Machine-readable evidence for the Stop gate (R11)."""
    best = 0.0
    rec = _read_json(h.hdir / "last-check.json", None)
    if isinstance(rec, dict):
        try:
            best = _dt.datetime.fromisoformat(rec.get("ts", "")).timestamp()
        except (TypeError, ValueError):
            pass
    p = h.hdir / "trace.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("feature") == fid and e.get("cmd") in ("try", "verify"):
                if isinstance(e.get("epoch"), (int, float)):
                    best = max(best, float(e["epoch"]))
                else:
                    try:
                        best = max(best, _dt.datetime.fromisoformat(e.get("ts", "")).timestamp())
                    except (TypeError, ValueError):
                        pass
    return best


def unverified_changes(h: Harness) -> list[str]:
    """Stop gate (R11): the active feature has files changed after its verification last ran. Cheap: git status and
    file times only, no test run; the Stop hook blocks once, then `stop_hook_active` lets the turn end."""
    feats = h.load_features()
    active = [f for f in feats if f.get("status") == "active"]
    dirty = []
    for rel in h.dirty_paths():
        if rel and not rel.startswith((".harness/", ".claude/")) and rel not in (h.cfg["feature_list"],
                                                                               h.cfg["progress_file"]):
            dirty.append(rel)
    if not active:
        # work that belongs to a feature nobody activated: no gate ever sees it, and the next session finds the feature
        # still not_started and does it again (found with sonnet in experiment 2, 16/10/2026)
        for f in feats:
            scope = _scope_globs(f)
            hit = [rel for rel in dirty if scope and any(fnmatch.fnmatch(rel, g) for g in scope)]
            if f.get("status") in ("not_started", "blocked") and hit:
                return [f"{', '.join(hit[:3])} changed, which is {f['id']}'s scope, but {f['id']} is {f.get('status')}: "
                        f"activate it (`python scripts/harness.py next --id {f['id']}`) and run `verify {f['id']}`, so "
                        f"the next session sees it as done — otherwise it starts this work again"]
        return []
    f = active[0]
    scope = _scope_globs(f)
    changed = []
    for rel in dirty:
        if scope and not any(fnmatch.fnmatch(rel, g) for g in scope):
            continue
        p = h.root / rel
        if p.is_file():
            changed.append((rel, p.stat().st_mtime))
    if not changed:
        return []
    since = _last_verification_epoch(h, f["id"])
    newer = [rel for rel, mtime in changed if mtime > since + 1]
    if not newer:
        return []
    return [f"{f['id']} has changes not verified since they were made ({', '.join(newer[:5])}): run "
            f"`python scripts/harness.py try {f['id']}` and report the result before finishing"]


# ------------------------------------------------------------------- loop ---
# Lessons 09/13/14: the system, not the agent, runs maker -> independent reviewer -> pass-gate, with machine-checked
# stop conditions. Each role is a fresh `claude -p` session (the user's Claude Code login; no API key needed).
# Defense in depth: (1) the CLI permission mode + allowed tools, (2) a PreToolUse hook (best effort, passed with
# --settings), (3) the real gate after every session: integrity snapshot of harness files + scope check, then verify.

PROTECTED = ["feature_list.json", ".harness/*", "scripts/harness.py", ".claude/*"]
# harness-owned files restored after every agent session unless the feature's scope names them (all of .harness/ and
# .claude/ are restored too, see _guarded_files)
INTEGRITY_FILES = [".harness/config.json", ".harness/rules.json", "scripts/harness.py", "AGENTS.md", "CLAUDE.md",
                   "CLAUDE.local.md", ".mcp.json", "docs/harness/evaluator-rubric.md", ".claude/settings.json"]
# written by the runtime itself while agents run `check`/`try`, and by the project's own v2 Claude Code hooks, which
# also run inside loop sessions (found building harness-tutorial, 2026-10-15)
LOOP_RUNTIME_STATE = {".harness/trace.jsonl", ".harness/last-check.json", ".harness/session.json",
                      ".harness/activity.jsonl", ".harness/hook-state.json", ".harness/compact-snapshot.md"}
DENIED_COMMANDS = [
    (r"harness\.py\s+(verify|next|block|unblock|add|fail|loop|upgrade)\b",
     "feature state changes only through the harness loop"),
    (r"\bgit\s+(-[Cc]\s*\S+\s+|--\S+\s+)*(commit|push|reset|rebase|checkout|switch|clean|stash(?!\s+(list|show)\b)|merge|tag|apply|am|"
     r"restore|rm|mv|worktree)\b", "the human approves and commits; the working tree is not rewritten by agents"),
    (r"(^|\s)--output(-file)?(=|\s)", "commands must not write output files"),
]
# Found in real runs: shell redirection wrote junk files outside the scope check -> files are written only with
# Write/Edit. Redirection is matched with quoted strings removed (so `python -c "print(1 > 0)"` passes). File commands
# are matched only in command position (start, after | ; & ( $( `, after `-c`, sudo/env/xargs or VAR=x) with quote
# marks removed, so `bash -c "cp a b"` and `FOO=1 cp` are caught but `grep -n "rm -rf" .` is not.
# Best effort: the integrity restore and scope revert after every session are the real gate.
DENIED_REDIRECTS = [
    (r"(&>|(?<![<>&\w])\d?>>?)(?!&)\s*(?!/dev/null\b|nul\b|\$null\b)[^\s|&;>]",
     "write files with the Write/Edit tools (they are scope-checked), not shell redirection"),
]
CMD_POS = (r"(?:^|[|;&(`{\n]|\$\(|(?<!\S)(?:(?:ba|z|da)?sh|cmd|pwsh|powershell)(?:\.exe)?\s+(?:-c|/c|-Command)\s+"
           r"|(?<!\S)(?:xargs|sudo|env|exec|nohup|time|command)\s+|(?<!\S)\w+=\S*\s+)\s*(?:\S*/)?")
IDE_DIRS = {".idea", ".vscode", ".vs", ".fleet"}  # local editor state: never the feature's work, never blamed on it
DENIED_FILE_WRITES = [
    (CMD_POS + r"(tee|cp|mv|rm|rmdir|touch|truncate|dd|ln|unlink|install|patch|del|erase|rd|ren|ri|sc|ni|cpi|mi|"
     r"Copy-Item|Move-Item|Remove-Item|Rename-Item|Set-Content|Add-Content|Out-File|New-Item)(?![\w.-])",
     "create, move or delete files with the Write/Edit tools (scope-checked)"),
    (CMD_POS + r"(sed|perl)\b[^|;&]*\s(-[a-zA-Z]*i\b|--in-place)", "edit files with the Edit tool (scope-checked), not in place"),
    (CMD_POS + r"find\b[^|;&]*\s-(delete|exec|execdir|ok|fprint\w*)\b", "find may only list files here"),
]
MAKER_SCHEMA = {"type": "object", "required": ["summary", "files_changed", "verification_passed"],
                "properties": {"summary": {"type": "string"}, "files_changed": {"type": "array", "items": {"type": "string"}},
                               "verification_passed": {"type": "boolean"}, "open_issues": {"type": "string"}}}
REVIEW_SCHEMA = {"type": "object", "required": ["verdict", "issues", "evidence"],
                 "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]},
                                "issues": {"type": "array", "items": {"type": "object", "properties": {
                                    "severity": {"type": "string"}, "location": {"type": "string"},
                                    "problem": {"type": "string"}, "fix": {"type": "string"}}}},
                                "evidence": {"type": "string"}}}
MAKER_PROMPT = """You are the MAKER in an automated harness loop. Work on exactly one feature, then stop.

Feature {id}: {behavior}
Done means: `python scripts/harness.py try {id}` exits 0 (it runs the feature's verification command:
{verification})
Scope (the only files you may change): {scope}
Out of scope: {out_of_scope}

Rules:
- First read AGENTS.md (and PROGRESS.md, docs/DECISIONS.md if present) and follow its hard constraints.
- You are already in the project root: run commands as is, one per call, never prefixed with `cd`.
- Change only what this feature needs, with the Write/Edit tools. Never edit feature_list.json, .harness/,
  scripts/harness.py, .claude/ or the rubric (unless your scope above names the file): the harness restores them
  and rejects the attempt.
- Never delete, skip or loosen tests. Do not commit, push, or run `harness.py verify/next/block/add`:
  the harness verifies, and a human approves and commits.
- New tests must be written in the style the project's own test command collects and runs:
  {test_command}. A test that this command does not run does not count.
- Use the Bash tool for commands; they already run in the project root (no `cd`). Before finishing, run `python scripts/harness.py try {id}` and
  `python scripts/harness.py check`, and fix what fails.
{feedback}
Return the JSON summary of what you did."""
REVIEW_PROMPT = """You are an independent REVIEWER in an automated harness loop. You did not write this change; do not
trust the maker's account. You cannot edit files. You are already in the project root: run every command as is, one
command per call, never prefixed with `cd` (a `cd … &&` prefix is denied in this session).

Feature {id}: {behavior}
Verification: `python scripts/harness.py try {id}` (runs: {verification})
Scope: {scope}   Out of scope: {out_of_scope}

1. Inspect the maker's change: `git diff {base} -- . ":(exclude)feature_list.json" ":(exclude)PROGRESS.md" ":(exclude).harness" ":(exclude)scripts/harness.py"`
   and `git status --short` (read new untracked files too). Changes to feature_list.json, PROGRESS.md, .harness/ and
   scripts/harness.py are made by the harness itself: they are not the maker's work and not a scope violation.
2. If docs/harness/evaluator-rubric.md exists, apply it.
3. Run `python scripts/harness.py try {id}` and `python scripts/harness.py check`.
4. Confirm that new or changed tests are actually COLLECTED and RUN by the project's test command ({test_command}):
   run `python scripts/harness.py check --only test` (the harness also compares the test count mechanically).
Use only the commands listed above, exactly as written: they already run in the project root (no `cd` prefix), and
others are not permitted in this session.
Return verdict "fail" if the behavior is not really implemented, new tests are not run by the project's test command,
tests were deleted/skipped/loosened, files outside the scope changed, debug code was left, or a command fails.
Return "pass" only if you would accept this as done. Every issue needs a location and a concrete fix."""


def _agent_base(spec: str) -> list[str]:
    spec = (spec or "").strip() or os.environ.get("HARNESS_AGENT_CMD", "") or "claude"
    base = [str(x) for x in json.loads(spec)] if spec.startswith("[") else spec.split()
    import shutil
    resolved = shutil.which(base[0])  # full path, so claude.cmd / claude.exe both work without a shell
    return [resolved or base[0]] + base[1:]


def _agent_available(base: list[str]) -> bool:
    return Path(base[0]).is_file()


def _allowed_tools(h: Harness, role: str) -> list[str]:
    tools = ["Read", "Glob", "Grep", "Bash(git diff *)", "Bash(git diff)", "Bash(git status *)", "Bash(git status)",
             "Bash(git log *)", "Bash(python scripts/harness.py try *)", "Bash(python scripts/harness.py check)",
             "Bash(python scripts/harness.py check *)", "Bash(python scripts/harness.py status)",
             # found in the first logged run: AGENTS.md tells agents to clock in, and they prefix `cd <root> &&`
             "Bash(python scripts/harness.py start)", "Bash(cd *)"]
    cmds = [c["cmd"] for key in ("test", "lint", "typecheck", "build", "e2e", "smoke")
            for c in h.cfg["commands"].get(key, [])]
    # the active feature's own verification command: the reviewer must be able to run exactly what it checks
    # (experiment 4, 16/10/2026: reviewers were denied `python -m unittest tests.test_part2 -q` and returned FAIL)
    cmds += [part.strip() for f in h.load_features() if f.get("status") == "active"
             and isinstance(f.get("verification"), str) for part in f["verification"].split("&&") if part.strip()]
    for cmd in dict.fromkeys(cmds):
        if "," in cmd:
            continue
        tools.append(f"Bash({cmd})")
        words = cmd.split()
        while len(words) > 1 and words[-1].startswith("-"):  # same command with other flags, e.g. -v for -q
            words.pop()
        if len(words) >= 2 and words != cmd.split():  # never widen to a bare interpreter (`node *`)
            tools.append(f"Bash({' '.join(words)} *)")
    # read-only helpers agents chain with (`... | grep x | head`); compound commands need every part allowed
    tools += ["Bash(grep *)", "Bash(head *)", "Bash(tail *)", "Bash(cat *)", "Bash(ls *)", "Bash(ls)", "Bash(wc *)",
              "Bash(git show *)"]
    # on Windows the agent may pick the PowerShell tool for the same commands
    tools += [t.replace("Bash(", "PowerShell(", 1) for t in tools if t.startswith("Bash(")]
    if role == "maker":
        tools = ["Edit", "Write", "MultiEdit"] + tools
    return list(dict.fromkeys(tools))


def _guard_settings(h: Harness) -> str:
    """Write the per-run settings (PreToolUse guard) under .harness/loop/; returns the path relative to the root."""
    script = h.root / "scripts" / "harness.py"
    target = script if script.exists() else Path(__file__).resolve()
    py, tgt, root = (Path(sys.executable).as_posix(), target.resolve().as_posix(), h.root.resolve().as_posix())
    settings = {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell", "hooks": [
        {"type": "command", "command": f'"{py}" "{tgt}" hook-pretool --root "{root}"'}]}]}}
    loop_dir = h.hdir / "loop"
    loop_dir.mkdir(parents=True, exist_ok=True)
    (loop_dir / ".gitignore").write_text("*\n", encoding="utf-8")  # local paths and run state never get committed
    (loop_dir / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return ".harness/loop/settings.json"


def _agent_argv(h: Harness, args, role: str, settings_rel: str, cap: float) -> list[str]:
    """The CLI command; the prompt goes on stdin (long multi-line prompts survive any shell or .cmd wrapper)."""
    maker = role == "maker"
    argv = _agent_base(args.agent_cmd) + [
        "-p", "--output-format", "stream-json", "--verbose",  # full transcript; the last line is the result
        "--json-schema", json.dumps(MAKER_SCHEMA if maker else REVIEW_SCHEMA),
        "--max-budget-usd", f"{cap:.4f}",
        "--permission-mode", "dontAsk",  # only the listed tools; no auto-approved rm/mv/cp/sed (acceptEdits would)
        "--allowedTools", ",".join(_allowed_tools(h, role)),
        "--settings", settings_rel, "--no-session-persistence"]
    model = args.model if maker else (args.reviewer_model or args.model)
    return argv + (["--model", model] if model else [])


def _cap(args, role: str, spent: float = 0.0) -> float:
    """The session's budget: its per-role cap, never more than what is left of --max-total-usd."""
    return max(0.0, min(args.budget_maker if role == "maker" else args.budget_reviewer, args.max_total_usd - spent))


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the agent and its children (on Windows `claude` is often a .cmd wrapper around node)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def _run_agent(h: Harness, args, role: str, prompt: str, settings_rel: str, spent: float, run: LoopRun) -> dict:
    cap = _cap(args, role, spent)
    argv = _agent_argv(h, args, role, settings_rel, cap)
    name = run.session_name(role)
    run.write(f"{name}.prompt.txt", prompt)
    env = {k: v for k, v in os.environ.items() if k != "HARNESS_ROOT"}
    env.update({"HARNESS_ROLE": role, "HARNESS_FEATURE": run.fid, "HARNESS_RUN": run.dir.resolve().as_posix(),
                "HARNESS_SESSION": name})
    start = time.time()
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(argv, cwd=h.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", env=env, **group)
        try:
            stdout, stderr = proc.communicate(prompt, timeout=h.cfg.get("loop_timeout_sec", 3600))
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            stdout, stderr = proc.communicate()  # keep the partial transcript: it shows where the session hung
            stderr = (stderr or "") + "\nagent timed out"
        stdout, stderr = stdout or "", stderr or ""
    except OSError as exc:
        stdout, stderr = "", f"agent failed to start: {exc}"
    run.write(f"{name}.transcript.jsonl", stdout)
    if stderr.strip():
        run.write(f"{name}.stderr.txt", stderr)
    data, msgs = _parse_stream(stdout)
    known = data.get("total_cost_usd") is not None
    out = {"role": role, "subtype": data.get("subtype") or ("no_result: " + tail(stderr or stdout, 3)),
           "ok": data.get("subtype") == "success" and not data.get("is_error"),
           # unknown spend (crash, timeout, no JSON) is charged at the session cap, never as free
           "cost": float(data["total_cost_usd"]) if known else cap,
           "turns": data.get("num_turns"), "structured": data.get("structured_output"),
           "duration_s": round(time.time() - start, 1),
           "denials": [f"{d.get('tool_name', '?')}: {json.dumps(d.get('tool_input', {}))[:120]}"
                       for d in (data.get("permission_denials") or []) if isinstance(d, dict)]}
    facts = _session_facts(data, msgs)
    facts["writes"] = [_rel_to_root(h, w) or w for w in facts["writes"]]
    out["session_id"] = facts["session_id"]
    run.event("session", out["subtype"], role=role, session=name, cost=out["cost"], cost_known=known,
              turns=out["turns"], duration_s=out["duration_s"], denials=out["denials"], cap_usd=round(cap, 4),
              prompt=prompt, **facts)
    h.trace("loop", out["subtype"], role=role, feature=run.fid, run=run.id, cost_usd=out["cost"], cost_known=known,
            turns=out["turns"])
    return out


def _fmt_issues(issues: list) -> str:
    lines = []
    for i in issues or []:
        if isinstance(i, dict):
            lines.append(f"- [{i.get('severity', '?')}] {i.get('location', '?')}: {i.get('problem', '')} "
                         f"→ fix: {i.get('fix', '')}")
    return "\n".join(lines) or "- (no details given)"


def _pick_loop_feature(h: Harness, wanted: str | None) -> dict | None:
    feats = h.load_features()
    active = [f for f in feats if f.get("status") == "active"]
    if wanted:
        f = h.find_feature(feats, wanted)
        if f.get("status") not in ("active", "not_started"):
            raise SystemExit(f"ERROR: {wanted} is {f.get('status')}; the loop only works on active or not_started "
                             f"features.\n  FIX: `unblock {wanted}` first, or pick another feature.")
        if active and active[0]["id"] != wanted:
            raise SystemExit(f"ERROR: {active[0]['id']} is active (WIP=1); finish or block it before looping on {wanted}.")
        return f
    if active:
        return active[0]
    for f in sorted(feats, key=lambda x: (x.get("priority", 100), feats.index(x))):
        # H00x edit .harness/config.json and the docs, which the loop protects: they belong to /hk-init
        if f.get("status") == "not_started" and _deps_ok(f, feats) and f.get("category") != "harness":
            return f
    return None


def _prompt_fields(h: Harness, f: dict) -> dict:
    scope = f.get("scope") or []
    tests = [f"`{c['cmd']}`" + (f" (in {c['cwd']}/)" if c.get("cwd", ".") != "." else "")
             for c in h.cfg["commands"].get("test", [])]
    return {"id": f["id"], "behavior": f.get("behavior", ""), "verification": f.get("verification", ""),
            "scope": ", ".join(scope if isinstance(scope, list) else [scope]) or "not declared: keep changes minimal",
            "out_of_scope": f.get("out_of_scope") or "nothing declared",
            "base": f.get("activated_commit") or "HEAD",
            "test_command": ", ".join(tests) or "none configured (see .harness/config.json commands.test)"}


# ---------------------------------------------------------- loop: run log ---
# Lesson 11 (observability): every loop run leaves a log that shows what each session actually did, and a report
# whose theory checklist is computed from that log, not from what the agents say about themselves.
# Trust model: the harness keeps its own events in memory and builds the report from them, so code an agent writes
# (e.g. its tests, run by the gates) cannot rewrite the record; hook decisions come from the file and are only as
# trustworthy as the session that could also append to it.

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
RUN_NAME_RE = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9_.-]+$")
# `harness.py try|check` in command position (not `grep 'harness.py try' AGENTS.md`)
VERIFY_CMD_RE = re.compile(r"(?:^|&&|;|\||\n)\s*(?:\S*python\S*|py)(?:\s+-\S+(?:\s+[^\s-]\S*)?)*\s+"
                           r"\S*scripts[/\\]harness\.py\s+"
                           r"(?:try|check)\b")


class LoopRun:
    """One feature's loop run: .harness/runs/<id>/ holds events.jsonl, each session's prompt and transcript, report.md."""

    def __init__(self, h: Harness, fid: str):
        self.h, self.fid, self.attempt, self.sessions, self.events = h, fid, 0, {}, []
        stem = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^A-Za-z0-9_.-]", "_", fid)
        self.id, n = stem, 1
        while (h.hdir / "runs" / self.id).exists():
            n += 1
            self.id = f"{stem}-{n}"
        self.dir = h.hdir / "runs" / self.id
        self.dir.mkdir(parents=True)
        # transcripts hold code and local paths: never committed, even where .harness/.gitignore predates runs/
        (h.hdir / "runs" / ".gitignore").write_text("*\n", encoding="utf-8")
        try:
            keep = int(h.cfg.get("loop_keep_runs", 30))
        except (TypeError, ValueError):
            keep = 30
        _prune_runs(h, keep=keep)

    def event(self, step: str, result: str, **data) -> None:
        rec = {"ts": now_iso(), "step": step, "result": result, "attempt": self.attempt, **data}
        self.events.append(rec)
        with (self.dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def session_name(self, role: str) -> str:
        key = f"a{self.attempt}-{role}"
        self.sessions[key] = self.sessions.get(key, 0) + 1
        return key if self.sessions[key] == 1 else f"{key}-{self.sessions[key]}"

    def write(self, name: str, text: str) -> None:
        (self.dir / name).write_text(text, encoding="utf-8")

    def finish(self) -> Path:
        """Write the authoritative log and the report. Lines the harness wrote must still be in the file exactly as
        written; if not, the file is rewritten from memory and the report says so."""
        on_disk = read_run_events(self.dir)
        hooks = [e for e in on_disk if e.get("step") == "hook"]
        mine = [json.dumps(e, sort_keys=True) for e in self.events]
        tampered = [json.dumps(e, sort_keys=True) for e in on_disk if e.get("step") != "hook"] != mine
        self.event("log-integrity", "rewritten" if tampered else "ok")
        if tampered:
            self.h.trace("loop", "run-log-tampered", run=self.id, feature=self.fid)
        events = self.events + hooks
        (self.dir / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                                               encoding="utf-8")
        (self.h.hdir / "runs" / ".gitignore").write_text("*\n", encoding="utf-8")  # a session may have blanked it
        report = self.dir / "report.md"
        report.write_text(build_run_report(events, self.id), encoding="utf-8")
        self.report_sha256 = hashlib.sha256(report.read_bytes()).hexdigest()
        self.checklist = _theory_checklist(events)
        return report


def _run_dirs(h: Harness) -> list[Path]:
    """Real run directories only: named like a run, not a link, directly inside .harness/runs/."""
    root = h.hdir / "runs"
    if not root.is_dir() or root.is_symlink():
        return []
    real_root = root.resolve()
    return sorted(p for p in root.iterdir() if RUN_NAME_RE.match(p.name) and p.is_dir() and not p.is_symlink()
                  and p.resolve().parent == real_root)


def _prune_runs(h: Harness, keep: int) -> None:
    import shutil
    runs = _run_dirs(h)
    for old in runs[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)


def read_run_events(run_dir: Path) -> list[dict]:
    p = run_dir / "events.jsonl"
    out = []
    for line in (p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _parse_stream(stdout: str) -> tuple[dict, list[dict]]:
    """`claude -p --output-format stream-json --verbose` prints one JSON message per line; the last `result` message
    is the summary that `--output-format json` would print."""
    msgs = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                msgs.append(d)
    return next((m for m in reversed(msgs) if m.get("type") == "result"), {}), msgs


def _session_facts(result: dict, msgs: list[dict]) -> dict:
    """What a session actually did, read from its transcript. A tool call the CLI or the hook refused did not run:
    it is listed under `denied_calls`, not counted in `tools`/`writes`/`commands`."""
    init = next((m for m in msgs if m.get("type") == "system" and m.get("subtype") == "init"), {})
    denied_ids = {d.get("tool_use_id") for d in (result.get("permission_denials") or []) if isinstance(d, dict)}
    for m in msgs:
        if m.get("type") == "user":
            for c in (m.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error") and \
                        re.search(r"has been denied|Blocked by the harness", str(c.get("content", ""))):
                    denied_ids.add(c.get("tool_use_id"))
    denied_ids.discard(None)
    tools, writes, commands, denied = {}, [], [], []
    for m in msgs:
        if m.get("type") != "assistant":
            continue
        for c in (m.get("message") or {}).get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            name, tin = c.get("name", "?"), c.get("input") or {}
            target = tin.get("command") or tin.get("file_path") or tin.get("notebook_path") or ""
            if c.get("id") in denied_ids:
                denied.append({"tool": name, "input": str(target)[:200]})
                continue
            tools[name] = tools.get(name, 0) + 1
            if name in WRITE_TOOLS:
                writes.append(str(target or "?"))
            elif name in ("Bash", "PowerShell"):
                commands.append(str(target)[:200])
    return {"session_id": init.get("session_id") or result.get("session_id"), "model": init.get("model"),
            "permission_mode": init.get("permissionMode"), "tools": tools, "writes": writes, "commands": commands,
            "denied_calls": denied}


ROW_MARKS = {"ok": "✔ held", "enforced": "⚠ enforced", "fail": "✘ violated", "na": "– n/a", "warn": "⚠ check",
             "unfinished": "⚠ unfinished"}


def _rejections(ev: list[dict], attempt: int) -> list[str]:
    """Concrete reasons attempt N was rejected, read from the log (not from the feedback the harness sent)."""
    at = [e for e in ev if e.get("attempt") == attempt]
    out = []
    for e in at:
        step, res = e.get("step"), e.get("result")
        if step == "session" and e.get("role") == "maker" and res != "success":
            out.append(str(res))
        elif step == "guard" and res in ("restored", "reverted"):
            out += [str(f) for f in e.get("files", [])[:10]]
        elif step == "review" and res == "fail":
            out += [str(i.get("problem", "")) for i in e.get("issues", []) if isinstance(i, dict) and i.get("problem")]
        elif step == "review" and res == "none":
            sessions = [s for s in at if s.get("step") == "session" and s.get("role") == "reviewer"]
            if sessions and sessions[-1].get("result") != "success":
                out.append(str(sessions[-1].get("result")))
        elif step == "test-gate" and res == "fail":
            out += [str(f) for f in e.get("uncollected", [])] or [f"ran {e.get('baseline')} test(s) before"]
        elif step == "verify" and res == "fail":
            out.append("pass-gate failed")
    return out


def _theory_checklist(ev: list[dict]) -> list[str]:
    """Each row is computed from logged facts. held = the principle held on its own; enforced = an agent broke it and
    the harness caught and undid it; violated = the run broke the principle; unfinished = the run has no end record."""
    by = lambda step: [e for e in ev if e.get("step") == step]
    start, ends = (by("run-start") or [{}])[0], by("run-end")
    end = ends[-1] if ends else {}
    sessions, guards, hooks = by("session"), by("guard"), by("hook")
    makers = [s for s in sessions if s.get("role") == "maker"]
    reviewers = [s for s in sessions if s.get("role") == "reviewer"]
    rows = ["| Lesson | Principle | Result | Evidence (from this run's log) |", "|---|---|---|---|"]

    def row(lesson: str, check: str, state: str, evidence: str) -> None:
        if not ends and state != "na":
            state = "unfinished"  # no run-end: the run crashed or was killed; nothing can be called held
        rows.append(f"| {lesson} | {check} | {ROW_MARKS[state]} | {evidence} |")

    if not ends:
        rows.append(f"| – | the run finished | {ROW_MARKS['fail']} | no run-end record: the loop crashed or was killed |")
    elif end.get("outcome") == "crashed":
        rows.append(f"| – | the run finished | {ROW_MARKS['fail']} | crashed: {str(end.get('detail', ''))[:120]} |")
    integrity_log = by("log-integrity")
    row("11", "the run log is the harness's own record", "na" if not integrity_log else
        ("enforced" if integrity_log[-1].get("result") == "rewritten" else "ok"),
        "events.jsonl matched the harness's in-memory record" if integrity_log and
        integrity_log[-1].get("result") == "ok" else ("an agent changed events.jsonl; rewritten from memory"
                                                      if integrity_log else "report rebuilt from disk, not verified"))

    active = start.get("active_features", [])
    row("07", "one feature at a time (WIP=1)", "ok" if active == [start.get("feature")] else "fail",
        f"active during the run: {', '.join(active) or 'none'}")

    scope = [g for g in guards if g.get("check") == "scope"]
    drift = [f for g in scope for f in g.get("files", [])]
    row("07", "maker changes stay in the feature's scope", "na" if not scope else ("enforced" if drift else "ok"),
        f"{len(scope)} maker session(s) checked against {start.get('scope') or 'no scope'}" +
        (f"; reverted: {', '.join(drift[:5])}" if drift else ""))

    pairs = [(m, r) for r in reviewers for m in makers if m.get("attempt") == r.get("attempt")]
    unknown = [p for p in pairs if not (p[0].get("session_id") and p[1].get("session_id"))]
    same = [p for p in pairs if p[0].get("session_id") and p[0].get("session_id") == p[1].get("session_id")]
    row("09", "maker and reviewer are separate, fresh sessions",
        "na" if not pairs else ("fail" if same else ("warn" if unknown else "ok")),
        f"{len(pairs)} maker/reviewer pair(s); distinct session ids: {len(pairs) - len(same) - len(unknown)}")

    rev_writes = [w for r in reviewers for w in r.get("writes", [])]
    rev_tried = [d for r in reviewers for d in r.get("denied_calls", []) if d.get("tool") in WRITE_TOOLS]
    rev_restored = [f for g in guards if g.get("check") == "integrity" and g.get("role") == "reviewer"
                    for f in g.get("files", [])]
    row("09", "the reviewer is read-only",
        "na" if not reviewers else ("fail" if rev_writes else ("enforced" if rev_restored or rev_tried else "ok")),
        f"{len(reviewers)} reviewer session(s); write calls that ran: {len(rev_writes)}; refused: {len(rev_tried)}" +
        (f"; restored: {', '.join(rev_restored[:5])}" if rev_restored else ""))

    ran_checks = [r for r in reviewers if any(VERIFY_CMD_RE.search(c) for c in r.get("commands", []))]
    row("09", "the reviewer ran the verification itself",
        "na" if not reviewers else ("ok" if len(ran_checks) == len(reviewers) else "warn"),
        f"{len(ran_checks)}/{len(reviewers)} reviewer session(s) ran `harness.py try`/`check` (refused calls not counted)")

    passed = end.get("outcome") == "passing"
    verifies, reviews, gates = by("verify"), by("review"), by("test-gate")
    last_verify = verifies[-1] if verifies else {}
    att = last_verify.get("attempt")
    gate_ok = any(g.get("attempt") == att and g.get("result") in ("pass", "skipped") for g in gates)
    review_ok = any(r.get("attempt") == att and r.get("result") == "pass" for r in reviews)
    if passed:
        state = "ok" if last_verify.get("result") == "pass" and gate_ok and review_ok and \
            end.get("status_after") == "passing" else "fail"
        evidence = (f"attempt {att}: reviewer {'pass' if review_ok else 'NOT pass'} → test gate "
                    f"{'pass' if gate_ok else 'NOT pass'} → verify exit {last_verify.get('exit_code')}")
    else:
        state = "ok" if end.get("status_after") not in (None, "passing") else "fail"
        evidence = f"outcome {end.get('outcome')}; feature left {end.get('status_after')}"
    row("08", "passing only through the pass-gate (reviewer → test gate → verify)", state, evidence)

    maker_attempts = sorted({m.get("attempt") for m in makers if m.get("attempt")})
    checked, missing = 0, []
    for a in maker_attempts:
        nxt = [m for m in makers if m.get("attempt") == a + 1]
        if not nxt:
            continue
        prompt = nxt[0].get("prompt", "")
        for reason in _rejections(ev, a):
            checked += 1
            if reason and reason not in prompt:
                missing.append(f"attempt {a}: {reason[:60]}")
    row("10", "every rejection is fed back to the next maker", "na" if not checked else ("fail" if missing else "ok"),
        f"{checked - len(missing)}/{checked} rejection reason(s) found in the next maker's prompt" +
        (f"; missing: {'; '.join(missing[:3])}" if missing else ""))

    last_gate = gates[-1] if gates else {}
    row("06/08", "new tests are real: collected and run, none deleted",
        "na" if not gates or last_gate.get("result") == "skipped" else
        ("ok" if last_gate.get("result") == "pass" else "enforced"),
        (f"last gate: {last_gate.get('baseline')} → {last_gate.get('now')} tests; "
         f"new files: {', '.join(last_gate.get('new_files', [])) or 'none'}") if gates else "no gate ran")

    integrity = [g for g in guards if g.get("check") == "integrity"]
    restored = [f for g in integrity for f in g.get("files", [])]
    row("12", "harness state untouched by agents", "na" if not integrity else ("enforced" if restored else "ok"),
        f"{len(integrity)} integrity check(s)" + (f"; restored: {', '.join(restored[:5])}" if restored else ""))

    guarded = sum(n for s in sessions for t, n in s.get("tools", {}).items() if t in WRITE_TOOLS + ("Bash", "PowerShell"))
    guarded += sum(1 for s in sessions for d in s.get("denied_calls", []) if d.get("tool") in WRITE_TOOLS +
                   ("Bash", "PowerShell"))
    denied = [x for x in hooks if x.get("result") == "deny"]
    row("12", "the PreToolUse guard actually fired", "na" if not guarded else ("ok" if hooks else "warn"),
        f"{len(hooks)} hook decision(s) ({len(denied)} denied) for {guarded} guarded tool call(s)" +
        ("" if hooks or not guarded else " — the hook never ran: check --settings"))

    lim = start.get("limits") or {}
    if not lim or not ends:
        row("13", "stop conditions held (attempts, budget)", "na" if not lim else "unfinished",
            "no limits recorded" if not lim else "no run-end record")
    else:
        within = (end.get("attempts") or 0) <= lim.get("max_attempts", 0) and \
            (end.get("run_total_usd") or 0) <= lim.get("max_total_usd", 0) + 1e-6
        row("13", "stop conditions held (attempts, budget)", "ok" if within else "fail",
            f"attempts {end.get('attempts')}/{lim.get('max_attempts')}, loop total ${end.get('run_total_usd') or 0:.4f}"
            f"/${lim.get('max_total_usd')} (this feature ${end.get('spent_usd') or 0:.4f}), outcome {end.get('outcome')}")
    return rows


def build_run_report(ev: list[dict], run_id: str) -> str:
    start = next((e for e in ev if e.get("step") == "run-start"), {})
    end = next((e for e in reversed(ev) if e.get("step") == "run-end"), {})
    lines = [f"# Loop run {run_id}", "",
             f"Feature **{start.get('feature', '?')}**: {start.get('behavior', '')}",
             f"Outcome: **{end.get('outcome', 'unfinished')}** ({end.get('detail', 'no run-end event')}); "
             f"feature status now `{end.get('status_after', '?')}`.", "",
             "## Theory checklist", "", *_theory_checklist(ev), "",
             "✔ held = the principle held on its own · ⚠ enforced = an agent broke it and the harness caught and undid "
             "it · ✘ violated = the run broke the principle: find out why (harness bug, model, or setup) and log it with "
             "`fail --layer` · ⚠ check = a weak signal worth a look.", "", "## Timeline"]
    hooks = [e for e in ev if e.get("step") == "hook"]
    for a in sorted({e.get("attempt") for e in ev if isinstance(e.get("attempt"), int) and e.get("attempt") > 0}):
        lines += ["", f"### Attempt {a}"]
        for e in (x for x in ev if x.get("attempt") == a):
            step = e.get("step")
            if step == "session":
                tools = ", ".join(f"{t}×{n}" for t, n in sorted(e.get("tools", {}).items())) or "none"
                mine = [x for x in hooks if x.get("session") == e.get("session")]
                lines.append(f"- **{e.get('role')}** `{e.get('session')}`: {e.get('result')}, ${e.get('cost', 0):.4f}"
                             f"{'' if e.get('cost_known', True) else ' (unknown, charged at cap)'}, "
                             f"{e.get('turns')} turns, {e.get('duration_s')}s · model {e.get('model')} · permissions "
                             f"{e.get('permission_mode')} · session {str(e.get('session_id') or '?')[:8]}")
                lines.append(f"  - tools that ran: {tools}")
                if e.get("writes"):
                    lines.append(f"  - wrote: {', '.join(sorted(set(e['writes'])))[:300]}")
                if e.get("commands"):
                    lines.append("  - commands: " + "; ".join(f"`{c[:80]}`" for c in e["commands"][:8]))
                if e.get("denied_calls"):
                    lines.append("  - refused calls: " + "; ".join(f"{d.get('tool')} `{str(d.get('input'))[:70]}`"
                                                                   for d in e["denied_calls"][:5]))
                if mine:
                    denied = [x for x in mine if x.get("result") == "deny"]
                    lines.append(f"  - hook: {len(mine) - len(denied)} allowed, {len(denied)} denied" +
                                 "".join(f"\n    - denied {x.get('tool')} `{str(x.get('input', ''))[:80]}`: "
                                         f"{x.get('reason')}" for x in denied[:5]))
            elif step == "guard":
                files = e.get("files", [])
                lines.append(f"- guard ({e.get('role')}) {e.get('check')}: " +
                             (f"⚠ {e.get('result')}: {', '.join(files[:8])}" if files else "✔ ok"))
            elif step == "review":
                lines.append(f"- verdict: **{e.get('result')}**" + (f" — {str(e.get('evidence', ''))[:200]}"
                                                                     if e.get("evidence") else ""))
                lines += [f"  - [{i.get('severity', '?')}] {i.get('location', '?')}: {i.get('problem', '')}"
                          for i in e.get("issues", []) if isinstance(i, dict)][:8]
            elif step == "test-gate":
                lines.append(f"- test gate: {e.get('result')} ({e.get('baseline')} → {e.get('now')} tests"
                             + (f"; uncollected: {', '.join(e['uncollected'])}" if e.get("uncollected") else "")
                             + (f"; {e['note']}" if e.get("note") else "") + ")")
            elif step == "verify":
                lines.append(f"- verify: {e.get('result')} (exit {e.get('exit_code')})")
            elif step == "feedback":
                lines.append("- fed to this maker: " + str(e.get("text", "")).replace("\n", " ")[:300])
    lines += ["", "## Files", "",
              "`events.jsonl` (every step), `a<N>-<role>.prompt.txt` and `a<N>-<role>.transcript.jsonl` (full session "
              "transcripts). Print this report again with `python scripts/harness.py report " + run_id + "`."]
    return "\n".join(lines) + "\n"


def _run_anchors(h: Harness) -> dict[str, dict]:
    """run id -> {outcome, report_sha256} recorded on the features in feature_list.json."""
    return {r["run"]: r for f in h.load_features() for r in f.get("loop_runs", []) or []
            if isinstance(r, dict) and r.get("run")}


def _report_status(h: Harness, name: str, anchors: dict) -> str:
    report = h.hdir / "runs" / name / "report.md"
    if name not in anchors:
        return "unverified: not recorded in feature_list.json"
    if not report.is_file():
        return "unverified: report.md is missing"
    if hashlib.sha256(report.read_bytes()).hexdigest() != anchors[name].get("report_sha256"):
        return "TAMPERED: report.md changed after the run (hash differs from feature_list.json)"
    return "verified"


def cmd_report(h: Harness, args) -> int:
    """Print a loop run's report (latest recorded run by default). A report.md whose sha256 matches the one recorded
    in feature_list.json at the end of the run is printed as is; anything else is rebuilt from events.jsonl and marked."""
    runs, anchors, root = [p.name for p in _run_dirs(h)], _run_anchors(h), h.hdir / "runs"
    if args.list:
        for name in runs:
            end = next((e for e in reversed(read_run_events(root / name)) if e.get("step") == "run-end"), {})
            print(f"{name}  {end.get('outcome', 'unfinished'):<10} [{_report_status(h, name, anchors)}] "
                  f"{end.get('detail', '')}")
        return 0 if runs else (print("No loop runs yet.") or 0)
    if args.run and args.run not in runs:
        raise SystemExit(f"ERROR: no run {args.run}." + (f" Runs: {', '.join(runs[-5:])}" if runs else " No runs yet."))
    if not runs:
        print("No loop runs yet: `python scripts/harness.py loop` writes them to .harness/runs/.")
        return 0
    recorded = [r for r in runs if r in anchors]
    name = args.run or (recorded or runs)[-1]  # a planted, unrecorded run folder never becomes "the latest"
    status = _report_status(h, name, anchors)
    if status == "verified":
        print(f"[verified] report.md matches the sha256 recorded for {name} in feature_list.json.\n")
        print((root / name / "report.md").read_text(encoding="utf-8"))
        return 0
    print(f"[{status}] Rebuilt from events.jsonl, which is not verified either.\n")
    events = [e for e in read_run_events(root / name) if e.get("step") != "log-integrity"]  # a forged "ok" line
    print(build_run_report(events, name))
    return 1 if status.startswith("TAMPERED") else 0


# --------------------------------------------------------- loop: integrity ---

def _scope_globs(f: dict) -> list[str]:
    scope = f.get("scope") or []
    return scope if isinstance(scope, list) else [scope]


def _harness_owned(rel: str, scope: list[str]) -> bool:
    """PROTECTED files are never the maker's; the other INTEGRITY_FILES are, when the feature's scope names them."""
    if any(fnmatch.fnmatch(rel, p) for p in PROTECTED):
        return True
    return rel in INTEGRITY_FILES and not any(fnmatch.fnmatch(rel, g) for g in scope)


def _guarded_files(h: Harness, f: dict) -> set[str]:
    """Everything an agent session must leave alone: the feature list, every file under .harness/ and .claude/
    (settings, hook config, baselines, local settings) and the harness-owned top-level files."""
    files = {h.cfg["feature_list"]} | {p for p in INTEGRITY_FILES if _harness_owned(p, _scope_globs(f))}
    for d in (h.hdir, h.root / ".claude"):
        if d.is_dir():
            files |= {p.relative_to(h.root).as_posix() for p in d.rglob("*") if p.is_file()}
    # run logs are written by the harness and its hook while the session runs
    return {p for p in files - LOOP_RUNTIME_STATE if not p.startswith(".harness/runs/")}


def _integrity_snapshot(h: Harness, f: dict) -> dict[str, bytes | None]:
    snap = {}
    for rel in _guarded_files(h, f):
        p = h.root / rel
        snap[rel] = p.read_bytes() if p.is_file() else None
    return snap


def _integrity_restore(h: Harness, f: dict, snap: dict[str, bytes | None]) -> list[str]:
    """Undo whatever an agent session changed, created or deleted among the guarded files; returns those paths."""
    restored = []
    for rel in sorted(set(snap) | _guarded_files(h, f)):
        before, p = snap.get(rel), h.root / rel
        now = p.read_bytes() if p.is_file() else None
        if now == before:
            continue
        if before is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(before)
        restored.append(rel)
    return restored


def _loop_drift(h: Harness, f: dict) -> list[str]:
    """Changes outside the feature's scope since its activation, including new files in dot-directories
    (which `changed_files` skips)."""
    scope = _scope_globs(f)
    if not scope:
        return []
    allowed = scope + list(h.cfg.get("scope_exempt", [])) + _harness_paths(h)
    base = f.get("activated_commit") if f.get("status") == "active" else None  # not started yet: compare with HEAD
    names = set(h.git("diff", "--name-only", base or "HEAD").splitlines())
    names |= {r for r in h.git("ls-files", "--others", "--exclude-standard").splitlines()
              if not any(part in SKIP_DIRS or part in IDE_DIRS for part in r.split("/")[:-1])}
    return sorted(n for n in names if n and not is_generated(n) and not any(fnmatch.fnmatch(n, g) for g in allowed))


def _harness_paths(h: Harness) -> list[str]:
    """Written by the harness itself (activation, handoff, traces) whatever `scope_exempt` says; the integrity
    snapshot guards them, never the scope revert."""
    return [h.cfg["feature_list"], "PROGRESS.md"] + PROTECTED + sorted(LOOP_RUNTIME_STATE)


def _revert_paths(h: Harness, base: str, paths: list[str]) -> None:
    """Put files back as they are in `base` (deleting the ones that did not exist there)."""
    for rel in paths:
        if any(fnmatch.fnmatch(rel, g) for g in _harness_paths(h)):
            continue
        res = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=h.root, capture_output=True)
        p = h.root / rel
        if res.returncode == 0:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(res.stdout)
        elif p.is_file():
            p.unlink()


# ---------------------------------------------------- loop: test collection ---

TEST_COUNT_RES = [r"\bRan (\d+) tests?\b", r"\b(\d+) passed\b", r"test result: \w+\. (\d+) passed", r"\bPassed:\s+(\d+)",
                  r"\bTests run: (\d+)", r"\bTests:\s+(\d+) passed"]
# test-named code files only: helpers, conftest.py, __init__.py and fixtures under tests/ are not tests
TEST_FILE_RE = re.compile(
    r"(^|/)test_[^/]+\.(py|rb)$|_test\.(py|go|rb)$|\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)$|_spec\.rb$"
    r"|[^/]Tests?\.(java|kt|cs|fs|vb|php|scala|swift)$|(^|/)tests/[^/]+\.rs$")
# what makes a test a test, and what switches one off (heuristics for the checker guard and `verify` warnings)
ASSERT_RE = (r"\bassert\w*|\bexpect\s*\(|\.should\b|\bt\.(Error|Fatal|Fail)\w*\(|\bassertThat\b|\bverify\s*\("
             r"|\bpanic!?\s*\(|\bself\.fail\(|\bpytest\.raises\b|\bassert_eq!|\bShould\w*\(")
SKIP_RE = (r"@(unittest\.)?skip\w*|\bunittest\.skip\w*|pytest\.mark\.(skip\w*|xfail)|pytest\.skip\(|\.skip\s*\(|\bxit\s*\("
           r"|\bxdescribe\s*\(|\bit\.todo\b|\bt\.Skip\w*\(|@Disabled\b|@Ignore\b|#\[ignore\]|\[Ignore\]")


def count_tests(h: Harness) -> int | None:
    """How many tests the project's own test command runs (unittest, pytest, jest/vitest, cargo, dotnet, maven).
    None when no test command is configured or its output has no recognizable count."""
    total, seen = 0, False
    for c in h.cfg["commands"].get("test", []):
        _, out, _ = h.run_shell(c["cmd"], c.get("cwd", "."))
        nums = [int(n) for pattern in TEST_COUNT_RES for n in re.findall(pattern, out)]
        if nums:
            # unittest's "Ran N" includes skipped tests (pytest/jest "N passed" already excludes them)
            skipped = [int(n) for n in re.findall(r"\bskipped=(\d+)", out)] if re.search(TEST_COUNT_RES[0], out) else []
            total, seen = total + max(nums) - max(skipped, default=0), True
    return total if seen else None


def _test_files(h: Harness) -> set[str]:
    names = h.git("ls-files").splitlines() + h.git("ls-files", "--others", "--exclude-standard").splitlines()
    return {p for p in names if TEST_FILE_RE.search(p) and (h.root / p).is_file()}


def _gate_baseline(h: Harness, f: dict) -> tuple[int | None, set[str]]:
    """Test-count baseline for the feature, persisted at its first loop run so a rerun cannot launder an uncollected
    test file into the baseline. A feature activated outside the loop gets a baseline only from a clean tree."""
    path = h.hdir / "loop" / f"baseline-{re.sub(r'[^A-Za-z0-9_.-]', '_', f['id'])}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("count"), set(data.get("files", []))
    dirty_tests = [p for p in changed_files(h, f.get("activated_commit") or "HEAD") if TEST_FILE_RE.search(p)]
    if dirty_tests:
        print(f"   note: test gate inactive for {f['id']}: test files changed before the loop's first run "
              f"({', '.join(dirty_tests[:5])}).")
        return None, set()
    count, files = count_tests(h), _test_files(h)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": count, "files": sorted(files), "commit": h.head()}), encoding="utf-8")
    return count, files


def _test_gate(h: Harness, baseline: int | None, baseline_files: set[str]) -> tuple[str | None, dict]:
    """Mechanical gate (review feedback promoted to a check, lesson 10): the project's test command must run at least
    as many tests as before, and every test file created since the baseline must add tests it actually runs."""
    if baseline is None:
        return None, {"result": "skipped", "note": "no baseline (test files changed before the first loop run)"}
    now = count_tests(h)
    info = {"baseline": baseline, "now": now, "new_files": sorted(_test_files(h) - baseline_files)}
    if now is None:  # unrecognizable output: the test step of verify judges it
        return None, {**info, "result": "skipped", "note": "test output has no recognizable count"}
    cmds = ", ".join(f"`{c['cmd']}`" for c in h.cfg["commands"].get("test", []))
    if now < baseline:
        return (f"the project's test command {cmds} ran {baseline} test(s) before and {now} now: tests were deleted, "
                f"skipped or broken. Never delete, skip or loosen tests."), {**info, "result": "fail"}
    uncollected = []
    for rel in info["new_files"]:
        p = h.root / rel
        hidden = p.with_name(p.name + ".loop-hidden")
        p.rename(hidden)
        try:
            without = count_tests(h)
        finally:
            hidden.rename(p)
        if without is not None and without >= now:
            uncollected.append(rel)
    if not uncollected:
        return None, {**info, "result": "pass"}
    return (f"new test file(s) {', '.join(uncollected)} are not run by the project's test command {cmds}: the number "
            f"of tests it runs is the same with or without them. Write them in the style that command collects "
            f"(e.g. unittest.TestCase classes for `python -m unittest`)."), {**info, "result": "fail",
                                                                             "uncollected": uncollected}


# ------------------------------------------------------------ loop: driver ---

def _after_session(h: Harness, f: dict, snap: dict, role: str, run: LoopRun) -> str | None:
    """The real gate after every agent session: restore harness state; the maker must stay inside the scope.
    Both are undone by the harness, so the next attempt starts clean."""
    restored = _integrity_restore(h, f, snap)
    run.event("guard", "restored" if restored else "ok", role=role, check="integrity", files=restored[:50])
    if restored:
        print(f"   INTEGRITY: {role} changed harness-owned files; restored: {', '.join(restored[:10])}")
        h.trace("loop", "integrity-restored", role=role, feature=f["id"], files=restored[:20])
        return (f"you changed harness-owned files ({', '.join(restored[:10])}); they were restored and the attempt "
                f"rejected. Never edit them.")
    drift = _loop_drift(h, f) if role == "maker" else []
    if role == "maker":
        run.event("guard", "reverted" if drift else "ok", role=role, check="scope", files=drift[:50])
    if drift:
        _revert_paths(h, f.get("activated_commit") or "HEAD", drift)
        print(f"   SCOPE: changes outside {_scope_globs(f)} reverted: {', '.join(drift[:10])}")
        h.trace("loop", "scope-drift", feature=f["id"], files=drift[:20])
        return (f"you changed files outside the feature's scope {_scope_globs(f)}: {', '.join(drift[:10])}. The "
                f"harness reverted them; do the feature without touching files outside the scope.")
    return None


def _review(h: Harness, args, f: dict, fields: dict, settings_rel: str, spent: list, runs: list,
            run: LoopRun) -> dict:
    note, review = "", {}
    for _ in (1, 2):  # one retry when the reviewer reached for a command it may not run
        snap = _integrity_snapshot(h, f)
        review = _run_agent(h, args, "reviewer", REVIEW_PROMPT.format(**fields) + note, settings_rel, spent[0], run)
        spent[0] += review["cost"]
        runs.append(review)
        tampered = _after_session(h, f, snap, "reviewer", run)
        review["verdict"] = None if tampered else ((review["structured"] or {}).get("verdict") if review["ok"] else None)
        review["tampered"] = tampered
        structured = review["structured"] if isinstance(review["structured"], dict) else {}
        run.event("review", review["verdict"] or "none", issues=structured.get("issues") or [],
                  evidence=str(structured.get("evidence") or "")[:500], denials=review["denials"])
        print(f"   reviewer: {review['subtype']}, verdict={review['verdict']} (${review['cost']:.4f})")
        if review["verdict"] == "pass" or not review["denials"] or spent[0] >= args.max_total_usd:
            break
        note = ("\n\nYour previous review was blocked because these commands are not permitted: "
                + "; ".join(review["denials"][:5]) + "\nJudge only with the permitted commands: "
                + ", ".join(t for t in _allowed_tools(h, "reviewer") if t.startswith("Bash(")) + ".")
    return review


def _pass_gate(h: Harness, f: dict) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = cmd_verify(h, argparse.Namespace(id=f["id"], skip_check=False))
        except SystemExit as exc:
            print(exc)
            rc = 1
    print(buf.getvalue().rstrip())
    return rc, buf.getvalue()


def _loop_feature(h: Harness, args, f: dict, settings_rel: str, spent: list, run: LoopRun) -> tuple[str, str]:
    """Run maker -> reviewer -> gates -> verify until pass or attempts run out. Returns (outcome, detail)."""
    fields, feedback, runs = _prompt_fields(h, f), "", []
    baseline_tests, baseline_files = _gate_baseline(h, f)
    run.event("baseline", "ok" if baseline_tests is not None else "none", tests=baseline_tests,
              test_files=len(baseline_files))
    for attempt in range(1, args.max_attempts + 1):
        run.attempt = attempt
        print(f"\n== {f['id']} attempt {attempt}/{args.max_attempts}: MAKER ==")
        block = f"\nThe previous attempt was rejected. Fix exactly this:\n{feedback}\n" if feedback else ""
        if feedback:
            run.event("feedback", "sent", text=feedback)
        snap = _integrity_snapshot(h, f)
        maker = _run_agent(h, args, "maker", MAKER_PROMPT.format(feedback=block, **fields), settings_rel, spent[0], run)
        spent[0] += maker["cost"]
        runs.append(maker)
        print(f"   maker: {maker['subtype']} (${maker['cost']:.4f}, {maker['turns']} turns)")
        violation = _after_session(h, f, snap, "maker", run)
        if spent[0] >= args.max_total_usd:
            return "budget", f"total budget ${args.max_total_usd} reached after the maker run"
        if violation:
            feedback = f"- {violation}"
            continue
        if not maker["ok"]:
            feedback = f"- the maker session ended with {maker['subtype']}; make a smaller, complete change"
            continue
        print(f"== {f['id']} attempt {attempt}: REVIEWER (fresh session, read-only) ==")
        review = _review(h, args, f, fields, settings_rel, spent, runs, run)
        if review["tampered"]:
            return "tools", f"the reviewer changed harness-owned files ({review['tampered']})"
        if spent[0] >= args.max_total_usd and review["verdict"] != "pass":
            return "budget", f"total budget ${args.max_total_usd} reached after the review"
        if review["verdict"] != "pass" and review["denials"]:
            # the reviewer could not run its checks: a harness (tools) problem, not the maker's fault
            return "tools", "the reviewer was denied: " + "; ".join(review["denials"][:5])
        if review["verdict"] != "pass":
            feedback = _fmt_issues((review["structured"] or {}).get("issues")) if review["ok"] else \
                f"- the review session ended with {review['subtype']}"
            continue
        record_review(h, f, "APPROVE", "loop reviewer", run=run.id)  # what the pass-gate's review gate reads
        gate, gate_info = _test_gate(h, baseline_tests, baseline_files)
        run.event("test-gate", gate_info.pop("result"), **gate_info)
        if gate:
            print(f"== {f['id']} attempt {attempt}: TEST GATE failed ==\n   {gate}")
            h.trace("loop", "test-gate", feature=f["id"])
            feedback = f"- {gate}"
            continue
        print(f"== {f['id']} attempt {attempt}: PASS-GATE (harness verify) ==")
        rc, gate_out = _pass_gate(h, f)
        run.event("verify", "pass" if rc == 0 else "fail", exit_code=rc, output_tail=tail(gate_out, 15))
        if rc == 0:
            feats = h.load_features()
            done = h.find_feature(feats, f["id"])
            done["evidence"]["loop"] = {"attempts": attempt, "cost_usd": round(sum(r["cost"] for r in runs), 6),
                                        "runs": [{k: r[k] for k in ("role", "subtype", "cost", "turns")} for r in runs],
                                        "reviewer_evidence": ((review["structured"] or {}).get("evidence") or "")[:500]}
            h.save_features(feats)
            return "passing", f"{attempt} attempt(s), ${sum(r['cost'] for r in runs):.4f}"
        feedback = "- the reviewer passed it but the harness pass-gate failed:\n" + tail(gate_out, 25)
    return "exhausted", feedback.strip()


def _finish_feature(h: Harness, args, f: dict, detail: str, spent: list, run: LoopRun) -> int:
    """Handoff and the approval decision. The commit itself comes after the run's report is anchored, so it includes
    the report hash in feature_list.json."""
    cmd_handoff(h, argparse.Namespace())
    print(f"\n[PASS] {f['id']} via loop ({detail}). Total spent ${spent[0]:.4f}.")
    if not args.commit:
        run.event("approval", "human", note="left uncommitted for human review")
        print("Human approval: review the diff (`git diff`), then commit it yourself, or rerun with --commit.")
        return 0
    if cmd_exit_check(h, argparse.Namespace(no_run=True, full=False)) != 0:
        run.event("approval", "refused", note="exit-check not clean")
        print("Not committing: the repo is not in a clean state.")
        return 1
    run.event("approval", "commit", note="pre-approved with --commit; committed once the report is anchored")
    return 0


def _commit_feature(h: Harness, f: dict, detail: str, run: LoopRun) -> int:
    # run logs hold transcripts and local paths: excluded explicitly, since a session could blank runs/.gitignore
    h.git("add", "-A", "--", ".", ":(exclude).harness/runs")
    msg = (f"feat({f['id']}): {str(f.get('behavior', ''))[:60]}\n\nAutomated by `harness.py loop`: maker -> "
           f"independent reviewer -> verify passed ({detail}). Run log: {run.id}.")
    res = subprocess.run(["git", "commit", "-q", "-m", msg], cwd=h.root, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        h.trace("loop", "commit-failed", feature=f["id"], run=run.id)
        print(f"ERROR: commit failed: {tail(res.stderr or res.stdout, 5)}")
        return 1
    h.trace("loop", "committed", feature=f["id"], run=run.id, commit=h.head())
    print(f"Committed {h.head()}.")
    return 0


def _anchor_run(h: Harness, fid: str, run: LoopRun, outcome: str) -> None:
    """Record the report's sha256 on the feature in feature_list.json. The file is git-tracked, so a later change to
    the anchor shows in `git diff`, and `report` can tell a report rewritten after the run."""
    try:
        feats = h.load_features()
        f = h.find_feature(feats, fid)
    except (SystemExit, ValueError, OSError) as exc:
        print(f"WARNING: could not anchor run {run.id} in the feature list ({exc}); its report stays unverified.")
        return
    runs = [r for r in f.get("loop_runs", []) if isinstance(r, dict)][-9:]
    runs.append({"run": run.id, "outcome": outcome, "report_sha256": run.report_sha256})
    f["loop_runs"] = runs
    h.save_features(feats)


def _stop(h: Harness, args, f: dict, outcome: str, detail: str, spent: list) -> int:
    if outcome == "budget":
        print(f"\nSTOPPED: {detail}. {f['id']} stays active; review the work and rerun or block it.")
        return 3
    if outcome == "tools":
        cmd_fail(h, argparse.Namespace(layer="tools", feature=f["id"],
                                       note=f"auto (loop): {detail[:250]}. Allow the command in commands.* of "
                                            f".harness/config.json or adjust the loop's allowed tools."))
        print(f"\nSTOPPED: {detail}.\n  WHY: a review that cannot run its checks says nothing about the work.\n"
              f"  FIX: make the command available (commands.* in .harness/config.json), then rerun. "
              f"{f['id']} stays active.")
        return 4
    cmd_block(h, argparse.Namespace(id=f["id"], reason=f"loop: {args.max_attempts} attempts rejected: {detail[:300]}"))
    cmd_fail(h, argparse.Namespace(layer="feedback", feature=f["id"],
                                   note=f"auto (loop): {args.max_attempts} attempts rejected; check the feature's "
                                        f"scope/verification or the instructions. Last: {detail[:200]}"))
    print(f"\nBLOCKED: {f['id']} after {args.max_attempts} attempts. Spent ${spent[0]:.4f}.")
    return 1


def cmd_loop(h: Harness, args) -> int:
    """Autonomous loop over the feature list: one feature at a time, maker -> reviewer -> verify (lesson 13)."""
    if args.max_attempts < 1 or args.max_features < 1 or args.max_total_usd <= 0:
        raise SystemExit("ERROR: --max-attempts and --max-features must be >= 1 and --max-total-usd > 0.")
    f = _pick_loop_feature(h, args.feature)
    if f is None:
        print("No eligible feature: the backlog is empty or waiting on dependencies.")
        return 0
    if args.dry_run:
        fields = _prompt_fields(h, f)
        for role, prompt in (("maker", MAKER_PROMPT.format(feedback="", **fields)),
                             ("reviewer", REVIEW_PROMPT.format(**fields))):
            argv = _agent_argv(h, args, role, ".harness/loop/settings.json", _cap(args, role))
            print(f"== {role.upper()} (dry run, nothing executed; prompt on stdin) ==\n{' '.join(argv)}\n\n{prompt}\n")
        return 0
    base = _agent_base(args.agent_cmd)
    if not _agent_available(base):
        print(f"ERROR: agent CLI `{base[0]}` not found.\n  FIX: install Claude Code and log in (`claude` then /login), "
              f"or pass --agent-cmd.")
        return 2
    settings_rel, spent, done = _guard_settings(h), [0.0], 0
    while f is not None and done < args.max_features:
        dirty = _loop_drift(h, f)
        if dirty:
            print(f"STOPPED before {f['id']}: the working tree has changes outside its scope: {', '.join(dirty[:10])}\n"
                  f"  WHY: the loop judges only this feature's changes; unrelated edits would be blamed on it.\n"
                  f"  FIX: commit or stash them (or add them to .gitignore), then rerun.")
            return 5
        if not _scope_globs(f):
            print(f"   note: {f['id']} declares no --scope, so the scope checks are off (harness files are still "
                  f"guarded). Declare one with `add --scope`.")
        if f.get("status") == "not_started":
            if cmd_next(h, argparse.Namespace(id=f["id"], force=False)) != 0:
                return 1
            f = h.find_feature(h.load_features(), f["id"])
        run, spent_before = LoopRun(h, f["id"]), spent[0]
        run.event("run-start", "ok", feature=f["id"], behavior=f.get("behavior", ""), scope=_scope_globs(f),
                  verification=f.get("verification", ""), base=f.get("activated_commit") or "",
                  active_features=[x["id"] for x in h.load_features() if x.get("status") == "active"],
                  limits={"max_attempts": args.max_attempts, "budget_maker": args.budget_maker,
                          "budget_reviewer": args.budget_reviewer, "max_total_usd": args.max_total_usd},
                  agent=_agent_base(args.agent_cmd)[0], model=args.model, reviewer_model=args.reviewer_model)
        print(f"Run log: {run.dir.relative_to(h.root).as_posix()}/")
        outcome, detail, rc = "crashed", "", 1
        try:
            outcome, detail = _loop_feature(h, args, f, settings_rel, spent, run)
            h.trace("loop-feature", outcome, feature=f["id"], run=run.id, detail=detail[:300],
                    total_cost_usd=round(spent[0], 4))
            rc = _stop(h, args, f, outcome, detail, spent) if outcome != "passing" else \
                _finish_feature(h, args, f, detail, spent, run)
        except BaseException as exc:  # crash or Ctrl-C: the log still gets its end record and a report
            outcome, detail = "crashed", f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            try:
                status = h.find_feature(h.load_features(), f["id"]).get("status")
            except (SystemExit, ValueError, OSError):  # never hide the original error behind a broken feature list
                status = "unknown"
            run.event("run-end", "ok" if outcome != "crashed" else "crashed", outcome=outcome, detail=detail[:300],
                      attempts=run.attempt, spent_usd=round(spent[0] - spent_before, 6),
                      run_total_usd=round(spent[0], 6), status_after=status)
            try:
                report = run.finish()
                _anchor_run(h, f["id"], run, outcome)
                print("\n" + "\n".join(run.checklist))
                print(f"\nReport: {report.relative_to(h.root).as_posix()}  (python scripts/harness.py report {run.id})")
            except Exception as exc:  # a broken report must not replace the run's own outcome or crash
                print(f"WARNING: could not write the report for {run.id}: {exc}")
        if outcome == "passing" and rc == 0 and args.commit:
            rc = _commit_feature(h, f, detail, run)
        if outcome != "passing" or rc != 0:
            return rc
        done += 1
        if not args.commit and done < args.max_features:
            # uncommitted, unapproved work would be judged as the next feature's out-of-scope changes
            print("Stopping after one feature without --commit: approve and commit it, then rerun.")
            break
        f = _pick_loop_feature(h, None) if done < args.max_features else None
    return 0


def cmd_try(h: Harness, args) -> int:
    """Run a feature's verification command without changing any state (for agents and humans)."""
    f = h.find_feature(h.load_features(), args.id)
    ver = f.get("verification")
    if not isinstance(ver, str) or not ver.strip():
        raise SystemExit(f"ERROR: {f['id']} has no verification command.")
    code, out, dur = h.run_shell(ver, f.get("cwd", "."))
    print(tail(out, 40))
    print(f"[{'PASS' if code == 0 else 'FAIL'}] {f['id']} verification exited {code} after {dur:.1f}s (no state changed).")
    h.trace("try", "pass" if code == 0 else "fail", feature=f["id"], exit_code=code, epoch=time.time())
    return 0 if code == 0 else 1


def _rel_to_root(h: Harness, raw: str) -> str | None:
    if not raw:
        return None
    if os.name == "nt":  # Git Bash / MSYS spelling of a Windows path: /c/Users/... -> C:/Users/...
        raw = re.sub(r"^/([a-zA-Z])/", lambda m: m.group(1).upper() + ":/", raw)
    p = Path(raw)
    p = (p if p.is_absolute() else (h.root / p)).resolve()
    try:
        return p.relative_to(h.root.resolve()).as_posix()
    except ValueError:
        return None


def _log_hook(h: Harness, role: str, tool: str, tin: dict, reason: str | None) -> None:
    """Append the decision to the loop run's events.jsonl (only for a run directory of this project)."""
    raw = os.environ.get("HARNESS_RUN", "")
    if not raw:
        return
    run_dir = Path(raw).resolve()
    if run_dir.parent != (h.hdir / "runs").resolve() or not run_dir.is_dir():
        return
    session = os.environ.get("HARNESS_SESSION", "")
    m = re.match(r"a(\d+)-", session)
    shown = tin.get("command") or tin.get("file_path") or tin.get("notebook_path") or ""
    rec = {"ts": now_iso(), "step": "hook", "result": "deny" if reason else "allow", "attempt": int(m.group(1)) if m
           else 0, "session": session, "role": role, "tool": tool, "input": str(shown)[:200], "reason": reason}
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def cmd_hook_pretool(h: Harness, args) -> int:
    """PreToolUse guard for `loop` sessions (passed via --settings). Exit 2 + stderr denies the tool call.
    Best effort: the post-session integrity and scope checks in `loop` are the real gate."""
    if getattr(args, "root", None):
        h = Harness(Path(args.root))
    try:
        payload = json.loads(_stdin_text() or "{}")
    except (ValueError, OSError):
        payload = {}
    role, fid = os.environ.get("HARNESS_ROLE", "reviewer"), os.environ.get("HARNESS_FEATURE", "")  # fail closed
    tool, tin = payload.get("tool_name", ""), payload.get("tool_input") or {}
    feats = h.load_features()
    f = next((x for x in feats if x.get("id") == fid), {}) or {}
    reason = None
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        rel = _rel_to_root(h, tin.get("file_path") or tin.get("notebook_path") or "")
        scope = _scope_globs(f)
        if role != "maker":
            reason = "the reviewer is read-only"
        elif rel is None:
            reason = "writing outside the repository is not allowed"
        elif _harness_owned(rel, scope):
            reason = f"{rel} is harness state; only the harness changes it"
        elif scope and not any(fnmatch.fnmatch(rel, g) for g in
                               scope + [g for g in h.cfg.get("scope_exempt", []) if g not in PROTECTED]):
            reason = f"{rel} is outside {fid}'s scope {scope}; add a separate feature for it"
    elif tool in ("Bash", "PowerShell"):
        command = str(tin.get("command", ""))
        flat = command.replace('"', " ").replace("'", " ")
        unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "''", command)
        checks = [(p, why, flat) for p, why in DENIED_COMMANDS + DENIED_FILE_WRITES] + \
            [(p, why, unquoted) for p, why in DENIED_REDIRECTS]
        reason = next((why for pattern, why, text in checks if re.search(pattern, text)), None)
    h.trace("hook-pretool", "deny" if reason else "allow", role=role, feature=fid, tool=tool)
    _log_hook(h, role, tool, tin, reason)
    if reason:
        sys.stderr.write(f"Blocked by the harness: {reason}.")
        return 2
    return 0


# ------------------------------------------------------- interactive hooks ---
# v2: hooks for interactive Claude Code sessions, called through .claude/hooks/harness_hook.py. They inject only
# dynamic state (the rules live in AGENTS.md), never return "allow", and stay cheap (no test runs): the gates are
# still `verify` and `exit-check`. Lessons 03/05 (state at session start, compaction), 07/08 (WIP, scope, pass-gate),
# 11 (activity trace), 12 (clean state).

HOOK_STATE_FILES = {".harness/trace.jsonl", ".harness/last-check.json", ".harness/session.json",
                    ".harness/activity.jsonl", ".harness/hook-state.json", ".harness/compact-snapshot.md",
                    ".harness/failures.jsonl", ".harness/reviews.jsonl", ".harness/incidents.jsonl"}
GUARDED_FEATURE_FIELDS = ("status", "evidence", "attempts", "loop_runs")
# how a feature is judged: fixed once it is active or passing, or editing it would bypass the pass-gate
LOCKED_WHEN_STARTED = ("verification", "verify_check", "scope", "cwd", "activated_commit", "activated_at")
# files that turn the harness itself off or redirect it: an edit needs the user's approval
GUARD_CONFIG_FILES = {".harness/config.json", ".claude/settings.json", ".claude/settings.local.json",
                      ".claude/hooks/harness_hook.py"}
SHELL_WRITE_RE = (r"(>>?|\btee\b|\bsed\s+(-[a-zA-Z]*i|--in-place)|Set-Content|Add-Content|Out-File|"
                  r"\[(?:System\.)?IO\.File\]::\w+|\b(?:Write|Append)All(?:Text|Lines|Bytes)\b|"
                  r"\bgit\s+(checkout|restore)\b)[^|;&\n]*")
SHELL_COPY_RE = r"\b(cp|mv|Copy-Item|Move-Item)\b[^|;&\n]*[\s/]"  # the target is the destination: the last argument

# Switching checks off is the user's action, never the agent's: always denied, in every configuration (V015; survey of
# nah, ECC block-no-verify, 2026-10-16). Matched on the command with quoted prose removed, plus `-c "..."` payloads.
_GIT_SUB = r"\bgit(?:\.exe)?(?:\s+(?:-[Cc]\s*\S+|--[\w-]+(?:=\S+)?))*\s+"  # git, its global options, then the subcommand
# `--no-veri…`: git accepts any unambiguous prefix of --no-verify (`--no-ver` alone is ambiguous with --no-verbose)
BYPASS_PATTERNS = [
    (_GIT_SUB + r"commit\b[^|;&\n]*\s(--no-veri\w*|-(?!-)[a-zA-Z]*n[a-zA-Z]*\b)",
     "`git commit --no-verify`/`-n` skips the repository's commit hooks"),
    (_GIT_SUB + r"(push|merge|rebase|am|cherry-pick|revert|pull|switch|checkout)\b[^|;&\n]*\s--no-veri\w*",
     "`--no-verify` skips the repository's git hooks"),
    (r"\bcore\.hookspath\b", "changing core.hooksPath switches the repository's git hooks off"),
    (r"\bGIT_CONFIG_(COUNT|PARAMETERS|KEY_\d+|VALUE_\d+)\b",
     "GIT_CONFIG_* variables rewrite git's configuration (and its hooks) for the command"),
    (r"\bclaude(\.exe)?\b[^|;&\n]*\s--(safe-mode|bare)\b", "starting Claude Code without its hooks bypasses the harness"),
    (r"(?:^|\s|\$env:)HUSKY\s*=\s*['\"]?0\b", "HUSKY=0 skips the repository's git hooks"),
    (r"(?:^|\s|\$env:)SKIP\s*=\s*\S+.*\bgit\b", "pre-commit's SKIP= skips the repository's hooks"),
    (r"(?:\b(?:rm|del|erase|rmdir|rd|remove-item|ri|chmod|mv|move|move-item|unlink|truncate|tee|set-content|add-content|"
     r"out-file|new-item)\b|\bsed\s+-\S*i|>)[^|;&\n]*\.git[\\/]+hooks\b",
     "writing, moving or deleting files in .git/hooks switches the repository's git hooks off"),
    (r"\b(?:cp|copy|copy-item|ln)\b[^|;&\n]*\s\S*\.git[\\/]+hooks\S*\s*$",  # copying INTO .git/hooks (a backup out is fine)
     "writing files in .git/hooks replaces the repository's git hooks"),
]
# a `git` subcommand that runs hooks, for `--no-verify` given through a variable or anywhere on its line
_GIT_HOOKED = _GIT_SUB + r"(?:commit|push|merge|rebase|am|cherry-pick|revert|pull|switch|checkout)\b"
# matched on the raw command (they may sit inside quoted JSON or an echo); disableAllHooks only where it lands in a
# settings file (2.3.1: a note in docs/ that mentions it is not a bypass)
BYPASS_RAW_PATTERNS = [
    (r"(?:^|[\s;&|(]|\$env:|\bexport\s+|\bset\s+)CLAUDE_CONFIG_DIR\s*=",
     "pointing Claude Code at another configuration folder bypasses this project's hooks"),
]
DISABLE_HOOKS_RE = r"[\"']?disableAllHooks[\"']?\s*\]?\s*[:=]\s*\$?true\b"
SETTINGS_FILE_RE = r"settings(?:\.local)?\.json|\bclaude\s+config\b"
# hook commands are run by Claude Code with its own payload; an agent running them could forge a verdict (2.3.1). Any
# mention of a hook subcommand (also through `python -c "...main(['hook-subagent-stop'])"`) or executing the dispatcher
# with an event; reading or staging the dispatcher file is fine. A guardrail: the SubagentStop record is also verified.
HOOK_COMMAND_RE = (r"\bhook-(?:subagent-stop|session-start|session-end|stop|guard|activity|precompact|prompt|pretool)\b"
                   r"|harness_hook\.py[\"']?\s+(?:subagent-stop|session-start|session-end|stop|guard|activity|precompact"
                   r"|prompt|statusline)\b")
# Claude Code's own transcripts: the review gate reads them as evidence, so the agent must not write there
CLAUDE_TRANSCRIPTS_RE = r"\.claude[\\/]+projects\b"
HOOKSPATH_READ_RE = r"\bgit\b[^|;&\n]*\sconfig\b[^|;&\n]*\s(--get(-all|-regexp)?|--list|-l|--show-origin)\b"
NESTED_SHELL_RE = (r"(?:\b(?:ba|z|da)?sh|\bcmd|\bpwsh|\bpowershell)(?:\.exe)?(?:\s+-\w+)*?\s+(?:-\w*c|/c|-Command)\s+"
                   r"([\"'])(.*?)\1|\beval\s+([\"'])(.*?)\3")

# Files that define how the work is checked: editing an existing one can make a red check green (V015).
CHECKER_FILES = ["pytest.ini", "tox.ini", ".coveragerc", "mypy.ini", ".mypy.ini", ".flake8", "ruff.toml", ".ruff.toml",
                 ".pylintrc", "pylintrc", ".eslintrc*", "eslint.config.*", ".prettierrc*", "prettier.config.*",
                 "jest.config.*", "vitest.config.*", "karma.conf.*", "playwright.config.*", "cypress.config.*",
                 ".golangci.y*ml", "phpunit.xml*", "phpstan.neon*", ".rubocop.yml", "rustfmt.toml", "clippy.toml",
                 ".pre-commit-config.yaml", "lefthook.y*ml", "Jenkinsfile", "azure-pipelines.yml", ".gitlab-ci.yml",
                 ".github/workflows/*", ".circleci/*", ".husky/*", ".harness/rules.json", "conftest.py"]
# files that hold checker settings next to other things: only changes inside the checker part count
CHECKER_SECTION_RE = (r"^\s*\[\s*(tool\.(pytest|ruff|mypy|coverage|pylint|black|isort|flake8|pyright|bandit)|"
                      r"tool:pytest|flake8|mypy|coverage|pylint|pycodestyle)\b")
CHECKER_JSON_KEY_RE = (r"\"(test|lint|typecheck|type-check|check|ci|pretest|posttest|format:check|verify)[\w:-]*\"\s*:"
                       r"|\"(jest|eslintConfig|prettier|vitest|mocha|ava|nyc|c8)\"\s*:")
CHECKER_SCRIPT_RE = r"^(test|lint|typecheck|type-check|check|ci|pretest|posttest|format:check|verify)([\w:-]*)$"
CHECKER_JSON_KEYS = ("jest", "eslintConfig", "prettier", "vitest", "mocha", "ava", "nyc", "c8")


INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md")
INSTRUCTION_DIRS = (".claude/agents/", ".claude/skills/", ".claude/rules/", ".claude/output-styles/")


def _is_instruction_file(key: str) -> bool:
    return key in {_path_key(p) for p in INSTRUCTION_FILES} or key.startswith(tuple(_path_key(d) for d in INSTRUCTION_DIRS))


def _inline_script_writes(command: str, h: Harness) -> bool:
    """`python -c` / `node -e` one-liners that open a harness state file for writing (a heuristic: a guardrail, the
    gates stay `verify` and the loop's integrity check)."""
    if not re.search(r"\b(python3?|py|node)\b[^|;&\n]*(\s-[ce]\b|\s-(?:\s|$)|<<)", command):  # -c, stdin, heredoc
        return False
    names = [re.escape(h.cfg["feature_list"])] + [re.escape(n) for n in sorted(HOOK_STATE_FILES)] + [CLAUDE_TRANSCRIPTS_RE]
    flags = re.I if os.name == "nt" else 0
    text = command.replace("\\", "/")
    write = (r"""open\([^)]*,\s*['"][wax]|write_text|write_bytes|writeFileSync|appendFile|\.write\(|json\.dump\(|"""
             r"""os\.replace|os\.rename|shutil\.(copy|move)|unlink\(|rmSync|renameSync""")
    if not any(re.search(n, text, flags) for n in names) or not re.search(write, text):
        return False
    # a state file named on the line that writes, or held in a variable the script writes through (2.3.1: a script
    # that only reads .harness/trace.jsonl and writes its own report is fine)
    for line in text.splitlines():
        if re.search(write, line) and any(re.search(n, line, flags) for n in names):
            return True
    # a variable that holds a state path counts only when the script writes through that variable (round 3: a script
    # that keeps `trace = '.harness/trace.jsonl'` to read it and writes its own report is fine)
    path_var = r"\b(\w+)\s*=\s*(?:Path\(\s*)?[rbuf]?['\"][^'\"\n]*(?:{})[^'\"\n]*['\"]\s*\)?\s*$"
    for line in text.splitlines():
        for n in names:
            m = re.search(path_var.format(n), line, flags)
            if m:
                v = re.escape(m.group(1))
                if re.search(r"open\(\s*" + v + r"\b[^)]*['\"][wax]|\b" + v + r"\s*\.\s*(?:write_text|write_bytes|open\("
                             r"[^)]*['\"][wax]|unlink|rename|replace)|(?:replace|rename|copy\w*|move|dump)\([^)]*\b" + v + r"\b",
                             text):
                    return True
    return False


def _shell_writes(command: str, target_re: str) -> bool:
    flags = re.I if os.name == "nt" else 0
    return bool(re.search(SHELL_WRITE_RE + target_re, command, flags) or
                re.search(SHELL_COPY_RE + target_re + r"\s*($|[|;&\n])", command, flags))


def _normalize_shell(text: str) -> str:
    """Line continuations joined, PowerShell backtick escapes removed (`--no`-verify` is `--no-verify`)."""
    text = re.sub(r"[\\`]\r?\n", " ", text)
    return re.sub(r"`(.)", r"\1", text)


def _bypass_reason(raw: str, scan: str) -> str | None:
    """Why the shell command switches a check off (git hooks, Claude Code hooks), or None. Checked per segment, also
    inside `sh -c "…"` / `eval "…"` payloads; `--no-verify` anywhere in a git command line (e.g. in a variable)."""
    raw = _normalize_shell(raw)  # before the scan text: it turns backslashes into slashes, hiding `\` continuations
    nested = [m[1] or m[3] for m in re.findall(NESTED_SHELL_RE, raw, re.S | re.I)]
    texts = [_shell_scan_text(raw)] + [_shell_scan_text(t) for t in nested]
    for text in texts:
        segs = [seg for _, seg in _split_segments(text)]
        hooked = [seg for seg in segs if re.search(_GIT_HOOKED, seg, re.I)]
        if hooked and (any(re.search(r"(?:^|\s)--no-veri\w*", seg) for seg in hooked) or
                       any(re.search(r"^\s*\w+=['\"]?--no-veri\w*", seg) for seg in segs)):
            return "`--no-verify` skips the repository's git hooks"
        for seg in segs:
            for pattern, why in BYPASS_PATTERNS:
                if re.search(pattern, seg, re.I) and not ("hookspath" in pattern and re.search(HOOKSPATH_READ_RE, seg, re.I)):
                    return why
    if re.search(r"\balias\.\w+", raw) and re.search(r"\bcommit\b[^'\"]*\s(?:--no-veri\w*|-n)\b", raw):
        return "a git alias with --no-verify skips the repository's git hooks"
    for pattern, why in BYPASS_RAW_PATTERNS:
        if re.search(pattern, raw):
            return why
    if re.search(DISABLE_HOOKS_RE, raw, re.I) and re.search(SETTINGS_FILE_RE, raw, re.I):
        return "disableAllHooks switches every hook off, the harness included"
    return None


def _edit_bypass_reason(tool: str, tin: dict) -> str | None:
    """A Write/Edit of a settings file (in the project or not) that switches hooks off or redirects the config."""
    path = str(tin.get("file_path") or tin.get("notebook_path") or "")
    if not re.search(r"settings(?:\.local)?\.json$", path.replace("\\", "/"), re.I):
        return None
    if tool == "Write":
        text = str(tin.get("content", ""))
    elif tool == "MultiEdit":
        text = "\n".join(str(e.get("new_string", "")) for e in tin.get("edits") or [] if isinstance(e, dict))
    else:
        text = str(tin.get("new_string", tin.get("new_source", "")))
    if re.search(DISABLE_HOOKS_RE, text, re.I):
        return "disableAllHooks switches every hook off, the harness included"
    if re.search(r"\bCLAUDE_CONFIG_DIR\b", text):
        return "pointing Claude Code at another configuration folder bypasses this project's hooks"
    return None


def _package_json_checker_change(current: str, proposed: str) -> bool | None:
    """Did a package.json edit change a test/lint script or a top-level checker config? None if either side is not
    JSON (the caller then falls back to the line heuristic). Dependency versions are not checker settings."""
    try:
        old, new = json.loads(current), json.loads(proposed)
    except ValueError:
        return None
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None

    def checker_part(d: dict) -> dict:
        scripts = d.get("scripts") if isinstance(d.get("scripts"), dict) else {}
        part = {f"scripts.{k}": v for k, v in scripts.items() if re.match(CHECKER_SCRIPT_RE, k)}
        part.update({k: d[k] for k in CHECKER_JSON_KEYS if k in d})
        return part
    return checker_part(old) != checker_part(new)


_COMMENT_LINE = re.compile(r"^\s*(#|//|--|;|/\*|\*)")


def _count_matches(pattern: str, lines: list[str]) -> int:
    return sum(len(re.findall(pattern, line)) for line in lines if not _COMMENT_LINE.match(line))


def _changed_lines(old: str, new: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(index, text) of the lines an edit removes from `old` and adds in `new`."""
    a, b = old.splitlines(), new.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += [(i, a[i]) for i in range(i1, i2)]
        if tag in ("replace", "insert"):
            added += [(j, b[j]) for j in range(j1, j2)]
    return removed, added


def _section_of(lines: list[str], idx: int) -> str:
    """The `[section]` header an INI/TOML line belongs to."""
    for k in range(min(idx, len(lines) - 1), -1, -1):
        if re.match(r"^\s*\[", lines[k]):
            return lines[k]
    return ""


def _weakens_checker(h: Harness, rel: str, tool: str, tin: dict) -> str | None:
    """Why an edit of an existing file can make a red check green (lint/test/CI config, harness rules, assertions
    removed or skips added in a test file), or None. New files add checks; they never weaken an existing one."""
    p = h.root / rel
    if tool == "NotebookEdit" or not p.is_file():
        return None
    current = p.read_text(encoding="utf-8", errors="replace")
    proposed = _proposed_text(current, tool, tin)
    if proposed is None or proposed == current:
        return None
    removed, added = _changed_lines(current, proposed)
    base = rel.rsplit("/", 1)[-1]
    approve = "approve only if you asked for this change (relaxing a check to make it pass is not a fix)"
    if any(fnmatch.fnmatch(rel, g) or ("/" not in g and fnmatch.fnmatch(base, g)) for g in CHECKER_FILES):
        return f"{rel} defines how the work is checked (lint, tests, CI, harness rules): {approve}"
    if base in ("pyproject.toml", "setup.cfg"):
        old_l, new_l = current.splitlines(), proposed.splitlines()
        if any(re.match(CHECKER_SECTION_RE, _section_of(old_l, i), re.I) for i, _ in removed) or \
                any(re.match(CHECKER_SECTION_RE, _section_of(new_l, j), re.I) for j, _ in added):
            return f"the edit changes checker settings in {rel} (pytest, ruff, mypy, coverage...): {approve}"
    if base == "package.json":
        changed = _package_json_checker_change(current, proposed)
        if changed is None:
            changed = any(re.search(CHECKER_JSON_KEY_RE, line) for _, line in removed + added)
        if changed:
            return f"the edit changes test/lint scripts or checker settings in {rel}: {approve}"
    if TEST_FILE_RE.search(rel):
        lost = _count_matches(ASSERT_RE, [l for _, l in removed]) - _count_matches(ASSERT_RE, [l for _, l in added])
        skips = _count_matches(SKIP_RE, [l for _, l in added]) - _count_matches(SKIP_RE, [l for _, l in removed])
        what = [f"removes {lost} assertion(s)"] * (lost > 0) + [f"adds {skips} skip marker(s)"] * (skips > 0)
        if what:
            return (f"the edit {' and '.join(what)} in the test file {rel}: tests are never loosened to get a pass — "
                    f"approve only if the test itself is wrong")
    return None


TEST_DELETE_RE = (r"(?:^|[|;&(\n]|\b(?:sudo|xargs|env)\s+)\s*(?:rm|del|erase|rmdir|rd|Remove-Item|ri|git\s+rm|mv|move|"
                  r"Move-Item|ren|rename|Rename-Item|git\s+mv|sed\s+-\S*i\S*|sed\s+--in-place|perl\s+-\S*i\S*)(?=\s)"
                  r"([^|;&\n]*)")  # in command position only: `pytest -k rename tests/` is not a rename


def _deleted_test(scan: str) -> str | None:
    """A test file or test folder a shell command deletes, moves away or edits in place (sed/perl -i), or None —
    Edit/Write changes of tests are judged by their assertions instead (`_weakens_checker`)."""
    for m in re.finditer(TEST_DELETE_RE, scan, re.I):
        for tok in m.group(1).split():
            t = tok.strip("'\"").rstrip("/")
            if t and not t.startswith("-") and (TEST_FILE_RE.search(t) or t.rsplit("/", 1)[-1] in
                                                ("tests", "test", "__tests__", "spec", "specs")):
                return t
    return None


def _stdin_text() -> str:
    """Hook payloads are UTF-8 JSON: read bytes, so a Windows code page never mangles a non-ASCII path."""
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    buf = getattr(sys.stdin, "buffer", None)
    return buf.read().decode("utf-8-sig", "replace") if buf is not None else sys.stdin.read()


_LAST_PAYLOAD: dict = {}
_PAYLOAD_BROKEN = False  # stdin had content that was not a JSON object (the guard may fail closed on it)


def _hook_payload() -> dict:
    global _LAST_PAYLOAD, _PAYLOAD_BROKEN
    try:
        raw = _stdin_text()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        data = None
    _PAYLOAD_BROKEN = not isinstance(data, dict)
    _LAST_PAYLOAD = data if isinstance(data, dict) else {}
    return _LAST_PAYLOAD


def _path_key(rel: str) -> str:
    """Windows paths are case-insensitive: compare `.HARNESS/Session.json` like `.harness/session.json`."""
    return rel.casefold() if os.name == "nt" else rel


def _shell_scan_text(command: str) -> str:
    """The parts of a shell command that can name a write target: heredoc bodies and quoted prose (commit messages,
    echo text) are dropped, a quoted single word (a quoted path) is kept, backslashes become slashes."""
    text = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", "<<heredoc", command, flags=re.S)
    text = re.sub(r"(\"[^\"]*\"|'[^']*')", lambda m: m.group(0)[1:-1] if not re.search(r"\s", m.group(0)[1:-1])
                  else '""', text)
    return text.replace("\\", "/")


def _emit(event: str, **fields) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}, ensure_ascii=False))


def _age(ts: str) -> str:
    try:
        then = _dt.datetime.fromisoformat(ts)
        secs = (_dt.datetime.now().astimezone() - then).total_seconds()
    except (TypeError, ValueError):
        return "?"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit}"
    return "now"


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _next_steps(h: Harness, limit: int = 3) -> list[str]:
    prog = h.path("progress_file")
    if not prog.exists():
        return []
    m = re.search(r"^## (Next Steps|Bước tiếp theo).*?(?=^## |\Z)", prog.read_text(encoding="utf-8", errors="replace"),
                  re.M | re.S)
    if not m:
        return []
    items = [l.strip() for l in m.group(0).splitlines()[1:] if re.match(r"\s*(\d+\.|[-*])\s+\S", l)]
    return items[:limit]


def state_summary(h: Harness) -> list[str]:
    """A compact picture of the harness state, for session start, compaction snapshots and the status line."""
    feats = h.load_features()
    s = stats(feats)
    branch = h.git("rev-parse", "--abbrev-ref", "HEAD") or "-"
    lines = [f"Project {h.cfg.get('project') or h.root.name} · branch {branch} @ {h.head() or '-'}",
             f"Features: {s['passing']}/{s['total']} passing · active {s['active']} · blocked {s['blocked']} · "
             f"VCR {s['vcr']}"]
    for f in feats:
        if f.get("status") == "active":
            lines.append(f"ACTIVE {f['id']} — {f.get('behavior')}")
            lines.append(f"  verify: {f.get('verification')}")
            if _scope_globs(f):
                lines.append(f"  scope: {' '.join(_scope_globs(f))}")
            if f.get("attempts"):
                lines.append(f"  failed verify attempts so far: {f['attempts']}")
        elif f.get("status") == "blocked":
            lines.append(f"BLOCKED {f['id']} — {f.get('blocked_reason', '')}")
    stale = stale_features(h, feats)
    if stale:
        lines.append(_stale_line(stale))
    steps = _next_steps(h)
    if steps:
        lines.append("Next steps (PROGRESS.md): " + " | ".join(steps))
    rec = _read_json(h.hdir / "last-check.json", None)
    if rec:
        failed = [r["step"] for r in rec.get("steps", []) if r.get("status") == "fail"]
        lines.append(f"Last check: {'PASS' if rec.get('ok') else 'FAIL ' + ', '.join(failed)} ({rec.get('mode')}, "
                     f"{_age(rec.get('ts', ''))} ago" + (f", @{rec['commit']})" if rec.get("commit") else ")"))
    else:
        lines.append("Last check: never run")
    dirty = h.git("status", "--porcelain").splitlines()
    if dirty:
        lines.append(f"Uncommitted changes: {len(dirty)} file(s)")
    return lines


def next_action(h: Harness) -> str:
    """The single next step, from the feature list (cheap: no git, no checks)."""
    feats = h.load_features()
    active = [f for f in feats if f.get("status") == "active"]
    if active:
        f = active[0]
        return (f"Next: continue {f['id']} — change only its scope, then `python scripts/harness.py verify {f['id']}`.")
    if any(f.get("status") == "blocked" for f in feats):
        return "Next: a feature is blocked — resolve it or record it, then `python scripts/harness.py next`."
    for f in sorted(feats, key=lambda x: (x.get("priority", 100), feats.index(x))):
        if f.get("status") == "not_started" and _deps_ok(f, feats):
            return (f"Next: `python scripts/harness.py next --id {f['id']}` ({f.get('behavior')}), build it, then "
                    f"`verify {f['id']}`.")
    return "Next: every feature is done or waiting; add new work with `python scripts/harness.py add`."


def cmd_hook_session_start(h: Harness, args) -> int:
    """SessionStart: put the harness state into context (lesson 03: what the agent can't see doesn't exist)."""
    payload = _hook_payload()
    if not (h.hdir / "config.json").exists():
        return 0
    source = payload.get("source", "startup")
    previous = _read_json(h.hdir / "session.json", {})
    previous_start = float(previous.get("ts") or 0) if isinstance(previous, dict) else 0.0
    if source == "startup":  # an automatic clock-in record, so exit-check can tell whether PROGRESS.md was updated
        sess_p = h.hdir / "session.json"
        h.hdir.mkdir(exist_ok=True)
        atomic_write(sess_p, json.dumps({"started_at": now_iso(), "head": h.git("rev-parse", "HEAD"), "ts": time.time(),
                                         "session_id": payload.get("session_id"), "via": "hook"}, indent=2))
    lines = ["## Harness state (injected by harness-kit; rules are in AGENTS.md)"] + state_summary(h)
    # experiments 5b/5c (16/10/2026): told to "clock in", agents re-ran `start` and re-read PROGRESS.md /
    # feature_list.json every session (24% of their tool calls on frozen kit 2.2.0; 16% with this wording). This block
    # already is the clock-in: say so, and name the one next action.
    lines.append("This state is current: you do not need `python scripts/harness.py start` or to re-read PROGRESS.md "
                 "and feature_list.json to learn it. " + next_action(h))
    lines.append("Rules: one feature at a time (WIP=1) · status changes only via `verify` · end with `handoff` + "
                 "`exit-check` (or /hk-handoff).")
    if source in ("startup", "clear"):
        lines += incident_lines(h, previous_start)
    snap = h.hdir / "compact-snapshot.md"
    if source == "compact" and snap.exists():
        body = snap.read_text(encoding="utf-8", errors="replace").splitlines()[:60]
        lines += ["", "## Snapshot written before the context was compacted"] + body
        lines.append("The context was compacted: re-read the active feature, re-run `python scripts/harness.py try "
                     "<id>` before claiming progress, and re-confirm any approval the user gave earlier.")
    ctx = "\n".join(lines)
    h.trace("hook-session-start", "ok", source=source, session=payload.get("session_id"),
            context_sha256=hashlib.sha256(ctx.encode("utf-8")).hexdigest()[:16], context_chars=len(ctx))
    _emit("SessionStart", additionalContext=ctx)
    return 0


def cmd_hook_prompt(h: Harness, args) -> int:
    """UserPromptSubmit: one reminder line while a feature is active (lesson 07), deduplicated for 10 minutes."""
    _hook_payload()
    active = [f for f in h.load_features() if f.get("status") == "active"]
    if not active:
        return 0
    f = active[0]
    key = hashlib.sha256(json.dumps([f["id"], f.get("verification"), _scope_globs(f)]).encode()).hexdigest()[:16]
    state_p = h.hdir / "hook-state.json"
    state = _read_json(state_p, {})
    if not isinstance(state, dict):
        state = {}
    if state.get("prompt_key") == key and time.time() - state.get("prompt_ts", 0) < 600:
        return 0
    state.update(prompt_key=key, prompt_ts=time.time())
    atomic_write(state_p, json.dumps(state))
    scope = f" Scope: {' '.join(_scope_globs(f))}." if _scope_globs(f) else ""
    ctx = (f"Harness: {f['id']} is the active feature (WIP=1) — {f.get('behavior')}.{scope} Done means "
           f"`python scripts/harness.py verify {f['id']}` passes after an independent review; new work becomes a new "
           f"feature (`add`), not part of this one.")
    payload = _LAST_PAYLOAD
    h.trace("hook-prompt", "ok", session=payload.get("session_id"), feature=f["id"],
            context_sha256=hashlib.sha256(ctx.encode("utf-8")).hexdigest()[:16], context_chars=len(ctx))
    _emit("UserPromptSubmit", additionalContext=ctx)
    return 0


def _status_of(f: dict):
    raw = f.get("status", f.get("state"))
    if raw is None and "passes" in f:
        raw = "passing" if f.get("passes") else "not_started"
    return STATUS_ALIASES.get(str(raw).lower().replace("-", "_"), raw) if raw is not None else None


def _feature_items(data) -> list:
    items = data.get("features", []) if isinstance(data, dict) else data
    return [f for f in items if isinstance(f, dict)] if isinstance(items, list) else []


def _feature_map(data) -> dict:
    return {str(f.get("id")): f for f in _feature_items(data)}


def _proposed_text(current: str, tool: str, tin: dict) -> str | None:
    """The file content after the edit, or None when the edit would not apply (the tool reports that itself)."""
    if tool == "Write":
        return str(tin.get("content", ""))
    edits = tin.get("edits") if tool == "MultiEdit" else [tin]
    text = current
    for e in edits or []:
        old, new = str(e.get("old_string", "")), str(e.get("new_string", ""))
        if not old or old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def _feature_list_violation(h: Harness, tool: str, tin: dict) -> str | None:
    """Lesson 08: status/evidence are owned by the harness. Other fields (behavior, scope, notes) may be edited."""
    p = h.path("feature_list")
    current = p.read_text(encoding="utf-8", errors="replace") if p.exists() else "[]"
    proposed = _proposed_text(current, tool, tin)
    if proposed is None:
        return None
    try:
        new_data = json.loads(proposed)
    except ValueError:
        return f"the edit would leave {h.cfg['feature_list']} as invalid JSON"
    try:
        old_data = json.loads(current)
    except ValueError:
        # the file is already broken: judge the repair against the last committed version, if there is one
        try:
            old_data = json.loads(h.git("show", f"HEAD:{h.cfg['feature_list']}") or "[]")
        except ValueError:
            old_data = []
    ids = [str(f.get("id")) for f in _feature_items(new_data)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        return f"duplicate feature id(s) {', '.join(dupes)}: every id must be unique"
    old_map, new_map = _feature_map(old_data), _feature_map(new_data)
    for fid, new in new_map.items():
        old = old_map.get(fid)
        if old is None:
            if _status_of(new) not in (None, "not_started") or new.get("evidence"):
                return f"new feature {fid} must start as not_started without evidence (use `add`)"
            continue
        for field in GUARDED_FEATURE_FIELDS:
            if (_status_of(new) if field == "status" else new.get(field)) != \
                    (_status_of(old) if field == "status" else old.get(field)):
                return (f"`{field}` of {fid} is harness state: change it with `python scripts/harness.py "
                        f"next/verify/block/unblock`, never by hand")
        if _status_of(old) in ("active", "passing"):
            for field in LOCKED_WHEN_STARTED:
                if new.get(field) != old.get(field):
                    return (f"`{field}` of {fid} is fixed while it is {_status_of(old)}: changing how a started "
                            f"feature is judged would bypass the pass-gate (block it with a reason, unblock, then "
                            f"edit; or record the change as a new feature with `add`)")
    for fid, old in old_map.items():
        if fid not in new_map and _status_of(old) != "not_started":
            return f"{fid} is {_status_of(old)}: removing it would hide work (block it with a reason instead)"
    return None


def cmd_hook_guard(h: Harness, args) -> int:
    """PreToolUse for interactive sessions: deny hand edits of harness state; ask before writing outside the active
    feature's scope (config `scope_guard`: ask | deny | off). Never answers "allow": the user's permissions decide."""
    payload = _hook_payload()
    if not (h.hdir / "config.json").exists():
        return 0
    if _PAYLOAD_BROKEN and h.cfg.get("guard_failure") == "closed":
        h.trace("hook-guard", "deny", reason="unreadable tool call (guard_failure: closed)")
        _emit("PreToolUse", permissionDecision="deny",
              permissionDecisionReason="harness-kit: the tool call could not be read and guard_failure is \"closed\".")
        return 0
    tool, tin = payload.get("tool_name", ""), payload.get("tool_input") or {}
    if not isinstance(tin, dict):
        return 0
    decision, reason = None, None
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        bypass = _edit_bypass_reason(tool, tin)
        if bypass:  # any settings file, inside the project or not
            h.trace("hook-guard", "deny", tool=tool, reason=bypass, session=payload.get("session_id"))
            _emit("PreToolUse", permissionDecision="deny",
                  permissionDecisionReason=f"harness-kit: {bypass}. Switching checks off is the user's action, not the "
                                           f"agent's: ask the user to do it if it is really wanted.")
            return 0
        target = str(tin.get("file_path") or tin.get("notebook_path") or "")
        try:
            in_transcripts = bool(target) and Path(target).resolve().is_relative_to(_claude_projects_dir().resolve())
        except (OSError, ValueError, AttributeError):
            in_transcripts = False
        if in_transcripts:
            h.trace("hook-guard", "deny", tool=tool, reason="claude transcripts", session=payload.get("session_id"))
            _emit("PreToolUse", permissionDecision="deny",
                  permissionDecisionReason="harness-kit: Claude Code's transcripts are its own record (the review gate "
                                           "reads them as evidence); they are never written by the agent.")
            return 0
        rel = _rel_to_root(h, target)
        if rel is None:
            return 0
        key = _path_key(rel)
        if key == _path_key(h.cfg["feature_list"]) and tool != "NotebookEdit":
            reason = _feature_list_violation(h, tool, tin)
        elif key in {_path_key(p) for p in HOOK_STATE_FILES} or key.startswith(".harness/runs/"):
            reason = f"{rel} is written by the harness runtime only"
        elif key == "scripts/harness.py":
            reason = "scripts/harness.py is managed by harness-kit: change the kit and run `harness update`"
        decision = "deny" if reason else None
        if not decision:  # every applicable concern is reported; a configured deny is never weakened to ask (2.3.1)
            asks, deny = [], None
            if key in {_path_key(p) for p in GUARD_CONFIG_FILES}:
                asks.append(f"{rel} configures the harness itself (commands, hooks, guards): approve only if you asked "
                            f"for this change")
            if h.cfg.get("checker_guard", "ask") == "ask":
                why = _weakens_checker(h, rel, tool, tin)
                if why:
                    asks.append(why)
            if h.cfg.get("instruction_guard", "ask") == "ask" and _is_instruction_file(key):
                asks.append(f"{rel} is an instruction file every later session trusts: approve only if this change "
                            f"was asked for (instructions written by an agent are input to review)")
            mode = h.cfg.get("scope_guard", "ask")
            if mode in ("ask", "deny"):
                try:
                    feats = h.load_features()
                except (ValueError, OSError, SystemExit):  # a broken feature list is being repaired: no scope to check
                    feats = []
                for f in feats:
                    scope = _scope_globs(f)
                    if f.get("status") == "active" and scope and not any(
                            fnmatch.fnmatch(rel, g) for g in scope + list(h.cfg.get("scope_exempt", []))):
                        why = (f"{rel} is outside the active feature {f['id']}'s scope {scope} (WIP=1). Approve only "
                               f"if it belongs to {f['id']}; otherwise record it as a new feature")
                        if mode == "deny":
                            deny = why
                        else:
                            asks.append(why)
                        break
            if deny:
                decision, reason = "deny", "; ".join([deny] + asks)
            elif asks:
                decision, reason = "ask", "; ".join(asks)
    elif tool in ("Agent", "Task"):
        decision, reason = _guard_subagent(h, tin, payload)
    elif tool in ("Bash", "PowerShell"):
        raw_command = str(tin.get("command", ""))
        command = _shell_scan_text(raw_command)
        targets = [re.escape(h.cfg["feature_list"])] + [re.escape(p) for p in HOOK_STATE_FILES]
        targets += [r"\.harness[\\/]+(?:\.[\\/]+)*" + re.escape(p.split("/", 1)[1]) for p in HOOK_STATE_FILES]
        if re.search(r"\b(?:cd|pushd|set-location|sl)\s+[\"']?\.?[\\/]?\.harness\b", command, re.I):
            targets += [r"(?:^|[\s'\"/\\])" + re.escape(p.rsplit("/", 1)[1]) for p in HOOK_STATE_FILES]
        targets.append(CLAUDE_TRANSCRIPTS_RE)
        bypass = _bypass_reason(raw_command, command)
        deleted = _deleted_test(command) if h.cfg.get("checker_guard", "ask") == "ask" else None
        read_only = all(re.match(r"(?:grep|rg|ag|findstr|select-string|sls|cat|type|less|more|head|tail|wc|"
                                 r"get-content|gc|git\s+(?:log|show|diff|grep|blame|status))\b", seg, re.I)
                        for _, seg in _split_segments(_normalize_shell(command)))
        # the script text of `python -c` / heredoc / stdin is code, so it is read raw; elsewhere quoted prose (a commit
        # message that mentions hook-stop, a `pytest -k "hook-stop"` filter) is not a command (2.3.1, review round 3)
        script = re.search(r"\b(python3?|py|node)\b[^|;&\n]*(\s-[ce]\b|\s-(?:\s|$)|<<)", raw_command)
        hook_text = raw_command if script else command
        imports_runtime = script and re.search(r"\bimport\s+harness\b|\bfrom\s+harness\s+import|scripts[\\/]+harness"
                                               r"|\bharness_kit\b|\brunpy\b|SourceFileLoader|spec_from_file_location",
                                               raw_command)
        if imports_runtime:
            decision, reason = "deny", ("a script that imports the harness runtime can rewrite its records; run "
                                        "`python scripts/harness.py <command>` instead")
        elif not read_only and re.search(HOOK_COMMAND_RE, hook_text, re.I):
            decision, reason = "deny", ("hook commands are run by Claude Code with its own payload; running one by hand "
                                        "could forge a review verdict or session record")
        elif bypass:
            decision, reason = "deny", (f"{bypass}. Switching checks off is the user's action, not the agent's: fix "
                                        f"what the check reports, or ask the user")
        elif any(_shell_writes(command, t) for t in targets) or _inline_script_writes(raw_command, h):
            decision, reason = "deny", "harness state files are changed only through scripts/harness.py"
        elif any(_shell_writes(command, re.escape(p)) for p in GUARD_CONFIG_FILES):
            decision, reason = "ask", "the command writes a file that configures the harness itself"
        elif deleted:
            decision, reason = "ask", (f"the command deletes, moves or edits in place the test {deleted}: tests are "
                                       f"never loosened to get a pass — approve only if you asked for it")
    if not decision:
        return 0
    h.trace("hook-guard", decision, tool=tool, reason=reason, session=payload.get("session_id"))
    _emit("PreToolUse", permissionDecision=decision, permissionDecisionReason=f"harness-kit: {reason}.")
    return 0


def cmd_hook_activity(h: Harness, args) -> int:
    """PostToolUse: process observability (lesson 11) — what the session touched, for handoffs and snapshots."""
    payload = _hook_payload()
    if not (h.hdir / "config.json").exists():
        return 0
    tin = payload.get("tool_input") or {}
    if not isinstance(tin, dict):
        tin = {}
    rec = {"ts": now_iso(), "session": payload.get("session_id"), "tool": payload.get("tool_name")}
    path = tin.get("file_path") or tin.get("notebook_path")
    if path:
        rec["file"] = _rel_to_root(h, str(path)) or str(path)
    elif tin.get("command"):
        rec["command"] = str(tin["command"])[:160]
    p = h.hdir / "activity.jsonl"
    with p.open("a", encoding="utf-8") as fh:  # append: parallel tool calls never lose a record
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    rotate_jsonl(p, int(h.cfg.get("activity_max_lines", 2000)))
    return 0


def session_files(h: Harness, session_id: str | None) -> list[str]:
    p = h.hdir / "activity.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("file") and (not session_id or rec.get("session") == session_id) and rec["file"] not in out:
            out.append(rec["file"])
    return out


def cmd_hook_precompact(h: Harness, args) -> int:
    """PreCompact: write a handoff snapshot that SessionStart(compact) re-injects (lesson 05, context anxiety)."""
    payload = _hook_payload()
    if not (h.hdir / "config.json").exists():
        return 0
    files = session_files(h, payload.get("session_id"))
    lines = [f"_Written {now_iso()} before {payload.get('trigger', 'a')} compaction_", ""] + state_summary(h)
    if files:
        lines += ["", f"Files changed in this session ({len(files)}): " + ", ".join(files[:30])]
    if payload.get("custom_instructions"):
        lines += ["", f"User's compaction instructions: {payload['custom_instructions']}"]
    atomic_write(h.hdir / "compact-snapshot.md", "\n".join(lines) + "\n")
    h.trace("hook-precompact", "ok", files=len(files))
    return 0


def cmd_hook_subagent_stop(h: Harness, args) -> int:
    """SubagentStop: record the verdict of an hk-reviewer run for the review gate. SubagentStop, not PostToolUse,
    because a subagent started in the background returns no text to PostToolUse (checked on Claude Code 2.1.287)."""
    payload = _hook_payload()
    if not (h.hdir / "config.json").exists():
        return 0
    agent = str(payload.get("agent_type") or "")
    if not (agent == "hk-reviewer" or agent.endswith(":hk-reviewer")):
        return 0
    active = [f for f in h.load_features() if f.get("status") == "active"]
    f = active[0] if active else None
    verdict = parse_verdict(payload.get("last_assistant_message", ""))
    extra: dict = {}
    start, problem = _authenticate_review(h, f, payload, verdict)
    if start:  # every verdict uses up its launch, so the next review is paired with its own
        extra["start"] = start.get("id")
    if verdict == "APPROVE":
        if problem:
            verdict, extra["note"] = "UNCERTAIN", problem
        elif start and start.get("fingerprint") != review_fingerprint(h, f):
            verdict, extra["note"] = "UNCERTAIN", "the code changed while the review ran: the approval covers older content"
    if start and start.get("fingerprint"):
        # the verdict is about the content the review started on: a late verdict of an older review must not cover
        # (or overwrite the verdict on) the current content
        extra["fingerprint"] = start["fingerprint"]
    record_review(h, f, verdict, "hk-reviewer", session=payload.get("session_id"), agent_id=payload.get("agent_id"),
                  **extra)
    return 0


def _claude_projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def _transcript_texts(path: Path) -> tuple[list[str], float | None, str]:
    """(text blocks of the first real user message, its time as epoch seconds, last assistant message) of a Claude Code
    JSONL transcript. `isMeta` entries (injected context) are skipped; on Claude Code 2.1.287 the first user entry is
    the Agent tool's prompt even with a CLAUDE.md (that arrives as `attachment` entries)."""
    first: list[str] = []
    started, last = None, ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("isMeta"):
            continue
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        content = msg.get("content")
        blocks = [content] if isinstance(content, str) else [
            str(b.get("text", "")) for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        blocks = [b for b in blocks if b.strip()]
        if e.get("type") == "user" and not first and blocks:
            first = blocks
            try:
                started = _dt.datetime.fromisoformat(str(e.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                started = None
        elif e.get("type") == "assistant" and blocks:
            last = " ".join(blocks)
    return first, started, last


def _authenticate_review(h: Harness, f: dict | None, payload: dict, verdict: str = "APPROVE") -> tuple[dict | None, str | None]:
    """An APPROVE counts only when Claude Code's own transcript of the reviewer backs it (2.3.1, review rounds 2–3): the
    transcript lies in Claude Code's projects folder, its last assistant message carries the same verdict, and its first
    user message is the prompt of a recorded hk-reviewer launch for this feature that no earlier verdict used. Among
    such launches the one made last *before the transcript started* is its own — right for any finish order of
    overlapping reviewers with identical prompts, and an abandoned launch (denied, crashed) is simply never chosen.
    Checked on Claude Code 2.1.287: at SubagentStop the transcript already holds the prompt (with its timestamp) and the
    final message. Returns (the matched launch, problem)."""
    raw = str(payload.get("agent_transcript_path") or "")
    if not raw:
        return None, "no subagent transcript in the hook payload"
    path = Path(raw)
    try:
        inside = path.resolve().is_relative_to(_claude_projects_dir().resolve())
    except (OSError, ValueError, AttributeError):
        inside = False
    if not inside or path.suffix != ".jsonl" or not path.is_file():
        return None, "the transcript is not a Claude Code subagent transcript"
    first, started, last = _transcript_texts(path)
    problem = None
    if parse_verdict(last) != verdict:
        problem = f"the transcript's last message does not carry the {verdict}"
    if not f:
        return None, problem

    def same_prompt(r: dict) -> bool:
        if r.get("prompt"):
            want = str(r["prompt"]).strip()
            return any(b.strip() == want for b in first) or want in "\n".join(first)
        return hashlib.sha256(" ".join(first).strip().encode("utf-8")).hexdigest()[:16] == r.get("prompt_sha256")
    recs = _read_jsonl(h.hdir / "reviews.jsonl")
    used = {r.get("start") for r in recs if r.get("kind") != "start" and r.get("start")}
    recent = time.time() - 6 * 3600
    launches = [r for r in recs if r.get("kind") == "start" and r.get("feature") == f["id"]
                and float(r.get("epoch") or 0) >= recent and same_prompt(r)]
    if started is not None:  # the transcript begins after the PreToolUse hook recorded its launch (ms timestamps)
        launches = [r for r in launches if float(r.get("epoch") or 0) <= started + 0.05]
    if not launches:
        return None, problem or "no recorded hk-reviewer launch matches this transcript's prompt"
    own = max(launches, key=lambda r: float(r.get("epoch") or 0))
    if own.get("id") in used:
        return None, problem or ("this transcript belongs to a review that already reported: a reviewer continued "
                                 "with SendMessage is not a new review — launch a fresh hk-reviewer")
    return own, problem


def _guard_subagent(h: Harness, tin: dict, payload: dict) -> tuple[str | None, str | None]:
    """PreToolUse on the Agent tool: an hk-reviewer launch records the fingerprint the review starts from (so edits made
    while it runs void its approval), and a prompt that dictates the verdict is refused."""
    agent = str(tin.get("subagent_type") or "")
    if not (agent == "hk-reviewer" or agent.endswith(":hk-reviewer")):
        return None, None
    prompt = str(tin.get("prompt") or "")
    for m in re.finditer(r"verdict\W{0,4}approve|\b(?:answer|reply|respond|return|say|output|end with|conclude)\b"
                         r"[^.\n]{0,40}\bapprove\b", prompt, re.I):
        around = prompt[max(0, m.start() - 40): m.end() + 60]
        if not re.search(r"reject|uncertain|\bfail\b|\|", around, re.I):  # APPROVE offered as the only answer
            return "deny", ("the reviewer's verdict must not be dictated in its prompt: describe the feature and the "
                            "change, and let hk-reviewer judge")
    active = [f for f in h.load_features() if f.get("status") == "active"]
    if active:
        rec = {"ts": now_iso(), "epoch": time.time(), "kind": "start", "feature": active[0]["id"],
               "id": str(payload.get("tool_use_id") or f"start-{time.time()}"),
               "prompt_sha256": hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()[:16],
               "prompt": prompt.strip()[:4000],
               "fingerprint": review_fingerprint(h, active[0]), "session": payload.get("session_id")}
        with (h.hdir / "reviews.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return None, None


def cmd_hook_session_end(h: Harness, args) -> int:
    """SessionEnd: record whether the session ended with a handoff (lesson 12)."""
    payload = _hook_payload()
    if not (h.hdir / "config.json").exists():
        return 0
    session = _read_json(h.hdir / "session.json", {})
    prog = h.path("progress_file")
    handed_off = bool(session) and prog.exists() and prog.stat().st_mtime >= session.get("ts", 0)
    files = session_files(h, payload.get("session_id"))
    h.trace("session-end", "ok", reason=payload.get("reason"), handoff=handed_off, files=len(files))
    unverified = unverified_changes(h)
    if unverified:  # SessionEnd cannot block: leave it for the next session start
        record_incident(h, "session-ended-unverified", "; ".join(unverified)[:500], session=payload.get("session_id"))
    elif files and not handed_off:
        record_incident(h, "session-ended-without-handoff",
                        f"{len(files)} file(s) changed ({', '.join(files[:5])}) and {h.cfg['progress_file']} was not "
                        f"updated", session=payload.get("session_id"))
    return 0


def incident_lines(h: Harness, since: float) -> list[str]:
    """SessionStart banner: gates bypassed or exhausted since `since`, and a warning when `--no-review` is routine."""
    recs = _read_jsonl(h.hdir / "incidents.jsonl")
    new = [r for r in recs if float(r.get("epoch") or 0) > since]
    out = []
    if new:
        out.append(f"## Incidents since the last session ({len(new)}) — gates that were bypassed or ran out; check them")
        out += [f"- {r.get('kind')}: {str(r.get('detail', ''))[:200]}" for r in new[-3:]]
    trace = _read_jsonl(h.hdir / "trace.jsonl")[-500:]  # one truncated line must not cost the whole injection
    passes = sum(1 for e in trace if e.get("cmd") == "verify" and e.get("result") == "pass")
    skips = sum(1 for r in recs[-50:] if r.get("kind") == "no-review")
    if skips >= 3 and skips >= 0.25 * max(passes, 1):
        out.append(f"WARNING: {skips} recent verifies skipped the review (`--no-review`) — the review gate is being "
                   f"routinely bypassed; ask the user whether that is intended.")
    return out


def cmd_statusline(h: Harness, args) -> int:
    """Claude Code status line: the harness state in one line (feature, VCR, last check, context use)."""
    payload = _hook_payload()
    if not (h.hdir / "config.json").exists():
        print("HK · not installed here")
        return 0
    feats = h.load_features()
    s = stats(feats)
    active = next((f["id"] for f in feats if f.get("status") == "active"), None)
    parts = ["HK", f"{active} active" if active else "no active feature",
             f"{s['passing']}/{s['total']} passing · VCR {s['vcr']}"]
    rec = _read_json(h.hdir / "last-check.json", None)
    parts.append(f"check {'PASS' if rec.get('ok') else 'FAIL'} {_age(rec.get('ts', ''))}" if rec else "check never")
    ctx = (payload.get("context_window") or {}).get("used_percentage") if isinstance(payload, dict) else None
    if isinstance(ctx, (int, float)):
        parts.append(f"ctx {int(ctx)}%" + (" · handoff soon" if ctx >= 70 else ""))
    print(" │ ".join(parts))
    return 0


def cmd_trace(h: Harness, args) -> int:
    p = h.hdir / "trace.jsonl"
    if not p.exists():
        print("No trace yet.")
        return 0
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if getattr(args, "grep", None):
        lines = [line for line in lines if args.grep in line]
    for line in lines[-args.n:]:
        print(line)
    return 0


def cmd_rules(h: Harness, args) -> int:
    ok, msgs = run_rules(h)
    for m in msgs:
        print(m)
    print("rules:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_docs(h: Harness, args) -> int:
    ok, msgs = run_docs(h, strict=args.strict)
    for m in msgs:
        print(m)
    print("docs:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ------------------------------------------------------------------- main ---

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness.py", description="Harness runtime " + VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check", help="run verification")
    s.add_argument("--full", action="store_true", help="also run build, e2e, feature regression")
    s.add_argument("--only", nargs="+", help="run only these steps")
    sub.add_parser("status", help="features, VCR and the last check")
    s = sub.add_parser("next", help="activate the next feature (WIP=1)")
    s.add_argument("--id")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("verify", help="pass-gate: review gate + verification + project check")
    s.add_argument("id")
    s.add_argument("--no-review", metavar="REASON", help="verify without an approved review (recorded as an incident)")
    s.add_argument("--skip-check", action="store_true", help="skip the project-wide check (not recommended)")
    s = sub.add_parser("block", help="mark the active feature blocked, with a reason")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s = sub.add_parser("unblock", help="blocked -> not_started")
    s.add_argument("id")
    s = sub.add_parser("add", help="add a not_started feature")
    s.add_argument("--id", required=True)
    s.add_argument("--behavior", required=True)
    s.add_argument("--verify", required=True, help="shell command that exits 0 when the behavior is correct")
    s.add_argument("--category", default="feature")
    s.add_argument("--depends", nargs="*")
    s.add_argument("--out-of-scope")
    s.add_argument("--priority", type=int)
    s.add_argument("--scope", nargs="+", help="globs of files this feature may change, e.g. 'src/orders/*' 'tests/*'")
    s.add_argument("--verify-check", choices=["quick", "full"],
                   help="project-wide check run by verify (full = + build, e2e, regression; use for cross-component work)")
    s = sub.add_parser("fail", help="log an agent failure and the harness layer that caused it (lesson 01)")
    s.add_argument("--layer", required=True, choices=LAYERS)
    s.add_argument("--note", required=True, help="what went wrong, concretely")
    s.add_argument("--feature")
    s = sub.add_parser("failures", help="summarize logged failures by layer")
    sub.add_parser("hook-stop", help="Claude Code Stop hook: block stopping on cheap, deterministic violations")
    sub.add_parser("hook-session-start", help="Claude Code SessionStart hook: inject the harness state")
    sub.add_parser("hook-prompt", help="Claude Code UserPromptSubmit hook: active-feature reminder (deduplicated)")
    sub.add_parser("hook-guard", help="Claude Code PreToolUse hook for interactive sessions: harness state, scope")
    sub.add_parser("hook-activity", help="Claude Code PostToolUse hook: append to .harness/activity.jsonl")
    sub.add_parser("hook-precompact", help="Claude Code PreCompact hook: write .harness/compact-snapshot.md")
    sub.add_parser("hook-session-end", help="Claude Code SessionEnd hook: record the end of the session")
    sub.add_parser("hook-subagent-stop", help="Claude Code SubagentStop hook: record an hk-reviewer verdict")
    sub.add_parser("statusline", help="Claude Code status line: feature, VCR, last check, context use")
    s = sub.add_parser("hook-pretool", help="PreToolUse guard used by `loop` sessions (scope, protected files, commands)")
    s.add_argument("--root", help="project root (set by the loop's --settings)")
    s = sub.add_parser("try", help="run a feature's verification command without changing any state")
    s.add_argument("id")
    s = sub.add_parser("loop", help="autonomous maker -> independent reviewer -> verify loop via the claude CLI")
    s.add_argument("--feature", help="feature id to work on (default: the active one, else the next eligible)")
    s.add_argument("--max-features", type=int, default=1, help="features to complete in this run (default 1)")
    s.add_argument("--max-attempts", type=int, default=3, help="maker/reviewer rounds per feature before blocking")
    s.add_argument("--budget-maker", type=float, default=2.0, help="max USD per maker session (--max-budget-usd)")
    s.add_argument("--budget-reviewer", type=float, default=1.0, help="max USD per reviewer session")
    s.add_argument("--max-total-usd", type=float, default=10.0, help="stop the run when estimated spend reaches this")
    s.add_argument("--model", help="model for the maker (and the reviewer unless --reviewer-model)")
    s.add_argument("--reviewer-model")
    s.add_argument("--agent-cmd", default="",
                   help='agent CLI, default "claude" (or $HARNESS_AGENT_CMD); a JSON list for paths with spaces')
    s.add_argument("--commit", action="store_true", help="commit each passing feature after a clean exit-check")
    s.add_argument("--dry-run", action="store_true", help="print the prompts and commands, run nothing")
    s = sub.add_parser("validate", help="check the feature list and the config")
    s.add_argument("--require", nargs="*", help="CATEGORY:N — require at least N features of that category")
    s = sub.add_parser("docs", help="check the docs map and links")
    s.add_argument("--strict", action="store_true")
    sub.add_parser("rules", help="run the architecture rules")
    s = sub.add_parser("start", help="clock in: state, baseline check")
    s.add_argument("--no-check", action="store_true")
    s = sub.add_parser("exit-check", help="clock out: clean-state check")
    s.add_argument("--no-run", action="store_true", help="do not re-run check")
    s.add_argument("--full", action="store_true")
    sub.add_parser("handoff", help="refresh the auto block of PROGRESS.md")
    s = sub.add_parser("setup", help="run commands.setup (called by init.sh)")
    s.add_argument("--print-dev", action="store_true")
    s.add_argument("--run-dev", action="store_true")
    s = sub.add_parser("size", help="check that file (or `file.py::func`) line counts are <= the limit (a refactor metric)")
    s.add_argument("files", nargs="+")
    s.add_argument("--max-lines", type=int, required=True)
    s = sub.add_parser("report", help="print a loop run's report: theory checklist + timeline (latest run by default)")
    s.add_argument("run", nargs="?", help="run id (a directory name under .harness/runs/)")
    s.add_argument("--list", action="store_true", help="list runs with their outcome")
    s = sub.add_parser("trace", help="the last runtime and hook records (.harness/trace.jsonl)")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--grep", help="only records containing this text, e.g. hook-guard or a session id")
    return p


COMMANDS = {"check": cmd_check, "status": cmd_status, "next": cmd_next, "verify": cmd_verify, "block": cmd_block,
            "unblock": cmd_unblock, "add": cmd_add, "validate": cmd_validate, "docs": cmd_docs, "rules": cmd_rules,
            "start": cmd_start, "exit-check": cmd_exit_check, "handoff": cmd_handoff, "trace": cmd_trace, "report": cmd_report,
            "setup": cmd_setup, "size": cmd_size, "fail": cmd_fail, "failures": cmd_failures,
            "hook-stop": cmd_hook_stop, "hook-pretool": cmd_hook_pretool, "try": cmd_try, "loop": cmd_loop,
            "hook-session-start": cmd_hook_session_start, "hook-prompt": cmd_hook_prompt, "hook-guard": cmd_hook_guard,
            "hook-activity": cmd_hook_activity, "hook-precompact": cmd_hook_precompact,
            "hook-session-end": cmd_hook_session_end, "hook-subagent-stop": cmd_hook_subagent_stop,
            "statusline": cmd_statusline}


HOOK_SWITCH_NAMES = {"hook-session-start": "session-start", "hook-prompt": "prompt", "hook-guard": "guard",
                     "hook-activity": "activity", "hook-precompact": "precompact", "hook-stop": "stop",
                     "hook-session-end": "session-end", "hook-subagent-stop": "subagent-stop",
                     "statusline": "statusline"}


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    h = Harness(root or find_root())
    name = HOOK_SWITCH_NAMES.get(args.cmd)
    off = h.cfg.get("hooks_off", [])
    if name and isinstance(off, list) and name in off:
        if name in ("guard", "stop"):  # a guardrail turned off is visible in the log
            h.trace(args.cmd, "disabled", reason="hooks_off in .harness/config.json")
        return 0
    return COMMANDS[args.cmd](h, args)


def config_problems(cfg: dict) -> list[str]:
    """R18: the project config against the keys and types the code reads (DEFAULT_CONFIG)."""
    out = []
    for key, value in cfg.items():
        if key not in DEFAULT_CONFIG:
            if key not in CONFIG_EXTRA_KEYS:
                out.append(f"unknown key `{key}` in .harness/config.json (typo? the runtime never reads it)")
            continue
        want = DEFAULT_CONFIG[key]
        if isinstance(want, bool) or want is None:
            continue
        if isinstance(want, (int, float)) and not isinstance(value, (int, float)) or \
                isinstance(want, (str, list, dict)) and not isinstance(value, type(want)):
            out.append(f"`{key}` should be a {type(want).__name__}, found {type(value).__name__}")
    for key, allowed in (("scope_guard", ("ask", "deny", "off")), ("instruction_guard", ("ask", "off")),
                         ("guard_failure", ("open", "closed")), ("scope_drift", ("warn", "error")),
                         ("verify_check", ("quick", "full")), ("trivial_verification", ("refuse", "warn")),
                         ("checker_guard", ("ask", "off")), ("review_gate", ("off", "warn", "require"))):
        if key in cfg and cfg[key] not in allowed:
            out.append(f"`{key}` must be one of {', '.join(allowed)}, found {cfg[key]!r}")
    unknown_hooks = [x for x in cfg.get("hooks_off", []) or [] if x not in HOOK_SWITCH_NAMES.values()]
    if unknown_hooks:
        out.append(f"`hooks_off` names unknown hooks: {', '.join(map(str, unknown_hooks))}")
    return out


if __name__ == "__main__":
    sys.exit(main())
