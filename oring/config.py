"""Configuration: ~/.config/oring/config.toml (or $ORING_CONFIG)."""

import os
import shlex
import tomllib
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """\
You are the desktop assistant integrated into the user's Linux desktop (Hyprland with the
serpantinum shell). The user talks to you from a narrow side panel: answer concisely, with simple
markdown, in the user's language.
You can act on the desktop with the tools of the "desktop" MCP server (windows, workspaces,
launching apps, screenshots, reading text on screen, wallpapers, music, notifications, clipboard,
mouse and keyboard). When asked to do something, do it directly and confirm in one sentence.
Rules: close windows only when explicitly asked. Use mouse and keyboard only when the request
requires it, preferring the dedicated window and workspace tools; locate the target with a
screenshot or text search before clicking, and verify the result afterwards. Never type
passwords or sensitive data, and never confirm purchases, payments, sending messages or
deletions unless the user explicitly asked for it.
"""

BACKEND_TYPES = {"claude-cli", "acp", "openai"}

# Desktop tools that need the user's confirmation in the panel, whatever the agent
# (overridable globally or per agent with confirm_tools)
DEFAULT_CONFIRM_TOOLS = ["clicca", "scorri", "muovi_mouse", "scrivi_testo", "premi_tasti",
                         "chiudi_finestra", "scrivi_appunti"]


def config_path() -> Path:
    if os.environ.get("ORING_CONFIG"):
        return Path(os.environ["ORING_CONFIG"])
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "oring" / "config.toml"


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    d = base / "oring"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runtime_dir() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    d = base / "oring"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def socket_path() -> Path:
    return runtime_dir() / "daemon.sock"


def load() -> dict:
    path = config_path()
    cfg = tomllib.loads(path.read_text()) if path.exists() else {}

    cfg.setdefault("language", (os.environ.get("LANG") or "en")[:2])
    cfg.setdefault("system_prompt", DEFAULT_SYSTEM_PROMPT)
    cfg.setdefault("desktop_mcp", True)
    cfg.setdefault("mcp_servers", [])
    cfg.setdefault("confirm_tools", DEFAULT_CONFIRM_TOOLS)
    agents = cfg.setdefault("agents", [])
    if not agents:
        agents.append({"id": "claude", "name": "Claude", "type": "claude-cli"})

    seen = set()
    for a in agents:
        if "id" not in a:
            raise ValueError("every [[agents]] entry needs an 'id'")
        if a["id"] in seen:
            raise ValueError(f"duplicate agent id '{a['id']}'")
        seen.add(a["id"])
        if a.get("type") not in BACKEND_TYPES:
            raise ValueError(f"agent '{a['id']}': type must be one of {sorted(BACKEND_TYPES)}")
        a.setdefault("name", a["id"])

    if cfg.get("default_agent") not in seen:
        cfg["default_agent"] = agents[0]["id"]
    return cfg


def mcp_servers(cfg: dict) -> list[dict]:
    """MCP servers given to every agent: [{name, command, args, env}]."""
    servers = []
    if cfg["desktop_mcp"]:
        command = shlex.split(os.environ.get("ORING_DESKTOP_MCP", ""))
        if command:
            servers.append({"name": "desktop", "command": command[0], "args": command[1:], "env": {}})
    for s in cfg["mcp_servers"]:
        servers.append({"name": s["name"], "command": s["command"],
                        "args": list(s.get("args", [])), "env": dict(s.get("env", {}))})
    return servers
