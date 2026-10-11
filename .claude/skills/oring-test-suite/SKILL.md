---
name: oring-test-suite
description: Run and extend oring's automated test suite (pytest with fake agents) and the scripts/check pipeline. Use after any code change in oring, when a test fails, or when adding a feature or fixing a bug that needs a regression test.
---

# oring test suite

The suite starts real daemon processes in isolated temp directories and drives them through the
socket with fake agents. It never touches the user's session, the installed oring or the desktop.

## Run

```sh
scripts/check          # syntax + all tests, ~30 s — run after every change
scripts/check full     # + nix flake check (package build, tests in the sandbox) + panel load test
```

Single tests while iterating (Python with mcp/httpx/pytest comes from the flake):

```sh
py=$(nix build --no-link --print-out-paths .#dev-python)/bin/python
$py -m pytest -q -p no:cacheprovider tests/test_acp.py -k permission
$py -m pytest -q -p no:cacheprovider tests -x --lf      # stop at first failure, rerun last failures
```

A failing daemon test leaves its log in the pytest temp dir: the assertion message of
`DaemonProcess` includes it when the daemon does not start or stop. Set `ORING_LOG=DEBUG` (already
the default in tests) and read `<tmp>/daemon.log`.

## Where things are

| File | Covers |
|---|---|
| `tests/test_units.py` | config loading/validation, labels, transcript helper |
| `tests/test_daemon.py` | state, persistence across restart, agent switch, broadcast, gate rules, shutdown |
| `tests/test_acp.py` | ACP backend: streaming, memory, sessions, system prompt, permissions, cancel, crash, auth |
| `tests/test_openai.py` | OpenAI backend: streaming, history, MCP tool loop, gate allow/deny, HTTP errors |
| `tests/test_claude_cli.py` | claude-cli: arguments, `--resume`, permission relay, denials, failures |
| `tests/conftest.py` | `daemon` fixture (`DaemonProcess`), config writer, `fake_acp_agent()` |
| `tests/fakes/` | `acp_agent.py` (scripted by prompt keywords), `openai_server.py`, `desktop_mcp.py` (real gate) |

## Writing a test

- Use the `daemon` fixture: `d = daemon([agent dicts], **top_level_config, env={...})`, then
  `with d.client() as c: events = c.turn("text", auto="allow"|"deny"|None)`.
  `transcript(events)` gives `[{role, text}]`; roles: user, agent, tool, info, error.
- Need new agent behaviour? Add a keyword to `tests/fakes/acp_agent.py` (document it in its
  docstring) or a scenario to `openai_server.py`. Keep fakes deterministic and fast.
- Backends that run a CLI: write a fake executable into `tmp_path` (see `test_claude_cli.py`).
- Bug fix → first a failing test reproducing it, then the fix.
- Tests must not depend on the network, on installed agents, on Wayland or on the user's HOME.

## Done when

`scripts/check` is green; for changes to packaging, flake or the panel, `scripts/check full` too.
Report the counts (e.g. "36 passed") in your summary.
