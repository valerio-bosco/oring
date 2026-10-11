---
name: oring-live-test
description: End-to-end test of oring against a real, isolated development daemon (scripts/dev) with real agents and the real desktop MCP server. Use to confirm a change works for real after the automated tests pass, or to reproduce a bug the user reported.
---

# Live test with the dev instance

`scripts/dev` runs the daemon and panel **from the working tree** with their own socket, state and
config under `/tmp/oring-dev-$USER`. The installed oring (systemd user services `oring`,
`oring-ui`) keeps running untouched. Must run as the desktop user inside the graphical session
(from Claude Code running as root: `runuser -u voodoo -- env XDG_RUNTIME_DIR=/run/user/1000
WAYLAND_DISPLAY=wayland-1 HYPRLAND_INSTANCE_SIGNATURE=… DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus scripts/dev …`).

## Safety first

- The desktop MCP server here is the **real** one. Risky tools (`clicca`, `scrivi_testo`,
  `premi_tasti`, `muovi_mouse`, `scorri`, `chiudi_finestra`, `scrivi_appunti`) act on the user's
  screen. Use `--auto deny`, or answer `deny`, unless the test is about them; when it is, test with
  `scrivi_appunti` only and tell the user their clipboard changed.
- Never `--auto allow` a prompt that could lead to clicking, typing or shell commands.
- Use prompts that are harmless if the agent misunderstands them.

## Steps

1. `scripts/dev start [config.toml]` — default config: OpenCode (free, ACP) + Claude. For other
   agents write a config (same format as `config.example.toml`) and pass it.
2. Drive it:
   ```sh
   scripts/dev oring state                       # agents, active, history
   scripts/dev oring select claude
   scripts/dev oring send --auto deny "Quante finestre ho aperte?"
   scripts/dev oring new
   ```
   `send` prints tool calls (`[tool]`), permission requests (`[permission]`), answers and errors.
3. Scenarios to cover, as relevant to the change:
   - plain answer; a read-only desktop tool (`elenca_finestre`, `screenshot`); memory across turns;
   - a risky tool → `[permission]` appears → denied → nothing happened;
   - Claude: a non-allowed tool (Write to `/tmp/...`) → permission relay allow and deny;
   - `cancel` during a long answer; daemon restart (`scripts/dev stop && scripts/dev start`) keeps history.
4. Check `tail -50 /tmp/oring-dev-$USER/daemon.log` for tracebacks even if the output looked fine.
5. `scripts/dev stop` — always, also after failures. `scripts/dev status` must show nothing running.

## Done when

Every scenario relevant to the change behaved as expected, the daemon log has no unexpected
errors, the dev instance is stopped. Write anything surprising to `docs/MEMORY.md` (Gotchas).
