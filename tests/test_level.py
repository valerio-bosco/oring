"""Access level (oring/level.py): reading the file, the root helper, and what the daemon allows."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import fake_acp_agent

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin" / "oring-level-helper"
sys.path.insert(0, str(ROOT))
from oring import level  # noqa: E402
from oring.client import transcript  # noqa: E402


@pytest.fixture
def level_file(tmp_path, monkeypatch):
    f = tmp_path / "level"
    monkeypatch.setenv("ORING_LEVEL_FILE", str(f))
    monkeypatch.setenv("ORING_LEVEL_ANY_OWNER", "1")
    return f


@pytest.mark.parametrize("content, expected", [
    (None, "sandbox"),                                              # no file
    ({"level": "full", "until": None}, "full"),
    ({"level": "full", "until": time.time() + 3600}, "full"),
    ({"level": "full", "until": time.time() - 1}, "sandbox"),       # expired
    ({"level": "root"}, "sandbox"),                                 # unknown level
    ({"level": "full", "until": "tomorrow"}, "sandbox"),
    ("not json", "sandbox"),
])
def test_read(level_file, content, expected):
    if content is not None:
        level_file.write_text(content if isinstance(content, str) else json.dumps(content))
    assert level.current() == expected


def test_file_not_owned_by_root_is_ignored(level_file, monkeypatch):
    # the tests run as a normal user: without the test override this file cannot be trusted
    level_file.write_text(json.dumps({"level": "full", "until": None}))
    monkeypatch.delenv("ORING_LEVEL_ANY_OWNER")
    assert level.current() == "sandbox"


def helper(level_file, *args, env=None):
    return subprocess.run(["bash", str(HELPER), *args], capture_output=True, text=True,
                          env={**os.environ, "ORING_LEVEL_FILE": str(level_file), **(env or {})})


def test_helper_writes_the_level(level_file):
    assert helper(level_file, "full").returncode == 0
    data = json.loads(level_file.read_text())
    assert data["level"] == "full" and data["until"] is None and data["set_at"] > 0
    assert oct(level_file.stat().st_mode & 0o777) == "0o644"
    assert helper(level_file, "full", "2h").returncode == 0
    assert abs(json.loads(level_file.read_text())["until"] - (time.time() + 7200)) < 10
    assert helper(level_file, "sandbox").returncode == 0
    assert json.loads(level_file.read_text())["level"] == "sandbox"


@pytest.mark.parametrize("args", [(), ("root",), ("full", "5x"), ("full", "0h"), ("full", "2h", "x"),
                                  ("sandbox", "2h"), ("full", "1h; rm -rf /"), ("full", "999999h")])
def test_helper_rejects_bad_arguments(level_file, args):
    level_file.write_text("untouched")
    assert helper(level_file, *args).returncode == 2
    assert level_file.read_text() == "untouched"


def test_helper_records_who_asked(level_file):
    helper(level_file, "full", env={"PKEXEC_UID": str(os.getuid())})
    assert json.loads(level_file.read_text())["set_by"] == subprocess.check_output(["id", "-nu"], text=True).strip()
    helper(level_file, "full", env={"PKEXEC_UID": '0"; echo pwned'})
    assert json.loads(level_file.read_text())["set_by"] == "unknown"


def hello(c, remote):
    c.op("hello", remote=remote)
    while (ev := c.event(5))["ev"] != "state":
        pass
    return ev


def test_state_reports_the_level(daemon):
    d = daemon([fake_acp_agent("a")])
    with d.client() as c:
        st = hello(c, False)
        assert st["level"] == "sandbox" and st["agents"][0]["sandboxed"] is False
        assert st["agents"][0]["remote"] is False                  # unsandboxed: local only
        d.set_level("full")
        while (ev := c.event(5)) and ev["ev"] != "state":          # the daemon notices the change
            pass
        assert ev["level"] == "full" and ev["agents"][0]["remote"] is True


def test_chats_cannot_use_unsandboxed_agents_at_sandbox_level(daemon):
    d = daemon([fake_acp_agent("a"), fake_acp_agent("b")])
    with d.client() as chat, d.client() as panel:
        hello(chat, True)
        chat.op("select", agent="b")
        assert chat.event(5) == {"ev": "refused", "op": "select", "reason": "local_only", "agent": "Fake"}
        chat.op("send", text="hello")
        assert chat.event(5)["ev"] == "refused"
        assert panel.state()["history"] == [] and panel.state()["active"] == "a"
        entries = transcript(panel.turn("from the desk"))           # the panel may
        assert [e["role"] for e in entries].count("agent") == 1


def test_chats_may_use_every_agent_at_full_level(daemon):
    d = daemon([fake_acp_agent("a"), fake_acp_agent("b")], level="full")
    with d.client() as chat:
        hello(chat, True)
        chat.op("select", agent="b")
        assert chat.state()["active"] == "b"
        events = chat.turn("hello")
    assert any(e.get("text", "").startswith("echo 1") for e in events if e["ev"] == "append")


def test_going_back_to_sandbox_applies_at_once(daemon):
    d = daemon([fake_acp_agent("a")], level="full")
    with d.client() as chat:
        hello(chat, True)
        d.set_level(None)                                          # no wait for the 2 s watcher
        chat.op("send", text="hello")
        assert chat.event(5)["ev"] == "refused"
