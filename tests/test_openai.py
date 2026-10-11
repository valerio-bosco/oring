"""OpenAI-compatible backend: streaming, tool loop through MCP, confirmation gate."""

import sys

import pytest

from conftest import FAKES
from oring.client import transcript
from openai_server import FakeOpenAI


@pytest.fixture
def fake_api():
    api = FakeOpenAI()
    yield api
    api.close()


def start(daemon, tmp_path, api, **agent):
    log = tmp_path / "mcp.log"
    log.write_text("")
    d = daemon([{"id": "local", "type": "openai", "model": "m", "base_url": api.url, **agent}],
               desktop_mcp=True, system_prompt="SYS",
               env={"ORING_DESKTOP_MCP": f"{sys.executable} {FAKES / 'desktop_mcp.py'}",
                    "FAKE_MCP_LOG": str(log)})
    return d, log


def agent_texts(events):
    return [e["text"] for e in transcript(events) if e["role"] == "agent"]


def test_stream_and_history(daemon, tmp_path, fake_api):
    d, _ = start(daemon, tmp_path, fake_api)
    with d.client() as c:
        assert agent_texts(c.turn("world")) == ["hello world"]
        c.turn("again")
    msgs = fake_api.requests[-1]["messages"]
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert {t["function"]["name"] for t in fake_api.requests[0]["tools"]} == {
        "desktop__elenca_finestre", "desktop__scrivi_appunti"}


def test_tool_loop(daemon, tmp_path, fake_api):
    d, _ = start(daemon, tmp_path, fake_api)
    with d.client() as c:
        events = c.turn("use a tool")
    assert {"role": "tool", "text": "Looking at open windows"} in transcript(events)
    assert agent_texts(events) == ["tool said: 3 windows"]


@pytest.mark.parametrize("answer, written", [("allow", True), ("deny", False)])
def test_risky_tool_goes_through_the_gate(daemon, tmp_path, fake_api, answer, written):
    d, log = start(daemon, tmp_path, fake_api)
    with d.client() as c:
        events = c.turn("clip it", auto=answer)
    asked = [e for e in events if e["ev"] == "permission"]
    assert asked and asked[0]["title"] == "Copying to clipboard: secret"
    assert ("secret" in log.read_text()) is written


def test_backend_ask_policy(daemon, tmp_path, fake_api):
    d, _ = start(daemon, tmp_path, fake_api, permissions="ask")
    with d.client() as c:
        events = c.turn("use a tool", auto="deny")
    assert any(e["ev"] == "permission" for e in events)
    assert "error: the user denied this action" in fake_api.requests[-1]["messages"][-1]["content"]


def test_http_error(daemon, tmp_path):
    d = daemon([{"id": "local", "type": "openai", "model": "m", "base_url": "http://127.0.0.1:9/v1"}])
    with d.client() as c:
        entries = transcript(c.turn("hi"))
    assert entries[-1]["role"] == "error"
