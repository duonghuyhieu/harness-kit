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
from tests.test_kit import OK_VERIFY, PY, git, make_python_project, run_rt, set_commands, set_config  # noqa: E402


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
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY, "--scope", "app/*")
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

    # ---- 2.1.0: requirements R5, R11, R13, R18 and the instruction guard ----

    def test_stop_requires_verification_after_the_last_change(self):
        """R11: finishing a turn with changes the active feature's verification has not seen is blocked once."""
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY, "--scope", "app/*")
        run_rt(self.root, "next", "--id", "F1")
        run_rt(self.root, "start", "--no-check")
        (self.root / "app" / "new_mod.py").write_text("X = 1\n", encoding="utf-8")
        code, _, err = run_dispatcher(self.root, "stop", {"session_id": "s1"})
        self.assertEqual(code, 2)
        self.assertIn("not verified", err)
        self.assertEqual(run_dispatcher(self.root, "stop", {"stop_hook_active": True})[0], 0)  # never loops
        run_rt(self.root, "try", "F1")
        self.assertEqual(run_dispatcher(self.root, "stop", {"session_id": "s1"})[0], 0)

    def test_guard_failure_is_visible_or_closed(self):
        """R5: a crashed guard fails open with a warning the user sees, or denies when guard_failure is closed."""
        (self.root / "scripts" / "harness.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
        code, out, _ = run_dispatcher(self.root, "guard", self.write("app/x.py"))
        self.assertEqual(code, 0)
        self.assertIn("fail-open", json.loads(out)["systemMessage"])
        set_config(self.root, guard_failure="closed")
        out = hook_output(run_dispatcher(self.root, "guard", self.write("app/x.py"))[1])
        self.assertEqual(out["permissionDecision"], "deny")

    def test_instruction_files_inline_scripts_and_switches(self):
        self.assertEqual(self.decision(self.write("AGENTS.md")), "ask")
        self.assertEqual(self.decision(self.write(".claude/skills/hk-plan/SKILL.md")), "ask")
        def bash(cmd):
            return self.decision({"tool_name": "Bash", "tool_input": {"command": cmd}})
        self.assertEqual(bash("python -c \"open('feature_list.json','w').write('[]')\""), "deny")
        self.assertIsNone(bash("python -c \"import json; print(json.load(open('feature_list.json')))\""))
        set_config(self.root, instruction_guard="off", hooks_off=["prompt"])
        self.assertIsNone(self.decision(self.write("AGENTS.md")))
        run_rt(self.root, "next")
        self.assertEqual(run_dispatcher(self.root, "prompt", {"prompt": "hi"})[1].strip(), "")  # switched off
        set_config(self.root, hooks_off=["guard"], bogus_key=1)
        self.assertIsNone(self.decision(self.write(".harness/session.json")))
        self.assertIn('"disabled"', (self.root / ".harness/trace.jsonl").read_text(encoding="utf-8"))  # logged
        code, out = run_rt(self.root, "validate")
        self.assertNotEqual(code, 0)
        self.assertIn("unknown key `bogus_key`", out)

    def test_session_start_names_the_next_action_instead_of_a_clock_in(self):
        """Experiment 5b: "clock in with start" made agents spend 31% of their calls re-reading state they already had."""
        run_rt(self.root, "add", "--id", "F1", "--behavior", "first thing", "--verify", OK_VERIFY, "--scope", "app/*",
               "--priority", "1")
        ctx = hook_output(run_dispatcher(self.root, "session-start", {"source": "startup"})[1])["additionalContext"]
        self.assertIn("you do not need `python scripts/harness.py start`", ctx)
        self.assertRegex(ctx, r"Next: `python scripts/harness.py next --id (H001|F1)`")
        run_rt(self.root, "next", "--id", "F1")
        ctx = hook_output(run_dispatcher(self.root, "session-start", {"source": "resume"})[1])["additionalContext"]
        self.assertIn("Next: continue F1", ctx)

    def test_generated_files_are_never_scope_drift(self):
        """Experiment 4: a loop commit tracked __pycache__; the next test run changed the .pyc and blocked a feature."""
        self.assertTrue(runtime.is_generated("src/pkg/__pycache__/mod.cpython-312.pyc"))
        self.assertTrue(runtime.is_generated("Main.class"))
        self.assertFalse(runtime.is_generated("src/pkg/mod.py"))
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY, "--scope", "app/*")
        cache = self.root / "app" / "__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "x.cpython-312.pyc").write_bytes(b"old")
        subprocess.run(["git", "add", "-A", "-f"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "track bytecode"], cwd=self.root, capture_output=True)
        run_rt(self.root, "next", "--id", "F1")
        (cache / "x.cpython-312.pyc").write_bytes(b"new")
        h = runtime.Harness(self.root)
        f = next(x for x in h.load_features() if x["id"] == "F1")
        self.assertEqual(runtime._loop_drift(h, f), [])
        self.assertEqual(runtime.scope_drift(h, f), [])

    def test_loop_reviewer_may_run_the_active_feature_verification(self):
        """Experiment 4: loop reviewers were denied the feature's own verification command and returned FAIL."""
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", "python -m unittest tests.test_part2 -q")
        run_rt(self.root, "next", "--id", "F1")
        tools = runtime._allowed_tools(runtime.Harness(self.root), "reviewer")
        self.assertIn("Bash(python -m unittest tests.test_part2 -q)", tools)
        self.assertIn("Bash(python -m unittest tests.test_part2 *)", tools)  # -v instead of -q
        self.assertIn("PowerShell(python -m unittest tests.test_part2 -q)", tools)
        self.assertNotIn("Edit", tools)

    def test_stop_flags_work_on_a_feature_nobody_activated(self):
        """Experiment 2 (sonnet): code for F1 written without `next`/`verify`; the next session redid it."""
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY, "--scope", "app/*")
        run_rt(self.root, "start", "--no-check")
        (self.root / "app" / "feature_code.py").write_text("X = 2\n", encoding="utf-8")
        code, _, err = run_dispatcher(self.root, "stop", {"session_id": "s9"})
        self.assertEqual(code, 2)
        self.assertIn("next --id F1", err)

    def test_verify_explains_a_red_baseline_from_future_features(self):
        """Experiment 2: up-front tests of later features keep the project check red; verify must say why."""
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY)
        run_rt(self.root, "add", "--id", "F2", "--behavior", "c", "--verify", OK_VERIFY)
        set_commands(self.root, test=f"{PY} -c \"import sys; sys.exit(1)\"")
        set_config(self.root, review_gate="off")  # this test is about the project check; ReviewGateTests covers reviews
        run_rt(self.root, "next", "--id", "F1")
        code, out = run_rt(self.root, "verify", "F1")
        self.assertNotEqual(code, 0)
        self.assertIn("baseline is red by design", out)
        self.assertIn("F2", out)

    def test_injected_context_is_logged_with_a_hash(self):
        """R13: what the model saw can be traced back: session id, event, content hash."""
        run_dispatcher(self.root, "session-start", {"source": "startup", "session_id": "sess-42"})
        recs = [json.loads(line) for line in (self.root / ".harness/trace.jsonl").read_text(encoding="utf-8").splitlines()]
        rec = next(r for r in recs if r["cmd"] == "hook-session-start")
        self.assertEqual(rec["session"], "sess-42")
        self.assertEqual(len(rec["context_sha256"]), 16)
        code, out = run_rt(self.root, "trace", "--grep", "sess-42")
        self.assertIn("context_sha256", out)

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

    def test_update_respects_removals_and_retires_obsolete_rules(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_python_project(root)
            quiet_install(root)
            sp, mp = root / ".claude/settings.json", root / ".claude/harness-kit.json"
            s = json.loads(sp.read_text(encoding="utf-8"))
            self.assertIn("PowerShell(git push --force *)", s["permissions"]["deny"])  # keeps the PowerShell tool on
            s["permissions"]["ask"].remove("Bash(git push *)")
            del s["hooks"]["UserPromptSubmit"]
            del s["statusLine"]
            s["permissions"]["allow"].append("Bash(python scripts/harness.py *)")  # as 2.0.0 added it
            sp.write_text(json.dumps(s), encoding="utf-8")
            m = json.loads(mp.read_text(encoding="utf-8"))
            m["settings_added"]["allow"].append("Bash(python scripts/harness.py *)")
            mp.write_text(json.dumps(m), encoding="utf-8")
            out = installer.update(root)
            s = json.loads(sp.read_text(encoding="utf-8"))
            self.assertNotIn("Bash(git push *)", s["permissions"]["ask"])
            self.assertNotIn("UserPromptSubmit", s["hooks"])
            self.assertNotIn("statusLine", s)
            self.assertNotIn("Bash(python scripts/harness.py *)", s["permissions"]["allow"])  # retired
            self.assertIn("kept your removal", out)
            code, report = installer.doctor(root)
            self.assertEqual(code, 0, report)  # a hook removed on purpose is a warning, not a failure
            self.assertIn("[WARN] hooks registered", report)
            installer.update(root, force=True)  # --force restores everything
            s = json.loads(sp.read_text(encoding="utf-8"))
            self.assertIn("UserPromptSubmit", s["hooks"])
            self.assertIn("Bash(git push *)", s["permissions"]["ask"])
            dry = installer.install(root, dry_run=True, diff=True)  # R9: dry-run with a diff
            self.assertIn("Diff of existing files", dry)
            installer.uninstall(root)
            self.assertFalse(sp.exists())  # nothing of the user's was in it, not even a leftover $schema

    def test_loop_leaves_harness_setup_features_to_hk_init(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_python_project(root)
            quiet_install(root)
            run_rt(root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY)
            picked = runtime._pick_loop_feature(runtime.Harness(root), None)
            self.assertEqual(picked["id"], "F1")

    def test_mini_toml_matches_tomllib(self):
        """Python 3.10 has no tomllib: the fallback reader must read pyproject/Cargo files the same way (CI 3.10)."""
        from harness_kit import detect
        docs = [
            '[project]\nname = "app"\nversion = "1.0"\nrequires-python = ">=3.10"\n'
            'dependencies = [\n  "fastapi>=0.1",  # web\n  \'pydantic\',\n]\n'
            '[project.optional-dependencies]\ndev = ["pytest", "ruff"]\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\naddopts = "-q"\n'
            '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = { version = "^2.31", optional = true }\n'
            '[tool.ruff]\nline-length = 120\n[tool.ruff.lint]\nselect = ["E", "F"]\n',
            '[package]\nname = "crate"\nedition = "2021"\n[workspace]\nmembers = ["a", "b/*"]\n'
            '[[bin]]\nname = "x"\npath = "src/x.rs"\n[[bin]]\nname = "y"\n'
            '[dependencies]\nserde = { version = "1", features = ["derive"] }\n'
            '"quoted.key" = 1\nsite."google.com" = true\nf = 1.5e3\nn = 1_000\nd = 1979-05-27\n'
            "s = 'C:\\\\no\\\\escape'\nm = \"\"\"\nline1\nline2\"\"\"\ne = \"tab\\tq\\\"u\\u00e9\"\n",
            '[toolchain]\nchannel = "1.78"\ncomponents = [ "rustfmt", "clippy" ]\n',
        ]
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest("tomllib needed as the oracle")
        for doc in docs:
            self.assertEqual(detect.mini_toml(doc), tomllib.loads(doc))
        with self.assertRaises(ValueError):
            detect.mini_toml("[broken\nx = ")

    def test_every_config_key_the_code_reads_has_a_default(self):
        """R18: the config schema (DEFAULT_CONFIG + validation) matches the keys the runtime actually reads."""
        src = Path(runtime.__file__).read_text(encoding="utf-8")
        read = set(re.findall(r'cfg\.get\("([a-z_]+)"', src)) | set(re.findall(r'cfg\["([a-z_]+)"\]', src))
        self.assertFalse(read - set(runtime.DEFAULT_CONFIG), read - set(runtime.DEFAULT_CONFIG))
        self.assertEqual(runtime.config_problems(dict(runtime.DEFAULT_CONFIG)), [])
        self.assertTrue(runtime.config_problems({"scope_guard": "maybe", "trace_max_lines": "many"}))

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


class HarnessProjectCase(unittest.TestCase):
    """A temporary Python project with the kit installed and a passing project check. The review gate is off here
    (these tests are about other gates); ReviewGateTests switches it on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        make_python_project(self.root)
        quiet_install(self.root)
        set_commands(self.root, test=OK_VERIFY)
        set_config(self.root, review_gate="off")

    def tearDown(self):
        self._tmp.cleanup()

    def features(self) -> dict:
        data = json.loads((self.root / "feature_list.json").read_text(encoding="utf-8"))
        return {f["id"]: f for f in (data["features"] if isinstance(data, dict) else data)}

    def set_feature(self, fid: str, **fields) -> None:
        p = self.root / "feature_list.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for f in data["features"] if isinstance(data, dict) else data:
            if f["id"] == fid:
                f.update(fields)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TrivialVerificationTests(HarnessProjectCase):
    """V014: a verification command that cannot fail is refused by add, verify and validate."""

    def test_detector(self):
        for cmd in ("echo ok", "true", "exit 0", "python -c pass", f'{PY} -c "print(1)"', "cd app && echo done",
                    "pytest || true", "npm test || echo failed", "pytest; exit 0", "pytest | tail -5",
                    "python -m unittest 2>&1 | tee log", "cmd /c exit 0", ""):
            self.assertTrue(runtime.trivial_verification(cmd), cmd)
        for cmd in ("pytest", "python -m unittest discover -s tests -q", "set -o pipefail; pytest | tail -5",
                    "pytest && echo ok", OK_VERIFY, "cd app && npm test", 'grep -q "a|b" f.txt', "pytest; echo done",
                    "curl -fsS http://localhost:8000/health"):
            self.assertIsNone(runtime.trivial_verification(cmd), cmd)

    def test_add_refuses_and_warn_mode_allows(self):
        code, out = run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", "pytest || true")
        self.assertEqual(code, 1)
        self.assertIn("cannot fail", out)
        self.assertIn("FIX:", out)
        self.assertNotIn("F1", self.features())
        set_config(self.root, trivial_verification="warn")
        code, out = run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", "echo ok")
        self.assertEqual(code, 0)
        self.assertIn("WARNING", out)
        self.assertIn("F1", self.features())

    def test_verify_refuses_a_trivial_command_set_by_hand(self):
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY)
        self.set_feature("F1", verification="echo all good")  # not started yet: the guard lets this edit through
        code, out = run_rt(self.root, "validate")
        self.assertEqual(code, 1)
        self.assertIn("cannot fail", out)
        run_rt(self.root, "next", "--id", "F1")
        code, out = run_rt(self.root, "verify", "F1")
        self.assertEqual(code, 1)
        self.assertIn("cannot fail", out)
        self.assertEqual(self.features()["F1"]["status"], "active")
        trace = (self.root / ".harness" / "trace.jsonl").read_text(encoding="utf-8")
        self.assertIn("trivial verification", trace)

    def test_verify_warns_about_a_test_file_without_assertions(self):
        (self.root / "tests" / "test_empty.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_nothing(self):\n        pass\n", encoding="utf-8")
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", f"{PY} -m unittest tests/test_empty.py")
        run_rt(self.root, "next", "--id", "F1")
        code, out = run_rt(self.root, "verify", "F1")
        self.assertEqual(code, 0, out)
        self.assertIn("tests/test_empty.py is named by the verification command but contains no assertion", out)


class CheckerGuardTests(HarnessProjectCase):
    """V015: the guard denies hook bypasses and asks before edits that weaken the checker."""

    def decide(self, payload: dict) -> str | None:
        _, out, _ = run_dispatcher(self.root, "guard", payload)
        self.assertNotIn('"allow"', out)
        return hook_output(out).get("permissionDecision")

    def bash(self, command: str, tool: str = "Bash") -> str | None:
        return self.decide({"tool_name": tool, "tool_input": {"command": command}})

    def edit(self, rel: str, old: str, new: str) -> str | None:
        return self.decide({"tool_name": "Edit", "tool_input": {"file_path": str(self.root / rel), "old_string": old,
                                                                "new_string": new}})

    def test_hook_bypasses_are_denied_in_every_configuration(self):
        set_config(self.root, checker_guard="off", scope_guard="off", instruction_guard="off")
        for cmd in ("git commit --no-verify -m x", "git commit -nm x", "git -c core.hooksPath=/dev/null commit -m x",
                    "git config core.hooksPath .nohooks", "GIT_CONFIG_COUNT=1 git commit -m x",
                    'bash -c "git commit --no-verify -m x"', "claude --safe-mode -p hi", "git push --no-verify",
                    "CLAUDE_CONFIG_DIR=/tmp/x claude -p hi", "echo '{\"disableAllHooks\": true}' > .claude/settings.local.json"):
            self.assertEqual(self.bash(cmd), "deny", cmd)
        self.assertEqual(self.bash("$env:GIT_CONFIG_COUNT=1; git commit -m x", "PowerShell"), "deny")
        for cmd in ('git commit -m "explain the --no-verify policy"', "git config --get core.hooksPath", "git push -n",
                    "git log --grep commit -n 5", "git commit --amend -m x"):
            self.assertIsNone(self.bash(cmd), cmd)
        outside = Path(self._tmp.name).parent / "user-settings.json"  # e.g. ~/.claude/settings.json
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(outside), "content": '{"disableAllHooks": true}'}}
        self.assertEqual(self.decide(payload), "deny")

    def test_checker_config_edits_ask(self):
        (self.root / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
        self.assertEqual(self.edit("pytest.ini", "addopts = -q", "addopts = -q -k 'not slow'"), "ask")
        self.assertEqual(self.edit("pyproject.toml", "[tool.ruff]\n", "[tool.ruff]\nignore = ['E']\n"), "ask")
        self.assertIsNone(self.edit("pyproject.toml", 'name="app"', 'name="app2"'))  # not checker settings
        (self.root / "package.json").write_text('{\n  "name": "a",\n  "scripts": {\n    "test": "jest"\n  }\n}\n',
                                                encoding="utf-8")
        self.assertEqual(self.edit("package.json", '"test": "jest"', '"test": "jest --passWithNoTests"'), "ask")
        self.assertIsNone(self.edit("package.json", '"name": "a"', '"name": "b"'))
        new_rules = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / ".harness/rules.json"),
                                                          "content": "[]"}}
        self.assertEqual(self.decide(new_rules), "ask")
        set_config(self.root, checker_guard="off")
        self.assertIsNone(self.edit("pytest.ini", "addopts = -q", "addopts = -q -x"))

    def test_loosening_or_deleting_tests_asks(self):
        test = "tests/test_logic.py"  # def test_x():\n    assert True\n
        self.assertEqual(self.edit(test, "    assert True", "    pass"), "ask")
        self.assertEqual(self.edit(test, "    assert True", "    # assert True"), "ask")  # commented out
        self.assertEqual(self.edit(test, "def test_x():", "@pytest.mark.skip\ndef test_x():"), "ask")
        self.assertIsNone(self.edit(test, "    assert True", "    assert True\n    assert 1 == 1"))  # stronger
        new_file = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "tests/test_new.py"),
                                                         "content": "def test_y():\n    pass\n"}}
        self.assertIsNone(self.decide(new_file))  # a new test file never weakens an existing check
        self.assertEqual(self.bash("rm tests/test_logic.py"), "ask")
        self.assertEqual(self.bash("git rm -r tests"), "ask")
        self.assertIsNone(self.bash("rm -rf build"))


class StalePassTests(HarnessProjectCase):
    """V016: a passing feature whose scope files changed since it last passed is reported as stale."""

    def pass_feature(self, fid: str, scope: str) -> None:
        run_rt(self.root, "add", "--id", fid, "--behavior", "b", "--verify", OK_VERIFY, "--scope", scope)
        run_rt(self.root, "next", "--id", fid)
        code, out = run_rt(self.root, "verify", fid)
        self.assertEqual(code, 0, out)

    def test_change_in_scope_makes_a_feature_stale_until_check_full(self):
        self.pass_feature("F1", "app/core/*")
        self.assertTrue(self.features()["F1"]["evidence"]["scope_sha256"])
        self.assertNotIn("Stale", run_rt(self.root, "status")[1])
        (self.root / "README.md").write_text("outside the scope\n", encoding="utf-8")
        self.assertNotIn("Stale", run_rt(self.root, "status")[1])
        logic = self.root / "app" / "core" / "logic.py"
        logic.write_text(logic.read_text(encoding="utf-8") + "\ndef g():\n    return 2\n", encoding="utf-8")
        out = run_rt(self.root, "status")[1]
        self.assertIn("Stale: 1 passing feature(s)", out)
        self.assertIn("F1", out)
        ctx = hook_output(run_dispatcher(self.root, "session-start", {"source": "startup"})[1])["additionalContext"]
        self.assertIn("Stale: 1", ctx)
        self.assertIn("Stale: 1", run_rt(self.root, "exit-check", "--no-run")[1])
        out = run_rt(self.root, "check", "--full")[1]
        self.assertIn("re-verified on the current code: F1", out)
        self.assertNotIn("Stale", run_rt(self.root, "status")[1])
        self.assertIn("reverified_at", self.features()["F1"]["evidence"])

    def test_commit_of_the_feature_itself_and_bookkeeping_files_are_not_changes(self):
        self.pass_feature("F2", "*.md")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "F2")
        (self.root / "PROGRESS.md").write_text("# Progress\nupdated by the handoff\n", encoding="utf-8")  # scope_exempt
        self.assertNotIn("Stale", run_rt(self.root, "status")[1])
        (self.root / "README.md").write_text("changed\n", encoding="utf-8")
        self.assertIn("F2", run_rt(self.root, "status")[1].split("Stale:")[-1])


class PorcelainTests(HarnessProjectCase):
    """V017: the Stop gate sees an unverified change even when it is the first `git status --porcelain` line
    (an unstaged modification, which `git()`'s strip() used to mangle) — the case of a one-file change."""

    def setUp(self):
        super().setUp()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "harness installed")
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY, "--scope", "app/*")
        run_rt(self.root, "next", "--id", "F1")

    def stop(self) -> tuple[int, str]:
        code, _, err = run_dispatcher(self.root, "stop", {"session_id": "s1"})
        return code, err

    def test_single_unstaged_change_blocks_the_stop_once(self):
        self.assertEqual([p for p in runtime.Harness(self.root).dirty_paths() if p.startswith("app/")], [])
        logic = self.root / "app" / "core" / "logic.py"
        logic.write_text(logic.read_text(encoding="utf-8") + "\ndef g():\n    return 2\n", encoding="utf-8")
        self.assertIn("app/core/logic.py", runtime.Harness(self.root).dirty_paths())
        code, err = self.stop()
        self.assertEqual(code, 2, err)
        self.assertIn("F1 has changes not verified", err)
        self.assertIn("app/core/logic.py", err)
        code, _, _ = run_dispatcher(self.root, "stop", {"session_id": "s1", "stop_hook_active": True})
        self.assertEqual(code, 0)  # blocks once, never loops

    def test_new_file_in_a_new_folder_counts_and_bytecode_does_not(self):
        cache = self.root / "app" / "__pycache__"
        cache.mkdir()
        (cache / "logic.cpython-312.pyc").write_bytes(b"\0")
        self.assertEqual(self.stop()[0], 0)  # generated files are never someone's work
        (self.root / "app" / "extra").mkdir()
        (self.root / "app" / "extra" / "x.py").write_text("x = 1\n", encoding="utf-8")
        code, err = self.stop()
        self.assertEqual(code, 2, err)
        self.assertIn("app/extra/x.py", err)


class ReviewGateTests(HarnessProjectCase):
    """V019: verify needs an hk-reviewer APPROVE of exactly the current change (recorded by the SubagentStop hook)."""

    def setUp(self):
        super().setUp()
        self.assertEqual(json.loads((self.root / ".harness/config.json").read_text(encoding="utf-8"))["review_gate"],
                         "off")  # set by HarnessProjectCase
        set_config(self.root, review_gate="require")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "harness installed")
        run_rt(self.root, "add", "--id", "F1", "--behavior", "b", "--verify", OK_VERIFY, "--scope", "app/*")
        run_rt(self.root, "next", "--id", "F1")
        self.logic = self.root / "app" / "core" / "logic.py"
        self.change("\ndef g():\n    return 2\n")

    def change(self, text: str) -> None:
        self.logic.write_text(self.logic.read_text(encoding="utf-8") + text, encoding="utf-8")

    def review(self, message: str, agent: str = "hk-reviewer") -> None:
        """The SubagentStop payload as Claude Code 2.1.287 sends it (observed with a probe agent)."""
        payload = {"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1", "agent_type": agent,
                   "stop_hook_active": False, "last_assistant_message": message}
        self.assertEqual(run_dispatcher(self.root, "subagent-stop", payload)[0], 0)

    def verify(self, *extra: str) -> tuple[int, str]:
        return run_rt(self.root, "verify", "F1", *extra)

    def test_parse_verdict(self):
        cases = {"checked\nVERDICT: APPROVE": "APPROVE", "**VERDICT: REJECT**": "REJECT", "Verdict: pass": "APPROVE",
                 "## Verdict\nFAIL\n\n## Issues": "REJECT", "## Verdict\nPASS | FAIL": "UNCERTAIN",
                 "VERDICT: APPROVE | REJECT | UNCERTAIN": "UNCERTAIN", "looks good to me": "UNCERTAIN", "": "UNCERTAIN",
                 "VERDICT: APPROVE\n...later...\nVERDICT: REJECT": "REJECT"}
        for text, want in cases.items():
            self.assertEqual(runtime.parse_verdict(text), want, text)

    def test_verify_needs_an_approval_of_the_current_change(self):
        code, out = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("no hk-reviewer verdict", out)
        self.review("all good\nVERDICT: APPROVE", agent="general-purpose")  # other subagents are not reviews
        self.assertEqual(self.verify()[0], 1)
        self.review("## Verdict\nFAIL\n\nVERDICT: REJECT")
        code, out = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("REJECT", out)
        self.review("fixed\nVERDICT: APPROVE")
        self.change("\n# one more edit after the review\n")
        code, out = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("changed after the last review", out)
        self.review("re-checked\nVERDICT: APPROVE")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "F1 work")  # committing between review and verify changes no content
        code, out = self.verify()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.features()["F1"]["status"], "passing")

    def test_no_review_override_is_recorded_and_warn_mode_only_warns(self):
        code, out = self.verify("--no-review", "reviewer unavailable offline")
        self.assertEqual(code, 0, out)
        incidents = (self.root / ".harness/incidents.jsonl").read_text(encoding="utf-8")
        self.assertIn("no-review", incidents)
        self.assertIn("reviewer unavailable offline", incidents)
        run_rt(self.root, "add", "--id", "F2", "--behavior", "c", "--verify", OK_VERIFY, "--scope", "app/*")
        run_rt(self.root, "next", "--id", "F2")
        set_config(self.root, review_gate="warn")
        code, out = run_rt(self.root, "verify", "F2")
        self.assertEqual(code, 0, out)
        self.assertIn("WARNING", out)

    def test_review_records_are_harness_state_and_installs_require_the_gate(self):
        forged = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / ".harness/reviews.jsonl"),
                                                       "content": '{"feature": "F1", "verdict": "APPROVE"}\n'}}
        self.assertEqual(hook_output(run_dispatcher(self.root, "guard", forged)[1])["permissionDecision"], "deny")
        bash = {"tool_name": "Bash", "tool_input": {"command": "echo x >> .harness/reviews.jsonl"}}
        self.assertEqual(hook_output(run_dispatcher(self.root, "guard", bash)[1])["permissionDecision"], "deny")
        with tempfile.TemporaryDirectory() as d:
            fresh = Path(d)
            make_python_project(fresh)
            quiet_install(fresh)
            cfg = json.loads((fresh / ".harness/config.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["review_gate"], "require")
            settings = json.loads((fresh / ".claude/settings.json").read_text(encoding="utf-8"))
            self.assertIn("SubagentStop", settings["hooks"])
        self.assertEqual(runtime.DEFAULT_CONFIG["review_gate"], "warn")  # projects without the hook are not blocked


if __name__ == "__main__":
    unittest.main()
