"""Tests for the v2 Claude Code layer: hook I/O contracts through the installed dispatcher, and the installer
(install / update / uninstall / doctor / global). Uses the helpers of tests/test_kit.py."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_kit import installer, runtime, scaffold  # noqa: E402
from tests.test_kit import PY, make_python_project, run_rt, set_commands, set_config  # noqa: E402


def run_dispatcher(root: Path, event: str, payload: dict, env: dict | None = None) -> tuple[int, str, str]:
    """Run a hook exactly as Claude Code does: the installed dispatcher, JSON on stdin, CLAUDE_PROJECT_DIR set."""
    proc = subprocess.run([sys.executable, str(root / ".claude" / "hooks" / "harness_hook.py"), event], cwd=root,
                          input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "CLAUDE_PROJECT_DIR": str(root), **(env or {})})
    return proc.returncode, proc.stdout, proc.stderr


def hook_output(stdout: str) -> dict:
    return json.loads(stdout)["hookSpecificOutput"] if stdout.strip() else {}


def quiet_install(root: Path, **kw) -> str:
    with contextlib.redirect_stdout(io.StringIO()):
        return installer.install(root, python="python", **kw)


class ClaudeHookTests(unittest.TestCase):
    """v2 interactive hooks: the I/O contract of every event, through the installed dispatcher."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        make_python_project(self.root)
        quiet_install(self.root)
        set_commands(self.root, test=f"{PY} -c \"print('ok')\"")

    def tearDown(self):
        self._tmp.cleanup()

    def edit(self, old: str, new: str) -> dict:
        return {"tool_name": "Edit", "tool_input": {"file_path": str(self.root / "feature_list.json"),
                                                   "old_string": old, "new_string": new}}

    def write(self, rel: str) -> dict:
        return {"tool_name": "Write", "tool_input": {"file_path": str(self.root / rel), "content": "x"}}

    def test_session_start_injects_state_and_records_clock_in(self):
        code, out, _ = run_dispatcher(self.root, "session-start", {"source": "startup", "session_id": "s1"})
        self.assertEqual(code, 0)
        ctx = hook_output(out)
        self.assertEqual(ctx["hookEventName"], "SessionStart")
        self.assertIn("Harness state", ctx["additionalContext"])
        self.assertIn("Features: 0/", ctx["additionalContext"])
        self.assertEqual(json.loads((self.root / ".harness/session.json").read_text())["session_id"], "s1")
        self.assertLess(len(ctx["additionalContext"]), 10_000)  # Claude Code's limit for injected context
        self.assertFalse((self.root / "scripts" / "__pycache__").exists())  # the dispatcher leaves no bytecode

    def test_guard_denies_hand_edits_of_feature_state_only(self):
        _, out, _ = run_dispatcher(self.root, "guard", self.edit('"status": "not_started"', '"status": "passing"'))
        self.assertEqual(hook_output(out)["permissionDecision"], "deny")
        _, out, _ = run_dispatcher(self.root, "guard", self.edit("Verification baseline", "The verification baseline"))
        self.assertEqual(out.strip(), "")  # behavior text may be edited; no decision means the user's permissions apply
        _, out, _ = run_dispatcher(self.root, "guard", self.edit('"features": [', '"features": [ ,'))
        self.assertIn("invalid JSON", hook_output(out)["permissionDecisionReason"])
        new_passing = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "feature_list.json"),
                       "content": json.dumps({"features": [{"id": "Z9", "status": "passing"}]})}}
        self.assertEqual(hook_output(run_dispatcher(self.root, "guard", new_passing)[1])["permissionDecision"], "deny")
        for cmd in ("echo x > feature_list.json", "sed -i s/a/b/ .harness/last-check.json"):
            _, out, _ = run_dispatcher(self.root, "guard", {"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(hook_output(out)["permissionDecision"], "deny", cmd)
        _, out, _ = run_dispatcher(self.root, "guard", {"tool_name": "Bash", "tool_input": {"command": "cat feature_list.json"}})
        self.assertEqual(out.strip(), "")
        _, out, _ = run_dispatcher(self.root, "guard", self.write("scripts/harness.py"))
        self.assertEqual(hook_output(out)["permissionDecision"], "deny")

    def test_guard_never_allows_and_asks_outside_scope(self):
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", f"{PY} -c pass", "--scope", "app/*")
        run_rt(self.root, "next", "--id", "F1")
        out = hook_output(run_dispatcher(self.root, "guard", self.write("README.md"))[1])
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("F1", out["permissionDecisionReason"])
        self.assertEqual(run_dispatcher(self.root, "guard", self.write("app/x.py"))[1].strip(), "")
        self.assertEqual(run_dispatcher(self.root, "guard", self.write("PROGRESS.md"))[1].strip(), "")  # scope-exempt
        if os.name == "nt":  # Git Bash spelling of the same path
            msys = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), (self.root / "README.md").as_posix())
            payload = {"tool_name": "Write", "tool_input": {"file_path": msys, "content": "x"}}
            self.assertEqual(hook_output(run_dispatcher(self.root, "guard", payload)[1])["permissionDecision"], "ask")
        set_config(self.root, scope_guard="off")
        self.assertEqual(run_dispatcher(self.root, "guard", self.write("README.md"))[1].strip(), "")
        for payload in (self.edit("a", "b"), self.write("README.md"), {"tool_name": "Bash", "tool_input": {"command": "ls"}}):
            self.assertNotIn('"allow"', run_dispatcher(self.root, "guard", payload)[1])

    def test_prompt_reminder_is_deduplicated(self):
        self.assertEqual(run_dispatcher(self.root, "prompt", {"prompt": "hi"})[1].strip(), "")  # nothing active
        run_rt(self.root, "next")
        first = hook_output(run_dispatcher(self.root, "prompt", {"prompt": "hi"})[1])
        self.assertIn("WIP=1", first["additionalContext"])
        self.assertEqual(run_dispatcher(self.root, "prompt", {"prompt": "again"})[1].strip(), "")

    def test_activity_precompact_and_compact_restore(self):
        run_rt(self.root, "next")
        run_dispatcher(self.root, "activity", {"session_id": "s1", "tool_name": "Write",
                                               "tool_input": {"file_path": str(self.root / "app" / "x.py")}})
        run_dispatcher(self.root, "activity", {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        lines = (self.root / ".harness/activity.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["file"], "app/x.py")
        self.assertEqual(json.loads(lines[1])["command"], "ls")
        run_dispatcher(self.root, "precompact", {"session_id": "s1", "trigger": "auto"})
        snap = (self.root / ".harness/compact-snapshot.md").read_text(encoding="utf-8")
        self.assertIn("ACTIVE H001", snap)
        self.assertIn("app/x.py", snap)
        ctx = hook_output(run_dispatcher(self.root, "session-start", {"source": "compact"})[1])["additionalContext"]
        self.assertIn("Snapshot written before the context was compacted", ctx)
        self.assertIn("re-confirm any approval", ctx)

    def test_stop_blocks_with_exit_2_and_dispatcher_fails_open(self):
        run_rt(self.root, "start", "--no-check")
        self.assertEqual(run_dispatcher(self.root, "stop", {})[0], 0)
        (self.root / "app" / "dbg.py").write_text("def g():\n    breakpoint()\n", encoding="utf-8")
        code, _, err = run_dispatcher(self.root, "stop", {"stop_hook_active": False})
        self.assertEqual(code, 2)
        self.assertIn("breakpoint", err)
        self.assertEqual(run_dispatcher(self.root, "stop", {"stop_hook_active": True})[0], 0)  # never loops
        self.assertEqual(run_dispatcher(self.root, "stop", {}, env={"HARNESS_HOOKS": "off"})[0], 0)
        (self.root / "scripts" / "harness.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
        code, _, err = run_dispatcher(self.root, "guard", self.edit("a", "b"))
        self.assertEqual(code, 0)  # a broken runtime never blocks the session
        self.assertIn("failed open", err)
        (self.root / "scripts" / "harness.py").unlink()
        self.assertEqual(run_dispatcher(self.root, "session-start", {})[0], 0)

    def test_statusline_and_session_end(self):
        code, out, _ = run_dispatcher(self.root, "statusline", {"context_window": {"used_percentage": 75}})
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("HK"))
        self.assertIn("ctx 75%", out)
        self.assertIn("handoff soon", out)
        self.assertEqual(run_dispatcher(self.root, "session-end", {"reason": "other"})[0], 0)
        self.assertIn('"session-end"', (self.root / ".harness/trace.jsonl").read_text(encoding="utf-8"))

    # ---- regressions from the independent review of 2.0.0 (15/10/2026) ----

    def feature_list_write(self, fid: str, **changes) -> dict:
        data = json.loads((self.root / "feature_list.json").read_text(encoding="utf-8"))
        for f in data["features"]:
            if f["id"] == fid:
                f.update(changes)
        return {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "feature_list.json"),
                                                    "content": json.dumps(data, indent=2)}}

    def decision(self, payload: dict):
        return hook_output(run_dispatcher(self.root, "guard", payload)[1]).get("permissionDecision")

    def test_guard_locks_how_a_started_feature_is_judged(self):
        """Editing an active feature's verification to `exit 0` made `verify` pass without the real check."""
        self.assertIsNone(self.decision(self.feature_list_write("H001", verification="exit 0")))  # planning: allowed
        run_rt(self.root, "next", "--id", "H001")
        self.assertEqual(self.decision(self.feature_list_write("H001", verification="exit 0")), "deny")
        self.assertEqual(self.decision(self.feature_list_write("H001", scope=["**"])), "deny")
        self.assertEqual(self.decision(self.feature_list_write("H001", activated_commit="0000000")), "deny")
        self.assertIsNone(self.decision(self.feature_list_write("H001", behavior="clearer wording")))

    def test_guard_denies_duplicate_ids_and_allows_repairing_a_broken_list(self):
        p = self.root / "feature_list.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        first = data["features"][0]
        data["features"].insert(0, {**first, "status": "passing", "evidence": {"exit_code": 0}})
        out = hook_output(run_dispatcher(self.root, "guard", {"tool_name": "Write", "tool_input": {
            "file_path": str(p), "content": json.dumps(data)}})[1])
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("duplicate", out["permissionDecisionReason"])
        good = p.read_text(encoding="utf-8")
        p.write_text(good[:-30], encoding="utf-8")  # truncated by a crash
        self.assertIsNone(self.decision({"tool_name": "Write", "tool_input": {"file_path": str(p), "content": good}}))

    def test_guard_shell_rules_target_writes_not_prose(self):
        def bash(cmd):
            return self.decision({"tool_name": "Bash", "tool_input": {"command": cmd}})
        for cmd in ("cp /tmp/x feature_list.json", "mv t.json feature_list.json", 'echo x > "feature_list.json"',
                    "git checkout HEAD~1 -- feature_list.json", "echo {} > .harness\\session.json"):
            self.assertEqual(bash(cmd), "deny", cmd)
        for cmd in ('git commit -m "feat: login -> verified, feature_list.json updated by verify"',
                    "cat >> PROGRESS.md <<'EOF'\nverify wrote feature_list.json\nEOF",
                    "cp feature_list.json /tmp/backup.json", "cat feature_list.json"):
            self.assertIsNone(bash(cmd), cmd)
        self.assertEqual(bash("echo '{}' > .harness/config.json"), "ask")
        self.assertEqual(self.decision(self.write(".claude/settings.json")), "ask")  # turning the harness off
        if os.name == "nt":
            self.assertEqual(self.decision(self.write(".HARNESS/Session.json")), "deny")

    def test_guard_reads_utf8_paths_whatever_the_code_page(self):
        """Windows decoded piped stdin with the ANSI code page: non-ASCII paths slipped past the guard."""
        with tempfile.TemporaryDirectory(prefix="dự-án-") as d:
            root = Path(d)
            make_python_project(root)
            quiet_install(root)
            payload = {"tool_name": "Write", "tool_input": {"file_path": str(root / ".harness" / "session.json"),
                                                            "content": "{}"}}
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
            proc = subprocess.run([sys.executable, str(root / ".claude/hooks/harness_hook.py"), "guard"], cwd=root,
                                  input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True,
                                  env={**env, "CLAUDE_PROJECT_DIR": str(root)})
            self.assertEqual(hook_output(proc.stdout.decode("utf-8"))["permissionDecision"], "deny")


class InstallerTests(unittest.TestCase):
    """`harness install / update / uninstall / doctor`: manifest-tracked, never touches what the user changed."""

    def test_install_doctor_update_uninstall_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_python_project(root)
            (root / ".claude").mkdir()
            (root / ".claude/settings.json").write_text(json.dumps({"model": "opus",
                                                                    "permissions": {"allow": ["Bash(make *)"]}}))
            quiet_install(root)
            manifest = json.loads((root / ".claude/harness-kit.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], runtime.VERSION)
            self.assertEqual(manifest["python"], "python")
            self.assertIn(".claude/skills/hk-implement/SKILL.md", manifest["files"])
            self.assertEqual(len([f for f in manifest["files"] if f.startswith(".claude/skills/")]), 16)
            self.assertEqual(len([f for f in manifest["files"] if f.startswith(".claude/agents/")]), 6)
            code, report = installer.doctor(root)
            self.assertEqual(code, 0, report)
            self.assertIn("HEALTHY", report)
            # update: an unchanged file is refreshed, a changed one is kept and proposed, a dropped one is removed
            mine = root / ".claude/skills/hk-plan/SKILL.md"
            mine.write_text("my own plan skill", encoding="utf-8")
            stale = root / ".claude/agents/hk-reviewer.md"
            stale.write_bytes(stale.read_bytes() + b"old line\n")
            manifest["files"][".claude/agents/hk-reviewer.md"] = scaffold._sha_file(stale)  # as if installed so
            manifest["files"][".claude/agents/hk-dropped.md"] = scaffold._sha("dropped\n")
            (root / ".claude/agents/hk-dropped.md").write_bytes(b"dropped\n")  # LF, as the installer writes
            (root / ".claude/harness-kit.json").write_text(json.dumps(manifest), encoding="utf-8")
            out = installer.update(root)
            self.assertEqual(mine.read_text(encoding="utf-8"), "my own plan skill")
            self.assertTrue((root / ".harness/proposals/.claude/skills/hk-plan/SKILL.md").exists())
            self.assertNotIn("old line", stale.read_text(encoding="utf-8"))
            self.assertFalse((root / ".claude/agents/hk-dropped.md").exists())
            self.assertIn("kept your version", out)
            self.assertEqual(installer.doctor(root)[0], 0)
            # a second update with nothing new changes nothing
            before = {p: p.read_bytes() for p in (root / ".claude").rglob("*") if p.is_file() and p.name != "harness-kit.json"}
            installer.update(root)
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            # uninstall: user content and settings survive, harness entries go
            (root / ".claude/agents/mine.md").write_text("mine", encoding="utf-8")
            installer.uninstall(root)
            s = json.loads((root / ".claude/settings.json").read_text(encoding="utf-8"))
            self.assertEqual(s["model"], "opus")
            self.assertEqual(s["permissions"]["allow"], ["Bash(make *)"])
            self.assertNotIn("hooks", s)
            self.assertNotIn("statusLine", s)
            self.assertTrue((root / ".claude/agents/mine.md").exists())
            self.assertTrue(mine.exists())  # changed by the user: kept
            self.assertFalse((root / ".claude/hooks").exists())
            self.assertFalse((root / ".claude/harness-kit.json").exists())
            self.assertTrue((root / "AGENTS.md").exists())  # project content stays

    def test_install_upgrades_an_old_runtime_and_keeps_user_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_python_project(root)
            scaffold.retrofit(root)
            (root / "scripts/harness.py").write_text("#!/usr/bin/env python3\n# File managed by harness-kit (updated via "
                                                     "`harness upgrade`)\n# old runtime\n", encoding="utf-8")
            (root / ".harness/.gitignore").write_text("trace.jsonl\n", encoding="utf-8")
            (root / ".claude").mkdir()
            (root / ".claude/settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "mine"}}))
            out = quiet_install(root)
            self.assertEqual((root / "scripts/harness.py").read_text(encoding="utf-8"),
                             Path(runtime.__file__).read_text(encoding="utf-8"))
            self.assertEqual(json.loads((root / ".claude/settings.json").read_text())["statusLine"]["command"], "mine")
            self.assertIn("kept your statusLine", out)
            ignore = (root / ".harness/.gitignore").read_text(encoding="utf-8").splitlines()
            self.assertEqual(ignore[0], "trace.jsonl")
            self.assertIn("activity.jsonl", ignore)

    def test_v1_items_removed_only_when_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_python_project(root)
            for rel, text in ((".claude/agents/harness-checker.md", "v1 checker\n"),
                              (".claude/skills/harness-optimize/SKILL.md", "v1 skill, edited by the user\n")):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(text.encode("utf-8"))  # LF, as v1 wrote them
            legacy = {".claude/agents/harness-checker.md": scaffold._sha("v1 checker\n"),
                      ".claude/skills/harness-optimize/SKILL.md": scaffold._sha("v1 skill\n")}
            original, scaffold.LEGACY_ITEMS = scaffold.LEGACY_ITEMS, legacy
            try:
                out = quiet_install(root)
            finally:
                scaffold.LEGACY_ITEMS = original
            self.assertFalse((root / ".claude/agents/harness-checker.md").exists())
            self.assertTrue((root / ".claude/skills/harness-optimize/SKILL.md").exists())
            self.assertIn("you changed: kept", out)

    def test_global_install_has_no_hooks(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            installer.install_global(dest)
            self.assertTrue((dest / "skills/hk-start/SKILL.md").exists())
            self.assertTrue((dest / "agents/hk-reviewer.md").exists())
            self.assertFalse((dest / "hooks").exists())
            self.assertFalse((dest / "settings.json").exists())
            (dest / "skills/hk-start/SKILL.md").write_text("mine", encoding="utf-8")
            out = installer.install_global(dest)  # re-run keeps the user's change
            self.assertEqual((dest / "skills/hk-start/SKILL.md").read_text(encoding="utf-8"), "mine")
            self.assertIn("Kept 1", out)

    def test_uninstall_never_leaves_hooks_without_their_dispatcher(self):
        """`--python py` hooks were not recognized: update duplicated them, uninstall left them pointing at a deleted
        dispatcher (exit 2 blocks every prompt)."""
        for python in ("py", "py -3.12", "python3.12", "uv run python"):
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                make_python_project(root)
                with contextlib.redirect_stdout(io.StringIO()):
                    installer.install(root, python=python)
                    installer.update(root)
                hooks = json.loads((root / ".claude/settings.json").read_text(encoding="utf-8"))["hooks"]
                for event, entries in hooks.items():
                    ours = [h for e in entries for h in e["hooks"] if scaffold._is_harness_hook(h)]
                    self.assertEqual(len(ours), 1, (python, event))
                installer.uninstall(root)
                self.assertFalse((root / ".claude/settings.json").exists(), python)
                self.assertFalse((root / ".claude/hooks").exists(), python)
        with tempfile.TemporaryDirectory() as d:  # settings.json cannot be stripped: keep the dispatcher
            root = Path(d)
            make_python_project(root)
            quiet_install(root)
            (root / ".claude/settings.json").write_text("{ not json", encoding="utf-8")
            out = installer.uninstall(root)
            self.assertTrue((root / ".claude/hooks/harness_hook.py").exists())
            self.assertIn("Kept .claude/hooks/harness_hook.py", out)

    def test_reinstall_is_a_quiet_update_and_unmanaged_runtime_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_python_project(root)
            quiet_install(root)
            proposals = sorted(p.as_posix() for p in (root / ".harness").rglob("proposals/**/*"))
            manifest = (root / ".claude/harness-kit.json").read_bytes()
            out = quiet_install(root)  # the documented way to update
            self.assertIn("is updated and active", out)
            self.assertNotIn("Legacy instructions", out)
            self.assertEqual(sorted(p.as_posix() for p in (root / ".harness").rglob("proposals/**/*")), proposals)
            self.assertEqual((root / ".claude/harness-kit.json").read_bytes(), manifest)  # unchanged: not rewritten
            self.assertIn("proposals/", (root / ".harness/.gitignore").read_text(encoding="utf-8").splitlines())
            trace = root / ".harness/trace.jsonl"
            before = trace.read_text(encoding="utf-8") if trace.exists() else ""
            installer.doctor(root)
            self.assertEqual(trace.read_text(encoding="utf-8") if trace.exists() else "", before)  # doctor is read-only
            for skill in ("hk-init", "hk-audit"):  # no machine-specific kit path in committed files
                self.assertNotIn(scaffold.KIT_ROOT.as_posix(),
                                 (root / f".claude/skills/{skill}/SKILL.md").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_python_project(root)
            (root / "scripts").mkdir()
            (root / "scripts/harness.py").write_text("print('my own test harness')\n", encoding="utf-8")
            out = quiet_install(root)
            self.assertEqual((root / "scripts/harness.py").read_text(encoding="utf-8"), "print('my own test harness')\n")
            self.assertIn("is not the harness-kit runtime", out)

    def test_payload_frontmatter_is_valid(self):
        """Skill names match their directory (Agent Skills spec); agents declare name, description and tools."""
        for rel, content in scaffold.claude_payload():
            if rel.endswith("SKILL.md") or rel.startswith(".claude/agents/"):
                m = re.match(r"^---\n(.*?)\n---\n", content, re.S)
                self.assertIsNotNone(m, rel)
                fm = dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)
                self.assertTrue(fm.get("description"), rel)
                self.assertLess(len(fm["description"]), 1024, rel)
                expected = rel.split("/")[-2] if rel.endswith("SKILL.md") else Path(rel).stem
                self.assertEqual(fm.get("name"), expected, rel)
                if rel.startswith(".claude/agents/"):
                    self.assertTrue(fm.get("tools"), rel)
            self.assertNotIn("{{", content, rel)


if __name__ == "__main__":
    unittest.main()
