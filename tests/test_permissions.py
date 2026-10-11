"""Access rules (permissions.json): validation, Claude Code settings, the daemon ops."""

import json
import os
import stat

import pytest

from oring import permissions


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORING_CONFIG", str(tmp_path / "config.toml"))
    return tmp_path


def test_defaults_when_missing(config_dir):
    p = permissions.load()
    assert p == permissions.DEFAULTS and p is not permissions.DEFAULTS
    assert "~/.ssh" in p["modes"]["auto"]["forbidden"] and p["workdir"] == "~/Projects"


@pytest.mark.parametrize("data, error", [
    ({"workdir": "relative/dir"}, "absolute"),
    ({"modes": {"auto": {"forbidden": "~/.ssh"}}}, "list"),
    ({"modes": {"manual": {"writable": [""]}}}, "non-empty"),
    ({"network": [3]}, "network"),
])
def test_invalid_rules_are_refused(config_dir, data, error):
    with pytest.raises(ValueError, match=error):
        permissions.save(data)
    assert not permissions.path().exists()


def test_save_normalizes_and_protects_the_file(config_dir):
    saved = permissions.save({"workdir": "~/work/", "modes": {"auto": {"writable": ["/data", "/data/"]}}})
    assert saved["workdir"] == "~/work" and saved["modes"]["auto"]["writable"] == ["/data"]
    assert saved["modes"]["manual"] == permissions.DEFAULTS["modes"]["manual"]   # untouched parts kept
    assert stat.S_IMODE(os.stat(permissions.path()).st_mode) == 0o600
    assert permissions.load() == saved


def test_broken_file_falls_back_to_the_strict_defaults(config_dir):
    permissions.path().write_text("{not json")
    assert permissions.load() == permissions.DEFAULTS


def test_claude_settings_per_mode(config_dir):
    perms = permissions.validate({"modes": {"manual": {"forbidden": []}}})
    manual, auto = (permissions.claude_settings(perms, m) for m in ("manual", "auto"))
    assert manual["sandbox"]["autoAllowBashIfSandboxed"] is False
    assert auto["sandbox"]["autoAllowBashIfSandboxed"] is True
    for s in (manual, auto):
        assert s["sandbox"]["allowUnsandboxedCommands"] is False
        assert f"/{config_dir}" in s["sandbox"]["filesystem"]["denyRead"]    # always, even if emptied
        assert f"Edit(/{config_dir}/**)" in s["permissions"]["deny"]
    assert "~/.ssh" not in manual["sandbox"]["filesystem"]["denyRead"]
    assert "~/.ssh" in auto["sandbox"]["filesystem"]["denyRead"]
    assert auto["sandbox"]["filesystem"]["allowWrite"] == ["~/Projects", "//tmp"]


def test_daemon_ops(daemon):
    d = daemon([{"id": "x", "name": "X", "type": "openai", "model": "m"}])
    with d.client() as c:
        c.op("access_rules")
        ev = c.event(5)
        assert ev["ev"] == "access_rules" and ev["data"]["workdir"] == "~/Projects"
        assert ev["path"] == str(d.config.parent / "permissions.json")
        c.op("set_access_rules", data={"network": ["example.org"]})
        assert c.event(5)["data"]["network"] == ["example.org"]
        c.op("set_access_rules", data={"workdir": "nope"})
        assert c.event(5)["ev"] == "access_rules_error"
    assert json.loads((d.config.parent / "permissions.json").read_text())["network"] == ["example.org"]
