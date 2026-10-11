"""ACP backend, against the scripted fake agent."""

import json
import time

from conftest import fake_acp_agent
from oring.client import transcript


def agent_texts(events):
    return [e["text"] for e in transcript(events) if e["role"] == "agent"]


def test_turn_streams_and_labels_tools(daemon):
    # tool titles are relabelled only for oring's own MCP servers: declare the desktop one
    d = daemon([fake_acp_agent()], desktop_mcp=True, env={"ORING_DESKTOP_MCP": "/fake/desktop-mcp"})
    with d.client() as c:
        events = c.turn("hello")
    entries = transcript(events)
    assert entries[0] == {"role": "user", "text": "hello"}
    assert {"role": "tool", "text": "Looking at open windows"} in entries   # desktop_* titles are relabelled
    assert agent_texts(events) == ["echo 1: hello"]                          # two chunks merged


def test_conversation_memory_and_new_session(daemon, tmp_path):
    log = tmp_path / "prompts.jsonl"
    d = daemon([fake_acp_agent(system_prompt="BE NICE")], env={"FAKE_ACP_LOG": str(log)})
    with d.client() as c:
        c.turn("one")
        assert agent_texts(c.turn("two")) == ["echo 2: two"]
        c.op("new")
        assert c.state()["history"] == []
        assert agent_texts(c.turn("three")) == ["echo 1: three"]
    prompts = [json.loads(l) for l in log.read_text().splitlines()]
    # the system prompt goes with the first message of each session only
    assert "BE NICE" in prompts[0][0] and len(prompts[1]) == 1 and "BE NICE" in prompts[2][0]


def test_permission_ask_allow_and_deny(daemon):
    d = daemon([fake_acp_agent()])
    with d.client() as c:
        assert agent_texts(c.turn("permission please", auto="allow")) == ["allowed"]
        events = c.turn("permission again", auto="deny")
        assert agent_texts(events) == ["denied"]
        asked = [e for e in events if e["ev"] == "permission"]
        assert asked[0]["title"] == "Run rm -rf /tmp/x"
        assert [o["kind"] for o in asked[0]["options"]] == ["allow_once", "allow_always", "reject_once"]
        assert any(e["ev"] == "permission_done" for e in events)


def test_permission_policies(daemon):
    d = daemon([fake_acp_agent("yes", permissions="allow"), fake_acp_agent("no", permissions="deny")])
    with d.client() as c:
        events = c.turn("permission")
        assert agent_texts(events) == ["allowed"] and not any(e["ev"] == "permission" for e in events)
        c.op("select", agent="no")
        assert agent_texts(c.turn("permission")) == ["denied"]


def test_cancel(daemon):
    d = daemon([fake_acp_agent()])
    with d.client() as c, d.client() as other:
        c.op("send", text="slow job")
        while (ev := c.event(5))["ev"] != "extend" and not (ev["ev"] == "append" and ev["role"] == "agent"):
            pass
        other.op("send", text="meanwhile")               # refused while busy
        t = time.monotonic()
        other.op("cancel")
        events = []
        while (ev := c.event(10)) and not (ev["ev"] == "busy" and ev["value"] is False):
            events.append(ev)
        assert time.monotonic() - t < 5
        entries = transcript(events)
        assert {"role": "error", "text": "busy"} in entries
        assert {"role": "info", "text": "cancelled"} in entries
        assert agent_texts(c.turn("after")) == ["echo 2: after"]  # same session goes on


def test_agent_crash_then_recovery(daemon):
    d = daemon([fake_acp_agent()])
    with d.client() as c:
        entries = transcript(c.turn("crash now"))
        assert entries[-1]["role"] == "error"
        assert agent_texts(c.turn("again")) == ["echo 1: again"]   # restarted, new session


def test_auth_required_message(daemon):
    d = daemon([fake_acp_agent()], env={"FAKE_ACP_AUTH": "1"})
    with d.client() as c:
        entries = transcript(c.turn("hi"))
    assert entries[-1]["role"] == "error" and "signed in" in entries[-1]["text"]


def test_missing_command(daemon):
    d = daemon([{"id": "ghost", "type": "acp", "command": "/nonexistent/agent --acp"}])
    with d.client() as c:
        entries = transcript(c.turn("hi"))
    assert entries[-1]["role"] == "error" and "not found" in entries[-1]["text"]
