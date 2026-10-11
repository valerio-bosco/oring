---
name: oring-agent-check
description: Verify a real agent integration in oring (an ACP agent like Codex or Goose, a local model through Ollama, or a new backend such as Antigravity) against the protocol and safety requirements. Use when adding or changing a backend, or when PLAN asks to verify an agent.
---

# Agent check

## 1. Prerequisites

- The agent CLI is installed (for the maintainer: in `programs.oring.extraPackages`, so it is on the
  service PATH; `scripts/dev` inherits it) and **signed in by the user**. Logins are the user's job:
  if a turn ends with "needs to be signed in", stop and ask them to run the login command
  (`codex login`, `goose configure`, `opencode auth login`, `agy` …).
- Find the ACP entry point: `<cli> --help | grep -i acp` (known: `codex-acp`, `goose acp`,
  `opencode acp`, `gemini --acp`).

## 2. Protocol probe (ACP agents, no daemon involved)

Talk to the agent directly to see what it supports before blaming oring:

```sh
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":false,"writeTextFile":false},"terminal":false}}}' \
 '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}' \
 | timeout 20 <agent command>
```

Note `authMethods`, `agentCapabilities` (loadSession, mcpCapabilities, promptCapabilities) and
whether `session/new` succeeds.

## 3. Through oring (dev instance)

Write a config with the agent, `scripts/dev start <config>`, select it, then check:

| Check | How | Expect |
|---|---|---|
| answer | `send "Rispondi solo: ok"` | `[agent] ok`, no error |
| streaming/labels | `send "Quante finestre ho aperte?"` | `[tool] Guardo le finestre aperte` (label, not a raw name) |
| memory | ask about the previous message | correct |
| MCP env | daemon log / agent behaviour | desktop tools visible to the agent |
| agent permissions | ask for a shell command on `/tmp` | `[permission]` with the agent's options (ACP) |
| gate | ask to copy "test" to the clipboard, `--auto deny` | `[permission] Copio negli appunti…`, clipboard unchanged |
| cancel | long request, then `scripts/dev oring cancel` | turn ends with "cancelled" within a few s |
| new session | `scripts/dev oring new`, ask about earlier | does not remember |

Local models (openai backend): also check `ollama ps` (GPU vs CPU) and time a tool-using turn.

## 4. Record

- `docs/MEMORY.md`: how the agent titles tools, quirks, auth, speed, anything that needed a fix
  (as a Gotcha), and the session-log line.
- New backend: add a fake executable/agent and tests (see `oring-test-suite`), a commented example
  to `config.example.toml`, a row to the README.

## Done when

All rows pass (or failures are explained in MEMORY with a plan step to fix them), the dev instance
is stopped, and the PLAN step is ticked with the result.
