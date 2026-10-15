"""Offline stand-in for `claude -p`, used by LoopTests.

The script named in FAKE_AGENT_PLAN holds, per role (HARNESS_ROLE = maker | reviewer), a list of steps; call N of a
role uses step N (the last step repeats). A step may write files (relative to the cwd), list shell `commands` it
"ran", and sets the result. Output mimics `--output-format stream-json --verbose`: an init message, one assistant
message per tool call, then the result. With `"hook": true` the step runs the PreToolUse hook from `--settings` before
each tool call, as Claude Code does; a denied call gets an error tool_result. `refused` lists calls the CLI's permission
mode rejects (reported in permission_denials with their tool_use_id). `forge_log` rewrites the run's events.jsonl, as
code written by an agent could. The prompt arrives on stdin, like the real CLI.
Every call's argv and prompt are appended to <plan>.state.json for assertions.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

role = os.environ.get("HARNESS_ROLE", "maker")
prompt = sys.stdin.read()
plan_path = Path(os.environ["FAKE_AGENT_PLAN"])
plan = json.loads(plan_path.read_text(encoding="utf-8"))
state_path = plan_path.with_suffix(".state.json")
state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"calls": []}
n = sum(1 for c in state["calls"] if c["role"] == role)
state["calls"].append({"role": role, "argv": sys.argv[1:], "prompt": prompt, "feature": os.environ.get("HARNESS_FEATURE"),
                       "harness_root_env": os.environ.get("HARNESS_ROOT")})
state_path.write_text(json.dumps(state), encoding="utf-8")

steps = plan[role]
step = steps[min(n, len(steps) - 1)]
argv = sys.argv[1:]
session_id = step.get("session_id", f"fake-{role}-{len(state['calls'])}")


def emit(msg: dict) -> None:
    print(json.dumps(msg), flush=True)


calls = 0


def tool_call(tool: str, tool_input: dict) -> str:
    """Emit a tool_use like the CLI and return its id."""
    global calls
    calls += 1
    use_id = f"toolu_{session_id}_{calls}"
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": use_id, "name": tool,
                                                         "input": tool_input}]}})
    return use_id


def tool_result(use_id: str, content: str, is_error: bool = False) -> None:
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": content,
                                                    "is_error": is_error}]}})


def hook_allows(tool: str, tool_input: dict) -> tuple[bool, str]:
    if not step.get("hook") or "--settings" not in argv:
        return True, ""
    settings = json.loads((Path.cwd() / argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    res = subprocess.run(command, shell=True, input=payload, capture_output=True, text=True)
    return res.returncode != 2, res.stderr


def run_tool(tool: str, tool_input: dict) -> bool:
    """A tool call as Claude Code makes it: the hook first; a denied call still appears, with an error result."""
    use_id = tool_call(tool, tool_input)
    allowed, why = hook_allows(tool, tool_input)
    tool_result(use_id, "ok" if allowed else f"PreToolUse:{tool} hook error: {why}", is_error=not allowed)
    return allowed


mode = argv[argv.index("--permission-mode") + 1] if "--permission-mode" in argv else None
emit({"type": "system", "subtype": "init", "session_id": session_id, "model": "fake-model", "permissionMode": mode})
for cmd in step.get("commands", []):
    run_tool("Bash", {"command": cmd})
for rel, content in step.get("write", {}).items():
    target = Path.cwd() / rel
    if run_tool("Write", {"file_path": str(target)}):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
denials = list(step.get("denials", []))
for refused in step.get("refused", []):  # calls the CLI's permission mode refuses (not on the allowlist)
    tin = {"command": refused["input"]} if refused["tool"] in ("Bash", "PowerShell") else {"file_path": refused["input"]}
    use_id = tool_call(refused["tool"], tin)
    tool_result(use_id, f"Permission to use {refused['tool']} has been denied because Claude Code is running in "
                        f"don't ask mode.", is_error=True)
    denials.append({"tool_name": refused["tool"], "tool_use_id": use_id, "tool_input": tin})
if step.get("forge_log") and os.environ.get("HARNESS_RUN"):  # code the agent wrote, rewriting the run log
    log = Path(os.environ["HARNESS_RUN"]) / "events.jsonl"
    log.write_text("".join(l + "\n" for l in log.read_text(encoding="utf-8").splitlines()
                           if '"step": "guard"' not in l and '"step": "feedback"' not in l), encoding="utf-8")
if "result" in step:  # raw stdout instead of a result message (crash, timeout, garbage)
    print(step["result"])
    sys.exit(1)
subtype = step.get("subtype", "success")
emit({"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": 3,
      "total_cost_usd": step.get("cost", 0.01), "structured_output": step.get("output"),
      "result": None if subtype != "success" else "done", "session_id": session_id,
      "permission_denials": denials})
