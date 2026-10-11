"""Daemon behaviour independent of the backend: state, persistence, agent switching, gate, shutdown."""

import json
import socket

from conftest import fake_acp_agent


def gate(d, **req):
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(d.socket))
        s.sendall((json.dumps({"op": "gate", **req}) + "\n").encode())
        return next(e for e in map(json.loads, s.makefile()) if e.get("ev") == "gate")["allow"]


def test_state(daemon):
    d = daemon([fake_acp_agent("a"), fake_acp_agent("b")], language="it")
    with d.client() as c:
        st = c.state()
    assert [a["id"] for a in st["agents"]] == ["a", "b"]
    assert st["active"] == "a" and st["busy"] is False and st["language"] == "it"


def test_history_survives_restart(daemon):
    d = daemon([fake_acp_agent("a"), fake_acp_agent("b")])
    with d.client() as c:
        c.op("select", agent="b")
        c.turn("remember me")
    d.stop()
    d.start()
    with d.client() as c:
        st = c.state()
    assert st["active"] == "b"
    assert st["history"][0] == {"role": "user", "text": "remember me"}


def test_select_resets_conversation(daemon):
    d = daemon([fake_acp_agent("a"), fake_acp_agent("b")])
    with d.client() as c:
        c.turn("hi")
        c.op("select", agent="b")
        st = c.state()
        assert st["active"] == "b" and st["history"] == []
        c.op("select", agent="nobody")
        assert c.state()["active"] == "b"


def test_events_reach_every_client(daemon):
    d = daemon([fake_acp_agent()])
    with d.client() as sender, d.client() as watcher:
        watcher.state()
        sender.turn("broadcast")
        seen = []
        while ev := watcher.event(1):
            seen.append(ev)
    assert {"ev": "append", "role": "user", "text": "broadcast"} in seen


def test_gate_only_for_active_agent_during_a_turn(daemon):
    d = daemon([fake_acp_agent("a"), fake_acp_agent("b")])
    assert gate(d, agent="a", tool="elenca_finestre", args={}) is False      # no turn running
    with d.client() as c:
        c.op("send", text="slow")
        while not ((ev := c.event(5))["ev"] == "busy" and ev["value"]):
            pass
        assert gate(d, agent="a", tool="elenca_finestre", args={}) is True   # not in confirm_tools
        assert gate(d, agent="b", tool="elenca_finestre", args={}) is False  # not the active agent
        c.op("cancel")


def test_shutdown_with_connected_clients(daemon):
    d = daemon([fake_acp_agent()])
    with d.client() as c:
        c.turn("start the agent process")
        assert d.stop() < 5
