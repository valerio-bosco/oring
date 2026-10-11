"""Any agent speaking the Agent Client Protocol (https://agentclientprotocol.com): JSON-RPC 2.0 over
the agent's stdin/stdout. Gemini CLI, Codex (codex-acp), OpenCode, Goose, Qwen Code, ...

Options:  command (required, e.g. "gemini --acp", "codex-acp", "opencode acp", "goose acp"),
          permissions: "ask" (default: the panel shows the agent's options) | "allow" | "deny".
The agent process stays alive between turns; "new conversation" opens a new ACP session.
"""

import asyncio
import itertools
import json
import logging

from . import Backend, StderrTail, which
from ..labels import describe

log = logging.getLogger("oring.acp")

PROTOCOL_VERSION = 1


class AcpError(RuntimeError):
    def __init__(self, error: dict):
        self.code = error.get("code")
        self.data = error.get("data")
        super().__init__(error.get("message") or f"ACP error {self.code}")


class AcpBackend(Backend):
    def __init__(self, conf, daemon):
        super().__init__(conf, daemon)
        if "command" not in conf:
            raise ValueError(f"agent '{conf['id']}': ACP agents need a 'command'")
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr: StderrTail | None = None
        self.reader: asyncio.Task | None = None
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        self.session_id: str | None = None
        self.agent_info: dict = {}
        self.lock = asyncio.Lock()

    # ── JSON-RPC plumbing ──────────────────────────────────────────────────

    async def _write(self, msg: dict):
        msg["jsonrpc"] = "2.0"
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict):
        rid = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self._write({"id": rid, "method": method, "params": params})
        return await fut

    async def notify(self, method: str, params: dict):
        await self._write({"method": method, "params": params})

    async def _read_loop(self):
        proc = self.proc
        while line := await proc.stdout.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                log.debug("non-JSON output: %s", line[:200])
                continue
            if "method" in msg and "id" in msg:
                asyncio.create_task(self._answer(msg))
            elif "method" in msg:
                await self._notification(msg["method"], msg.get("params") or {})
            elif msg.get("id") in self.pending:
                fut = self.pending.pop(msg["id"])
                if not fut.done():
                    if "error" in msg:
                        fut.set_exception(AcpError(msg["error"]))
                    else:
                        fut.set_result(msg.get("result"))
        # the agent exited
        await proc.wait()
        if self.stderr:
            await self.stderr.task
        reason = (self.stderr.text() if self.stderr else "") or f"agent exited with code {proc.returncode}"
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self.pending.clear()
        if self.proc is proc:
            self.proc = None
            self.session_id = None

    async def _answer(self, msg: dict):
        try:
            result = await self._incoming(msg["method"], msg.get("params") or {})
            await self._write({"id": msg["id"], "result": result})
        except NotImplementedError:
            await self._write({"id": msg["id"], "error": {"code": -32601, "message": "method not supported"}})
        except Exception as e:
            log.exception("answering %s", msg["method"])
            await self._write({"id": msg["id"], "error": {"code": -32603, "message": str(e)}})

    # ── Agent lifecycle ─────────────────────────────────────────────────────

    async def _start(self):
        cmd = self.command("")
        if not cmd or not which(cmd[0]):
            raise RuntimeError(f"'{cmd[0] if cmd else ''}' not found: is the agent installed?")
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=self.cwd, env=self.env(), limit=16 * 1024 * 1024)
        self.stderr = StderrTail(self.proc.stderr)
        self.reader = asyncio.create_task(self._read_loop())
        init = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            # no file system or terminal access is offered: the agents use their own tools
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "oring", "version": "0.1.0"},
        })
        self.agent_info = init or {}

    async def _ensure_session(self):
        if self.proc is None or self.proc.returncode is not None:
            await self._start()
        if self.session_id:
            return
        servers = [{"name": s["name"], "command": s["command"], "args": s["args"],
                    "env": [{"name": k, "value": v} for k, v in s["env"].items()]}
                   for s in self.mcp_servers()]
        try:
            res = await self.request("session/new", {"cwd": self.cwd, "mcpServers": servers})
        except AcpError as e:
            if e.code == -32000 or "auth" in str(e).lower():
                methods = ", ".join(m.get("name", m.get("id", "?")) for m in self.agent_info.get("authMethods", []))
                raise RuntimeError(f"{self.conf['name']} needs to be signed in: run it once in a terminal"
                                   + (f" ({methods})" if methods else "")) from e
            raise
        self.session_id = res["sessionId"]
        self._first_prompt = True

    # ── Turns ───────────────────────────────────────────────────────────────

    async def prompt(self, text: str):
        async with self.lock:
            await self._ensure_session()
            blocks = []
            # ACP has no system prompt: it is sent along with the first message of each session
            if self._first_prompt and self.system_prompt and self.conf.get("send_system_prompt", True):
                blocks.append({"type": "text", "text": f"<instructions>\n{self.system_prompt}\n</instructions>"})
            blocks.append({"type": "text", "text": text})
            self._first_prompt = False
            res = await self.request("session/prompt", {"sessionId": self.session_id, "prompt": blocks})
            stop = (res or {}).get("stopReason")
            if stop == "refusal":
                await self.daemon.add("error", "the agent refused to answer")
            elif stop == "cancelled":
                await self.daemon.add("info", "cancelled")
            elif stop in ("max_tokens", "max_turn_requests"):
                await self.daemon.add("info", f"stopped: {stop}")

    async def _notification(self, method: str, params: dict):
        if method != "session/update" or params.get("sessionId") != self.session_id:
            return
        up = params.get("update") or {}
        kind = up.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = up.get("content") or {}
            if content.get("type") == "text":
                await self.daemon.add("agent", content.get("text", ""), stream=True)
        elif kind == "tool_call":
            await self.daemon.add("tool", self._tool_title(up))
        elif kind == "tool_call_update" and up.get("status") == "failed":
            await self.daemon.add("info", "failed: " + self._tool_title(up))

    def _tool_title(self, tool: dict) -> str:
        """Agents title MCP tool calls in their own way (desktop_clicca, mcp__desktop__clicca,
        desktop.clicca...): those of oring's MCP servers get the same labels as the other backends."""
        title = tool.get("title") or tool.get("kind") or "tool"
        for s in self.daemon.mcp:
            for prefix in (f"mcp__{s['name']}__", f"{s['name']}__", f"{s['name']}_", f"{s['name']}."):
                if title.startswith(prefix) and " " not in title:
                    return describe(title[len(prefix):], tool.get("rawInput"), self.daemon.language) or title
        return title if len(title) <= 160 else title[:157] + "…"

    async def _incoming(self, method: str, params: dict):
        if method == "session/request_permission":
            return await self._permission(params)
        raise NotImplementedError

    async def _permission(self, params: dict) -> dict:
        options = params.get("options") or []
        policy = self.conf.get("permissions", "ask")

        def first(prefix):
            return next((o["optionId"] for o in options if o.get("kind", "").startswith(prefix)), None)

        if policy == "allow":
            choice = first("allow_once") or first("allow")
        elif policy == "deny":
            choice = first("reject_once") or first("reject")
        else:
            tool = params.get("toolCall") or {}
            choice = await self.daemon.ask_permission(
                self._tool_title(tool),
                [{"id": o["optionId"], "name": o.get("name", o["optionId"]), "kind": o.get("kind", "")}
                 for o in options])
        if choice is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": choice}}

    async def cancel(self):
        if self.proc and self.session_id:
            await self.notify("session/cancel", {"sessionId": self.session_id})

    async def reset(self):
        self.session_id = None

    async def shutdown(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
