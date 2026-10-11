# Development plan

How to use: work through the current phase top to bottom. Each step has acceptance criteria
("done when") and the skills that verify it. When a step is done, tick it and add a short
`→ result` line (date, commit). New ideas go to the backlog, not into the current phase.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped (say why)

## Phase 0 — Core (v0.1) ✔

- [x] Daemon + socket protocol, persistent conversation → 2026-10-11, 698cf6e
- [x] Backends: claude-cli, ACP, OpenAI-compatible → 2026-10-11, 698cf6e
- [x] Panel in serpantinum style: agent switcher, permission cards, reconnection → 2026-10-11, 698cf6e
- [x] Desktop MCP server with confirmation gate for risky tools → 2026-10-11, 698cf6e
- [x] Claude Code permission prompts relayed to the panel → 2026-10-11, 698cf6e
- [x] Nix package + home-manager module, deployed on the maintainer's machine → 2026-10-11
- [x] Project tooling: AGENTS.md, plan, memory, test suite, pipeline, dev instance, test skills

## Phase 1 — Verified agents and safety

- [ ] **1.1 Verify Codex (codex-acp)** with a real login.
  Done when: a turn answers, a desktop tool is used, a permission request appears and both
  answers work; findings in MEMORY. Skills: `oring-agent-check`.
- [ ] **1.2 Verify Goose (`goose acp`)**, same criteria as 1.1.
- [ ] **1.3 Verify the local model on the GPU**: Ollama (Vulkan) runs `qwen3:4b` on the GTX 1060,
  tool calls work. Done when: `ollama ps` shows GPU offload, a tool-using turn finishes in < 30 s;
  record speed in MEMORY. Skills: `oring-agent-check`.
- [ ] **1.4 Locked-screen protection**: while the session is locked, mouse/keyboard/screenshot/OCR
  tools refuse with a clear message. First find a reliable lock signal for serpantinum's
  WlSessionLock (`loginctl` LockedHint is not set). Done when: tested by locking for a few seconds
  in a live test; unit test for the refusal path. Skills: `oring-test-suite`, `oring-live-test`.
- [ ] **1.5 Claude streaming**: use `--include-partial-messages` so Claude's text streams like the
  other backends. Done when: panel shows text growing; tests updated with a fake partial stream.

## Phase 2 — Gemini through Antigravity

- [ ] **2.1 Study `agy --print --output-format stream-json`**: event format, sessions
  (`--conversation`/`--continue`), permissions, MCP config (`agy mcp`). Needs the maintainer's
  Google login. Done when: format documented in MEMORY with a captured sample.
- [ ] **2.2 `antigravity` backend** (`oring/backends/antigravity.py`) modelled on claude_cli.
  Done when: fake-agy tests pass; real turn with desktop tool works. Skills: `oring-agent-check`.

## Phase 3 — Remote use

- [ ] **3.1 Telegram gateway** as a daemon client (`oring/gateways/telegram.py` or a separate
  service): allowlisted chat id, permission requests as inline buttons, `/new`, `/agent`, `/stop`.
  Done when: tested with a test bot; unauthorized chats ignored (test).
- [ ] **3.2 Suspend policy** decided with the maintainer (no auto-suspend vs Wake-on-LAN) and
  documented; implemented in the maintainer's NixOS config, not in oring.

## Phase 4 — Publish

- [ ] **4.1 Desktop MCP in English**: tool names, descriptions, messages; labels updated; keep
  Italian labels in `labels.py`. Done when: tests pass, live check of a few tools.
- [ ] **4.2 Remove machine-specific bits**: screen size in descriptions (read it from hyprctl),
  wallpaper dir (config option), OCR languages (config).
- [ ] **4.3 CI**: GitHub Actions running `nix flake check`.
- [ ] **4.4 README screenshots**, `CHANGELOG.md`, tag v0.2, switch the maintainer's flake input to
  `github:valerio-bosco/oring` (the GitHub repository already exists).

## Backlog

- Conversation list (keep past conversations, reopen them)
- Images: show screenshots in the panel; send pasted images to agents that accept them
- Per-agent model picker (claude `--model`, ACP session modes/models)
- Code blocks with copy button in agent messages
- Panel width/position settings in config instead of env vars
