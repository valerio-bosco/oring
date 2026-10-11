# AGENTS.md

Context for coding agents working on **oring** (Claude Code, Codex, OpenCode, Goose, Gemini, …).
Read this file first, then `docs/PLAN.md` (what to do) and `docs/MEMORY.md` (what we learned).

## What oring is

An agent panel for the serpantinum shell on Hyprland. A Quickshell side panel talks over a Unix
socket to a Python daemon, which drives one of several agents and gives every agent a desktop MCP
server. See `README.md` for the user-facing picture.

```
ui/shell.qml            panel (Quickshell/QML): reads serpantinum colors, talks JSON lines to the daemon
oring/daemon.py         daemon: conversation, clients, permissions, confirmation gate; protocol in its docstring
oring/backends/         claude_cli.py (claude -p), acp.py (Agent Client Protocol), openai.py (chat completions)
oring/client.py         sync socket client: `oring send/state/select/new/cancel`, tests, skills
oring/config.py         config.toml loading, paths (socket, state)
oring/labels.py         human-readable tool-call labels (it/en)
mcp/desktop_mcp.py      desktop MCP server (Hyprland 0.56 Lua dispatchers, serpantinum IPC); risky tools gated
mcp/permission_mcp.py   Claude Code permission prompt → panel
bin/oring               launcher (daemon, ui, toggle, ask, mcp, permission-mcp, control commands)
nix/                    package + home-manager module;  flake.nix: packages, checks, devShell
tests/                  pytest suite with fake agents (tests/fakes/), never touches the real desktop
scripts/check           the pipeline;  scripts/dev: isolated development instance
```

## Working loop

1. **Pick the next step** in `docs/PLAN.md` (first unchecked step of the current phase, unless the
   user asked for something else). Read its acceptance criteria.
2. **Read `docs/MEMORY.md`** for decisions and gotchas touching that area.
3. **Implement** with a test next to it (see the `oring-test-suite` skill). Keep the style of the
   surrounding code: English, small functions, comments only where the *why* is not obvious.
4. **Verify** with the test skills, in this order as relevant:
   `oring-test-suite` → `oring-live-test` → `oring-panel-check` → `oring-agent-check`.
5. **Record**: tick the step in `docs/PLAN.md` (add a one-line result), add decisions and
   gotchas to `docs/MEMORY.md`, append a dated line to its session log.
6. **Ship** with the `oring-release` skill (pipeline, commit). Never push: the maintainer does.

## Skills

Procedures live in `.claude/skills/<name>/SKILL.md` (also reachable as `.agents/skills`). Agents
without skill support: read the file and follow it.

| Skill | Use it when |
|---|---|
| `oring-test-suite` | after any code change; adding or fixing automated tests |
| `oring-live-test` | checking real behaviour end to end against an isolated daemon |
| `oring-panel-check` | any change to `ui/shell.qml` or to events the panel shows |
| `oring-agent-check` | adding/changing a backend, or verifying a real agent CLI (Codex, Goose, …) |
| `oring-release` | finishing a step: full pipeline, plan/memory update, commit |

## Commands

```sh
scripts/check              # syntax + tests (fast)
scripts/check full         # + nix flake check (build, sandboxed tests) + panel load test
scripts/dev start|stop|status|toggle|shot|oring <args>   # isolated dev instance
nix build                  # the package
```

## Rules

- **Never test on the installed instance** (systemd user services `oring`, `oring-ui`): use the
  test suite or `scripts/dev`, which has its own socket, state and config.
- **Risky desktop tools are real.** `clicca`, `scrivi_testo`, `premi_tasti`, `muovi_mouse`,
  `scorri`, `chiudi_finestra`, `scrivi_appunti` act on the user's session. In live tests answer
  their confirmations with **deny** unless the test is specifically about them, and then use only
  the clipboard. An agent once typed into a terminal during a test (see MEMORY G6).
- **Do not weaken the confirmation gate** (`_con_conferma`, `Daemon.gate`) or the permission relay;
  if a change touches them, the gate tests must still pass and you must say so in the commit.
- **Subscriptions**: Claude must run through the official `claude` CLI (`claude-cli` backend);
  do not build Claude support on the Agent SDK or ACP adapters with subscription logins (MEMORY D2).
- Keep the daemon protocol backward compatible or update `ui/shell.qml`, `oring/client.py` and the
  protocol docstring together.

## Commits

[Conventional Commits](https://www.conventionalcommits.org), enforced by `.githooks/commit-msg`
(enable once per clone: `git config core.hooksPath .githooks`).

```
<type>(<scope>): <summary>

<body: why the change, what was verified>

<footers: BREAKING CHANGE: …, Co-Authored-By: …>
```

- **type**: `feat` (user-visible feature), `fix` (bug), `docs`, `test`, `refactor` (no behaviour
  change), `perf`, `build` (Nix packaging, flake), `ci`, `chore` (maintenance), `style`, `revert`.
- **scope** (optional, one): `daemon`, `ui`, `mcp`, `acp`, `claude`, `openai`, `client`, `config`,
  `nix`, `tests`, `scripts`, `skills`, `docs`.
- **summary**: English, imperative ("add", not "added"), lowercase start, no final period,
  subject line ≤ 72 characters.
- **body**: after a blank line; explain the why and how it was verified (tests, live checks).
- **breaking changes** (socket protocol, config format, CLI): `!` after the scope and a
  `BREAKING CHANGE:` footer.
- One logical change per commit; a plan step may need several commits. Agents add a
  `Co-Authored-By:` footer naming themselves.

Examples: `feat(mcp): capture a window even when the screen is locked` ·
`fix(daemon): close client connections on shutdown` · `docs(plan): tick step 1.4` ·
`test(acp): cover agent crash and restart` · `build(nix): run the test suite in flake checks`
