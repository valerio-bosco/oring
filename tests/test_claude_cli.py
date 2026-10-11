"""claude-cli backend, against a fake `claude` executable that records its arguments."""

import json
import os
import stat
import sys

from oring.client import transcript

FAKE_CLAUDE = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
prompt = args[args.index("-p") + 1]
def out(ev): print(json.dumps(ev), flush=True)
out({{"type": "system", "subtype": "init", "session_id": "sess-1"}})
if "fail" in prompt:
    print("boom", file=sys.stderr); sys.exit(1)
out({{"type": "assistant", "message": {{"content": [
    {{"type": "tool_use", "name": "mcp__desktop__elenca_finestre", "input": {{}}}},
    {{"type": "tool_use", "name": "ToolSearch", "input": {{"q": "x"}}}},
    {{"type": "text", "text": "answer to " + prompt}}]}}}})
denials = [{{"tool_name": "Bash"}}] if "denied" in prompt else []
out({{"type": "result", "is_error": False, "result": "", "permission_denials": denials}})
"""


def setup(daemon, tmp_path, **agent):
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "claude.log"
    d = daemon([{"id": "claude", "type": "claude-cli", "command": str(exe), **agent}],
               env={"FAKE_CLAUDE_LOG": str(log), "ORING_BIN": "/fake/oring"})
    return d, log


def calls(log):
    return [json.loads(l) for l in log.read_text().splitlines()]


def test_turns_and_resume(daemon, tmp_path):
    d, log = setup(daemon, tmp_path)
    with d.client() as c:
        entries = transcript(c.turn("first"))
        c.turn("second")
    assert {"role": "tool", "text": "Looking at open windows"} in entries
    assert not any("ToolSearch" in e["text"] for e in entries)      # hidden internal tool
    assert {"role": "agent", "text": "answer to first"} in entries
    first, second = calls(log)
    assert "--resume" not in first
    assert second[second.index("--resume") + 1] == "sess-1"


def test_permission_prompt_tool(daemon, tmp_path):
    d, log = setup(daemon, tmp_path)
    with d.client() as c:
        c.turn("hi")
    args = calls(log)[0]
    assert args[args.index("--permission-prompt-tool") + 1] == "mcp__oring__approve"
    mcp = json.loads(open(args[args.index("--mcp-config") + 1]).read())["mcpServers"]
    assert mcp["oring"]["command"] == "/fake/oring" and mcp["oring"]["args"] == ["permission-mcp"]
    assert mcp["oring"]["env"]["ORING_AGENT"] == "claude"
    assert "--strict-mcp-config" in args


def test_permissions_deny_mode(daemon, tmp_path):
    d, log = setup(daemon, tmp_path, permissions="deny", allowed_tools=["Read"])
    with d.client() as c:
        c.turn("hi")
    args = calls(log)[0]
    assert "--permission-prompt-tool" not in args
    assert args[args.index("--allowedTools") + 1:args.index("--allowedTools") + 2] == ["Read"]


def test_denials_and_failures(daemon, tmp_path):
    d, _ = setup(daemon, tmp_path)
    with d.client() as c:
        assert {"role": "error", "text": "not allowed: Bash"} in transcript(c.turn("denied"))
        entries = transcript(c.turn("fail"))
    assert entries[-1] == {"role": "error", "text": "boom"}


def test_new_conversation_drops_session(daemon, tmp_path):
    d, log = setup(daemon, tmp_path)
    with d.client() as c:
        c.turn("a")
        c.op("new")
        c.state()
        c.turn("b")
    assert "--resume" not in calls(log)[1]
