# oring

An agent panel for the [serpantinum](https://github.com/ilyamiro/serpantinum) shell on Hyprland:
a side panel in the shell's style (it follows the serpantinum theme live) where you can talk
to **any agent you want** (Claude Code, Gemini CLI, Codex, OpenCode, Goose, or a local model
through Ollama) and let it act on the desktop.

```
 SUPER+I ─▶ panel (Quickshell)  ◀──── Unix socket, JSON lines ────▶  oring daemon
                                                                       │
                     ┌─────────────────────────┬───────────────────────┼─────────────────────┐
                     ▼                         ▼                       ▼                     │
             claude-cli backend          acp backend             openai backend              │
             `claude -p` (stream-json)   Gemini, Codex,          Ollama, llama.cpp,          │
             → Claude subscription OK    OpenCode, Goose, …      OpenRouter, …               │
                     │                         │                       │ (oring runs         │
                     └──────────── desktop MCP server ◀────────────────┘  the tool loop)     │
                                   windows, workspaces, screenshots, OCR,                    │
                                   mouse, keyboard, clipboard, wallpapers, music             │
                                   risky actions ──── confirmation request ─────────────────┘
```

## Features

- **Any agent, switchable from the panel header.** One conversation at a time; switching agent
  starts a new one.
- **Claude with your subscription.** The `claude-cli` backend runs the official Claude Code CLI,
  where Pro/Max logins are allowed (they are not in third-party clients built on the Agent SDK).
- **ACP agents.** Anything speaking the [Agent Client Protocol](https://agentclientprotocol.com).
  The agent's own permission requests appear in the panel as buttons.
- **Local models.** Any OpenAI-compatible endpoint with function calling.
- **Desktop tools for every agent**, through a built-in MCP server written for Hyprland 0.56's Lua
  dispatchers and serpantinum's IPC.
- **Confirmation for risky actions, whatever the agent.** Clicking, typing, pressing keys,
  closing windows and writing the clipboard are checked by the MCP server itself, which asks
  the daemon. An agent cannot skip this by using its own permission settings, and tools only
  run for the active agent during a turn.
- **Safe when the screen is locked.** Mouse and keyboard tools refuse while the session is locked
  (they would type into the lock screen); screenshots and OCR of a specific window keep working,
  even on another workspace, so remote use can still see what apps show.
- **Persistent conversation.** It survives closing the panel and restarting the daemon (and
  Claude sessions are resumed).
- One daemon, many clients: the socket protocol is documented at the top of
  [`oring/daemon.py`](oring/daemon.py). A messaging gateway (Telegram, …) is the planned next client.

## Install (NixOS + home-manager)

```nix
# flake.nix
inputs.oring.url = "github:<you>/oring";

# home-manager configuration
imports = [ inputs.oring.homeManagerModules.default ];
programs.oring = {
  enable = true;
  extraPackages = with pkgs; [ gemini-cli codex-acp opencode goose-cli ];   # agents you want
  settings = {
    language = "en";
    agents = [
      { id = "claude"; name = "Claude"; type = "claude-cli"; }
      { id = "gemini"; name = "Gemini"; type = "acp"; command = "gemini --acp"; }
      { id = "local"; name = "Qwen (local)"; type = "openai"; model = "qwen3:4b"; }
    ];
  };
};
```

```lua
-- Hyprland (Lua config)
hl.bind("SUPER + I", hl.dsp.exec_cmd("oring toggle"))
```

This starts two user services, `oring` (daemon) and `oring-ui` (panel). See
[`config.example.toml`](config.example.toml) for every option. Without Nix: run `bin/oring daemon`
and `bin/oring ui` with Python 3.11+ (`mcp`, `httpx`) and Quickshell available.

Sign in to each agent once from a terminal (`claude`, `gemini`, `codex login`, …).

## Commands

| Command | |
|---|---|
| `oring toggle` / `open` / `close` | show or hide the panel |
| `oring ask "<text>"` | open the panel and send a message |
| `oring send "<text>"` | send from the terminal, print the answer |
| `oring mcp` | run the desktop MCP server for other clients (`claude mcp add desktop -- oring mcp`) |

Panel environment variables: `ORING_WIDTH`, `ORING_MARGIN_TOP`, `ORING_FONT`.

## Development

Start with [`AGENTS.md`](AGENTS.md): it is written for coding agents and humans alike. The plan is
in [`docs/PLAN.md`](docs/PLAN.md), decisions and gotchas in [`docs/MEMORY.md`](docs/MEMORY.md),
test procedures in [`.claude/skills/`](.claude/skills).

```sh
scripts/check          # syntax + test suite (fake agents, no desktop access)
scripts/check full     # + nix flake check + panel load test
scripts/dev start      # isolated instance from the working tree; scripts/dev toggle / oring / stop
```

## Status

Early (0.1). The desktop MCP server's tools and descriptions are currently in Italian.
Tested with Claude Code, OpenCode (ACP) and Ollama. Antigravity CLI, which replaced Gemini CLI
for Google accounts, has no ACP mode yet: it needs its own backend (planned).

## License

MIT
