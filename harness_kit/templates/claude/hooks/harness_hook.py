#!/usr/bin/env python3
"""harness-kit hook dispatcher for Claude Code (installed by `harness install`, replaced by `harness update`).

Claude Code runs:  python "${CLAUDE_PROJECT_DIR}/.claude/hooks/harness_hook.py" <event>
The dispatcher loads the project's runtime (scripts/harness.py) in-process and runs `hook-<event>` (or `statusline`).

Fail-open by design: a missing runtime, an older runtime without the command, or a crash ends with exit 0 and a
warning the user sees (`systemMessage`) — hooks are cheap guardrails, the real gates are `verify` and `exit-check`,
and a broken hook must never block a session. Exception, opt-in: `"guard_failure": "closed"` in .harness/config.json
makes a crashed guard deny the tool call. Only the Stop hook may return 2 (that is how it asks the agent to clean up
before finishing). Secrets are protected by `permissions.deny` in settings.json, which does not depend on this file.
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # never leave scripts/__pycache__ behind in the user's project

EVENTS = {
    "session-start": "hook-session-start",
    "prompt": "hook-prompt",
    "guard": "hook-guard",
    "activity": "hook-activity",
    "precompact": "hook-precompact",
    "stop": "hook-stop",
    "session-end": "hook-session-end",
    "statusline": "statusline",
}


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def emit(stream, text: str) -> None:
    """Claude Code reads hook output as UTF-8, whatever the console code page is."""
    if not text:
        return
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        stream.flush()
        buf.write(text.encode("utf-8"))
        buf.flush()
    else:
        stream.write(text)


def guard_fails_closed(root: Path) -> bool:
    """`"guard_failure": "closed"` in .harness/config.json: a crashed guard denies the tool call instead of allowing it."""
    try:
        return json.loads((root / ".harness" / "config.json").read_text(encoding="utf-8")).get("guard_failure") == "closed"
    except (OSError, ValueError, AttributeError):
        return False


def failed(event: str, root: Path, why: str) -> int:
    """Fail open, visibly (a warning the user sees, not only stderr) — or, for a fail-closed guard, deny."""
    msg = f"harness-kit hook '{event}' failed: {why.strip()[-400:]}"
    if event == "guard" and guard_fails_closed(root):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": msg + " (guard_failure: closed)."}}))
        return 0
    emit(sys.stderr, msg + " — failed open\n")
    if event != "statusline":  # the status line's stdout is the status text itself
        print(json.dumps({"systemMessage": msg + " — the harness hook was skipped (fail-open)."}))
    return 0


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    if event not in EVENTS or os.environ.get("HARNESS_HOOKS", "on").lower() in ("off", "0", "false"):
        return 0
    root = project_root()
    runtime = root / "scripts" / "harness.py"
    if not runtime.is_file():
        if event == "session-start" and (root / ".claude" / "harness-kit.json").is_file():
            print(json.dumps({"systemMessage": "harness-kit: scripts/harness.py is missing, so the harness hooks are "
                                               "off. Re-run the install command to restore it."}))
        return 0
    err, out = io.StringIO(), io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location("harness_runtime", runtime)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = module.main([EVENTS[event]], root=root)
    except SystemExit as exc:  # argparse (unknown command in an older runtime) or a domain error
        code = exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001 - fail open (closed for a configured guard), but say why
        return failed(event, root, f"{type(exc).__name__}: {exc}")
    emit(sys.stdout, out.getvalue())
    if event == "stop" and code == 2:
        emit(sys.stderr, err.getvalue())
        return 2
    if code not in (0, None) and err.getvalue() and not out.getvalue().strip():
        return failed(event, root, err.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(main())
