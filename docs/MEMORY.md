# Project memory

Durable knowledge for whoever works on oring next: decisions (and why), gotchas that cost time,
the maintainer's environment, and a short session log. Add to it at the end of every step;
correct entries that turn out wrong instead of adding contradictions.

## Decisions

- **D1 — Separate Quickshell app, not a serpantinum module.** serpantinum has no plugin system;
  oring reads its theme file (`~/.local/state/serpantinum/qs_colors.json`) and mimics its style.
- **D2 — Claude through `claude -p`, not ACP/Agent SDK.** Since Feb 2026 Anthropic's consumer
  terms allow Pro/Max subscription logins only in Claude Code and claude.ai; third-party harnesses
  (OpenClaw etc.) were blocked on 2026-04-04. Running the official CLI keeps subscriptions usable.
- **D3 — Daemon + thin clients.** The panel is only a client; the conversation lives in the daemon
  so other clients (Telegram) can share it. Protocol: JSON lines over a Unix socket (daemon.py).
- **D4 — Confirmation gate inside the MCP server.** ACP agents call MCP tools directly, without
  asking the client, so permissions at the backend level cannot stop them. Risky tools call the
  daemon (`op: gate`) before acting; no answer = deny; only the active agent during a turn.
- **D5 — Claude permission relay** with `--permission-prompt-tool mcp__oring__approve`: tools not
  in `allowed_tools` are asked in the panel instead of being refused in headless mode.
- **D6 — Gemini dropped from the default config**: nixpkgs marks gemini-cli as replaced by
  Antigravity CLI for free and Google AI Pro/Ultra accounts; `agy` has no ACP (Phase 2).
- **D7 — Ollama with Vulkan** (`pkgs.ollama-vulkan`): the CUDA build is unfree, not in the binary
  cache, would compile for hours; GTX 1060 3 GB → models up to ~4B parameters.

## Gotchas

- **G1 — Quickshell `Socket` does not reconnect reliably.** Toggling `connected` false→true does
  not retry after a failure, and `connectedChanged` does not fire for the initial declarative
  `connected: true`. Fix in shell.qml: recreate the Socket through a `Loader` on each attempt and
  set `connected = true` in `Component.onCompleted`.
- **G2 — `show` is reserved by `quickshell ipc call`** (it prints the target instead of calling).
  IPC functions are `toggle/open/close/ask`.
- **G3 — FastMCP adds structured output** (`{"result": ...}`) for `-> str` tools; Claude Code then
  rejects the permission-prompt result as invalid. Use `@mcp.tool(structured_output=False)`.
- **G4 — asyncio `Server.wait_closed()` waits for connected clients** (Python ≥ 3.12): the daemon
  hung on SIGTERM while the panel was connected. Close client writers on shutdown (daemon.serve).
- **G5 — `pkill -f <pattern>` matches the shell running it** if the pattern is in the command line:
  it killed the test shell. Use `pgrep -f '[o]ring daemon'` style patterns.
- **G6 — Agents use risky desktop tools unprompted.** OpenCode (big-pickle), asked to "run echo
  in the terminal", focused a kitty window and tried to type into it (wtype failed) and wrote the
  clipboard. Origin of D4 and of the live-test rule "answer risky confirmations with deny".
- **G7 — OpenCode titles MCP tool calls `desktop_<tool>`**, Claude `mcp__desktop__<tool>`, the
  OpenAI backend `desktop__<tool>`: `AcpBackend._tool_title` and `labels.short_name` normalize them.
- **G8 — Nix sandbox sets `SSL_CERT_FILE` to a missing file**: httpx fails to create a client
  (`[Errno 2]`). The `checks.tests` derivation unsets it.
- **G9 — Background `cmd &` inside `[ ] && …` keeps the caller's pipe open**: use `setsid -f`.
- **G10 — Hyprland 0.56 uses Lua dispatchers**: `hyprctl dispatch 'hl.dsp.focus({...})'`; the old
  `hyprctl dispatch focuswindow …` fails with `')' expected`.
- **G12 — grim hangs when the monitor is off or the session is locked** (`hyprctl monitors`:
  `dpmsStatus: 0`, `solitaryBlockedBy: session lock`): no frames arrive. `scripts/dev shot` uses a
  timeout; visual checks need the user at the screen.
- **G11 — Screen coordinates**: grim and Hyprland use logical pixels (monitor size / scale);
  the maintainer's 3440×1440 at scale 1.0667 is 3225×1350 logical.

## Maintainer environment

- Repository: `github.com/valerio-bosco/oring` (public, branch `main`; the maintainer pushes).
- NixOS flake in `/etc/nixos` (git, remote on GitHub; the maintainer pushes, agents never do),
  home-manager as a NixOS module, user `voodoo`. oring is imported as flake input
  `oring = path:/home/voodoo/Projects/oring` in `/etc/nixos/oring.nix`; after changing oring:
  `sudo nix flake update oring --flake /etc/nixos`, build, then the maintainer runs
  `sudo nixos-rebuild switch --flake /etc/nixos#nixos`.
- Hyprland 0.56 (Lua config, SUPER+I → `oring toggle`), serpantinum 2.2.5, NVIDIA GTX 1060 3 GB,
  monitor 3440×1440 on HDMI-A-1, Italian UI.
- Agents installed for the service: claude (subscription), codex + codex-acp, opencode, goose-cli;
  Ollama service with `qwen3:4b`. OpenCode works without login (free model "big-pickle").
- Claude Code in the terminal has the desktop MCP registered as `desktop` (`oring mcp`).

## Session log

- 2026-10-11 — v0.1 built and deployed (698cf6e). Verified live: Claude, OpenCode (ACP), Ollama
  (qwen3:1.7b on CPU), gate allow/deny, Claude permission relay allow/deny, reconnection, shutdown.
- 2026-10-11 — Added project tooling: AGENTS.md, PLAN, MEMORY, control commands, pytest suite
  (36 tests, fake ACP agent / OpenAI server / claude CLI / desktop MCP), scripts/check, scripts/dev,
  test skills. Verified: suite green (also in the Nix sandbox), panel load test catches QML errors,
  `scripts/dev start/send/stop`; `scripts/dev shot` not visually verified (screen was off, G12).
