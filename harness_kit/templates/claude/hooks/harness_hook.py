#!/usr/bin/env python3
"""harness-kit hook dispatcher for Claude Code (installed by `harness install`, replaced by `harness update`).

Claude Code runs:  python "${CLAUDE_PROJECT_DIR}/.claude/hooks/harness_hook.py" <event>
The dispatcher loads the project's runtime (scripts/harness.py) in-process and runs `hook-<event>` (or `statusline`).

Fail-open by design: a missing runtime, an older runtime without the command, or a crash ends with exit 0 and a note
on stderr — hooks are cheap guardrails, the real gates are `verify` and `exit-check`, and a broken hook must never
block a session. Only the Stop hook may return 2 (that is how it asks the agent to clean up before finishing).
Secrets are protected by `permissions.deny` in settings.json, which does not depend on this file.
"""
import contextlib
import importlib.util
import io
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


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    if event not in EVENTS or os.environ.get("HARNESS_HOOKS", "on").lower() in ("off", "0", "false"):
        return 0
    root = project_root()
    runtime = root / "scripts" / "harness.py"
    if not runtime.is_file():
        return 0
    err = io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location("harness_runtime", runtime)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with contextlib.redirect_stderr(err):
            code = module.main([EVENTS[event]], root=root)
    except SystemExit as exc:  # argparse (unknown command in an older runtime) or a domain error
        code = exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001 - fail open, but say why
        sys.stderr.write(f"harness-kit hook '{event}' failed open: {type(exc).__name__}: {exc}\n")
        return 0
    if event == "stop" and code == 2:
        sys.stderr.write(err.getvalue())
        return 2
    if code not in (0, None) and err.getvalue():
        sys.stderr.write(f"harness-kit hook '{event}' failed open: {err.getvalue()[-500:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
