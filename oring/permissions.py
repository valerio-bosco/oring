"""Where agents may write, what they may never touch: ~/.config/oring/permissions.json.

Only the user changes this file, from the panel (daemon op "set_access_rules"); it lives in the
oring config directory, which is always forbidden, so agents can neither read nor write it.

  forbidden        never read or written, whatever the mode; no permission can be asked for it
  writable         where commands may write besides the working directory
  (both per mode: "manual" and "auto")
  workdir          the agent's working directory, always writable (the same in both modes, so
                   switching mode keeps the conversation)
  outside_sandbox  command patterns that run outside the sandbox, still through the permission
                   checks: every entry is a way around the sandbox, keep them harmless
  network          domains commands in the sandbox may reach (Claude's web tools are not affected)

Claude Code enforces it (claude-cli backend): its sandbox runs every Bash command with the
forbidden paths hidden, writes limited to the writable paths and the network limited to the
listed domains, with no way out (allowUnsandboxedCommands false); deny rules cover its own tools.
"""

import copy
import json
import logging
import os
from pathlib import Path

from . import config

MODES = ("manual", "auto")
FORBIDDEN = [
    "~/.ssh", "~/.gnupg", "~/.password-store", "~/.local/share/keyrings", "~/.config/oring",
    "~/.netrc", "~/.git-credentials", "~/.config/gh", "~/.aws", "~/.kube", "~/.docker/config.json",
    "~/.mozilla", "~/.config/google-chrome", "~/.config/chromium", "~/.claude/.credentials.json",
    "~/.codex/auth.json", "~/.local/share/opencode/auth.json",
]
WRITABLE = ["/tmp"]
DEFAULTS = {
    "workdir": "~/Projects",
    "modes": {m: {"forbidden": FORBIDDEN, "writable": WRITABLE} for m in MODES},
    "outside_sandbox": ["systemctl --user status *", "systemctl --user is-active *", "journalctl --user *"],
    "network": ["github.com", "*.github.com", "*.githubusercontent.com", "cache.nixos.org",
                "channels.nixos.org", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org"],
}


def path() -> Path:
    return config.config_path().parent / "permissions.json"


def always_forbidden() -> list[str]:
    """The oring config directory (this file, the bot token): forbidden even if removed from the list."""
    return [str(config.config_path().parent)]


def _paths(value, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(p, str) and p.strip() for p in value):
        raise ValueError(f"permissions: '{field}' must be a list of non-empty strings")
    out = []
    for p in (v.strip().rstrip("/") or "/" for v in value):
        if not (p.startswith("/") or p == "~" or p.startswith("~/")):
            raise ValueError(f"permissions: '{p}' in '{field}' must be absolute or start with ~/")
        if p not in out:
            out.append(p)
    return out


def validate(data: dict) -> dict:
    """A complete, normalized copy of data (missing parts from DEFAULTS); ValueError if invalid."""
    out = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        raise ValueError("permissions: expected an object")
    modes = data.get("modes", {})
    for m in MODES:
        for field in ("forbidden", "writable"):
            if field in modes.get(m, {}):
                out["modes"][m][field] = _paths(modes[m][field], f"{m}.{field}")
    if "workdir" in data:
        out["workdir"] = _paths([data["workdir"]], "workdir")[0]
    for field in ("outside_sandbox", "network"):
        if field in data:
            value = data[field]
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                raise ValueError(f"permissions: '{field}' must be a list of non-empty strings")
            out[field] = [v.strip() for v in value]
    return out


def load() -> dict:
    try:
        return validate(json.loads(path().read_text()))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError) as e:
        # a broken file must not open things up: fall back to the defaults, which are strict
        logging.getLogger("oring").error("%s unreadable, using the defaults: %s", path(), e)
        return copy.deepcopy(DEFAULTS)


def save(data: dict) -> dict:
    data = validate(data)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return data


def _rule_path(p: str) -> str:
    """Claude Code rule syntax: ~/x stays, an absolute /x is written //x."""
    return p if p.startswith("~") else "/" + p


def _forbidden(perms: dict, mode: str) -> list[str]:
    return [_rule_path(p) for p in perms["modes"][mode]["forbidden"] + always_forbidden()]


def deny_rules(perms: dict, mode: str) -> list[str]:
    """Claude Code deny rules for the forbidden paths (the path itself and what is below it)."""
    return [f"{tool}({p}{suffix})" for p in _forbidden(perms, mode) for tool in ("Read", "Edit")
            for suffix in ("", "/**")]


def claude_settings(perms: dict, mode: str) -> dict:
    """Claude Code settings (--settings) enforcing perms in mode ("manual" or "auto")."""
    m = perms["modes"][mode]
    forbidden = _forbidden(perms, mode)
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,              # no silent fallback to an unsandboxed shell
            "autoAllowBashIfSandboxed": mode == "auto",
            "allowUnsandboxedCommands": False,      # no way out of the sandbox, not even by asking
            "excludedCommands": perms["outside_sandbox"],
            "filesystem": {"allowWrite": [_rule_path(p) for p in [perms["workdir"]] + m["writable"]],
                           "denyRead": forbidden, "denyWrite": forbidden},
            "network": {"allowedDomains": perms["network"]},
        },
        "permissions": {"deny": deny_rules(perms, mode)},
    }
