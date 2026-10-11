"""Pure functions: configuration, labels, client helpers."""

import pytest

from oring import config, labels
from oring.client import transcript


def test_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ORING_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    cfg = config.load()
    assert cfg["language"] == "it"
    assert [a["id"] for a in cfg["agents"]] == ["claude"]
    assert cfg["default_agent"] == "claude"
    assert "clicca" in cfg["confirm_tools"]


@pytest.mark.parametrize("body, error", [
    ('[[agents]]\nname = "x"\ntype = "acp"\n', "needs an 'id'"),
    ('[[agents]]\nid = "a"\ntype = "nope"\n', "type must be"),
    ('[[agents]]\nid = "a"\ntype = "acp"\n[[agents]]\nid = "a"\ntype = "acp"\n', "duplicate"),
])
def test_config_errors(tmp_path, monkeypatch, body, error):
    path = tmp_path / "c.toml"
    path.write_text(body)
    monkeypatch.setenv("ORING_CONFIG", str(path))
    with pytest.raises(ValueError, match=error):
        config.load()


def test_config_unknown_default_agent_falls_back(tmp_path, monkeypatch):
    path = tmp_path / "c.toml"
    path.write_text('default_agent = "ghost"\n[[agents]]\nid = "a"\ntype = "acp"\ncommand = "x"\n')
    monkeypatch.setenv("ORING_CONFIG", str(path))
    assert config.load()["default_agent"] == "a"


def test_mcp_servers(monkeypatch):
    monkeypatch.setenv("ORING_DESKTOP_MCP", "/bin/oring mcp")
    cfg = {"desktop_mcp": True, "mcp_servers": [{"name": "gh", "command": "gh-mcp", "args": ["stdio"]}]}
    servers = config.mcp_servers(cfg)
    assert servers[0] == {"name": "desktop", "command": "/bin/oring", "args": ["mcp"], "env": {}}
    assert servers[1]["name"] == "gh" and servers[1]["args"] == ["stdio"]
    assert config.mcp_servers({**cfg, "desktop_mcp": False})[0]["name"] == "gh"


@pytest.mark.parametrize("name, short", [
    ("mcp__desktop__clicca", "clicca"), ("desktop__clicca", "clicca"), ("Read", "Read")])
def test_short_name(name, short):
    assert labels.short_name(name) == short


def test_describe():
    assert labels.describe("mcp__desktop__clicca", {"x": 10, "y": 20}, "it") == "Clic: 10, 20"
    assert labels.describe("ToolSearch", {}, "en") is None
    # file and command tools show only the relevant argument, not the content
    assert labels.describe("Write", {"file_path": "/tmp/a", "content": "x" * 500}, "en") == "Writing a file: /tmp/a"
    long = labels.describe("Bash", {"command": "y" * 500}, "en")
    assert len(long) <= 160 and long.endswith("…")
    assert labels.describe("unknown_tool", {}, "xx") == "unknown_tool"


def test_transcript():
    events = [{"ev": "append", "role": "user", "text": "hi"}, {"ev": "append", "role": "agent", "text": "a"},
              {"ev": "extend", "text": "b"}, {"ev": "busy", "value": False}]
    assert transcript(events) == [{"role": "user", "text": "hi"}, {"role": "agent", "text": "ab"}]
