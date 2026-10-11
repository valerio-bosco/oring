"""Claude Code through its own CLI in headless mode (`claude -p`, stream-json output).

Running the official CLI means the user's Claude subscription can be used: subscription logins
are allowed in Claude Code itself, not in third-party clients built on the Agent SDK.

Options:  command (default "claude"), allowed_tools (default: the MCP servers + Read),
          args (extra CLI arguments, e.g. ["--model", "sonnet"]), strict_mcp (default true:
          only the MCP servers given by oring, not the user's own Claude Code ones),
          permissions: "ask" (default: other tools are asked in the panel) | "deny" (refused).
"""

import asyncio
import json
import os

from . import Backend, StderrTail, which, with_paths
from .. import config
from ..labels import describe


class ClaudeCliBackend(Backend):
    def __init__(self, conf, daemon):
        super().__init__(conf, daemon)
        self.session_id: str | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.mcp_file = config.runtime_dir() / f"mcp-{conf['id']}.json"

    def session_state(self):
        return self.session_id

    def restore(self, state):
        self.session_id = state if isinstance(state, str) else None

    async def reset(self):
        self.session_id = None

    def _arguments(self, text: str) -> list[str]:
        servers = self.mcp_servers()
        allowed = self.conf.get("allowed_tools", [f"mcp__{s['name']}" for s in servers] + ["Read"])
        ask = self.conf.get("permissions", "ask") == "ask" and os.environ.get("ORING_BIN")
        if ask:
            # tools that are not pre-allowed are asked in the panel instead of being refused
            servers = servers + [{"name": "oring", "command": os.environ["ORING_BIN"], "args": ["permission-mcp"],
                                  "env": {"ORING_SOCKET": str(config.socket_path()), "ORING_AGENT": self.conf["id"]}}]
        self.mcp_file.write_text(json.dumps({"mcpServers": {
            s["name"]: {"command": s["command"], "args": s["args"], "env": s["env"]} for s in servers}}))
        args = [*self.command("claude"), "-p", text, "--output-format", "stream-json", "--verbose",
                "--mcp-config", str(self.mcp_file)]
        if self.conf.get("strict_mcp", True):
            args.append("--strict-mcp-config")
        if allowed:
            args += ["--allowedTools", *allowed]
        if ask:
            args += ["--permission-prompt-tool", "mcp__oring__approve"]
        if self.system_prompt:
            args += ["--append-system-prompt", self.system_prompt]
        args += self.conf.get("args", [])
        if self.session_id:
            args += ["--resume", self.session_id]
        return args

    async def prompt(self, text: str, images: list[str] = ()):
        args = self._arguments(with_paths(text, images))      # Claude Code reads images with Read
        if not which(args[0]):
            raise RuntimeError(f"'{args[0]}' not found: is Claude Code installed?")
        # stdin closed: otherwise Claude Code waits 3 seconds for input that never comes
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=self.cwd, env={"MCP_TOOL_TIMEOUT": "900000", **self.env()},
            limit=16 * 1024 * 1024)
        stderr = StderrTail(self.proc.stderr)
        result_seen = False
        try:
            while line := await self.proc.stdout.readline():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                result_seen |= await self._event(ev)
            code = await self.proc.wait()
            await stderr.task
            if code not in (0, -15, 143) and not result_seen:
                raise RuntimeError(stderr.text() or f"Claude Code exited with code {code}")
        except asyncio.CancelledError:
            await self.cancel()
            raise
        finally:
            self.proc = None

    async def _event(self, ev: dict) -> bool:
        if ev.get("session_id"):
            self.session_id = ev["session_id"]
        lang = self.daemon.language
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    await self.daemon.add("agent", block["text"].strip())
                elif block.get("type") == "tool_use":
                    title = describe(block.get("name", "?"), block.get("input"), lang)
                    if title:
                        await self.daemon.add("tool", title)
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                await self.daemon.add("error", ev.get("result") or "Claude Code error")
            denied = sorted({d.get("tool_name", "?") for d in ev.get("permission_denials") or []})
            if denied:
                await self.daemon.add("error", "not allowed: " + ", ".join(denied))
            return True
        return False

    async def cancel(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
