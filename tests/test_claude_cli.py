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


def test_images_are_passed_as_paths(daemon, tmp_path):
    d, log = setup(daemon, tmp_path)
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"jpeg")
    with d.client() as c:
        c.op("send", text="", images=[str(img), str(tmp_path / "missing.jpg")])
        while (ev := c.event(10)) and not (ev["ev"] == "busy" and ev["value"] is False):
            pass
        assert c.state()["history"][0]["text"] == "📎 pic.jpg"          # missing files are dropped
    args = calls(log)[0]
    assert args[args.index("-p") + 1] == f"Attached images (open the files to see them):\n{img}"


def test_auto_mode(daemon, tmp_path):
    d, log = setup(daemon, tmp_path, permission_mode="acceptEdits")
    with d.client() as c:
        c.turn("one")
        c.op("auto", value=True)
        c.turn("two")
    d.stop()
    d.start()                                                            # auto mode survives a restart
    with d.client() as c:
        assert c.state()["auto"] is True
    first, second = calls(log)
    assert first[first.index("--permission-mode") + 1] == "acceptEdits"
    assert second[second.index("--permission-mode") + 1] == "auto"
    assert "--permission-prompt-tool" in second                          # risky actions are still asked


def test_protected_paths(daemon, tmp_path):
    d, log = setup(daemon, tmp_path, extra_protected_paths=["~/secret/**"])
    with d.client() as c:
        c.turn("hi")
    args = calls(log)[0]
    denied = args[args.index("--disallowedTools") + 1:]
    for rule in ("Read(~/.ssh/**)", "Edit(~/.ssh/**)", "Read(~/.config/oring/**)", "Read(~/secret/**)"):
        assert rule in denied
    assert "Read" in args[args.index("--allowedTools") + 1:]             # reading stays allowed elsewhere


def test_protected_paths_can_be_disabled(daemon, tmp_path):
    d, log = setup(daemon, tmp_path, protected_paths=[])
    with d.client() as c:
        c.turn("hi")
    assert "--disallowedTools" not in calls(log)[0]
