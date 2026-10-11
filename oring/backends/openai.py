"""Any OpenAI-compatible chat completions API: Ollama (local), llama.cpp server, LM Studio,
OpenRouter, ... The model gets the MCP servers' tools as functions; oring runs the tool loop.

Options:  model (required), base_url (default Ollama: http://localhost:11434/v1),
          api_key_env (name of the environment variable holding the key, if any),
          permissions: "allow" (default) | "ask" | "deny" for tool calls,
          max_steps (default 12): tool calls allowed in one turn,
          vision (default false): the model takes images; otherwise it gets their file paths.
"""

import asyncio
import json
import logging
import os

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import Backend, image_data, with_paths
from ..labels import describe

log = logging.getLogger("oring.openai")


class McpConnection:
    """A long-lived MCP client session, running in its own task (the MCP client must be
    opened and closed in the same task); tool calls are queued to it."""

    def __init__(self, server: dict):
        self.server = server
        self.tools: list = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.ready = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        params = StdioServerParameters(command=self.server["command"], args=self.server["args"],
                                       env={**os.environ, **self.server["env"]})
        try:
            async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
                await session.initialize()
                self.tools = (await session.list_tools()).tools
                self.ready.set_result(True)
                while True:
                    name, args, fut = await self.queue.get()
                    try:
                        fut.set_result(await session.call_tool(name, args))
                    except Exception as e:
                        if not fut.done():
                            fut.set_exception(e)
        except Exception as e:
            log.warning("MCP server %s: %s", self.server["name"], e)
            if not self.ready.done():
                self.ready.set_exception(e)

    async def call(self, name: str, args: dict):
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put((name, args, fut))
        return await fut

    async def close(self):
        self.task.cancel()


class OpenAIBackend(Backend):
    def __init__(self, conf, daemon):
        super().__init__(conf, daemon)
        if "model" not in conf:
            raise ValueError(f"agent '{conf['id']}': OpenAI-compatible agents need a 'model'")
        self.messages: list[dict] = []
        self.connections: dict[str, McpConnection] | None = None
        self.functions: dict[str, tuple[McpConnection, str]] = {}

    async def _tools(self) -> list[dict]:
        if self.connections is None:
            self.connections = {s["name"]: McpConnection(s) for s in self.mcp_servers()}
            for name, conn in list(self.connections.items()):
                try:
                    await asyncio.wait_for(asyncio.shield(conn.ready), 30)
                except Exception as e:
                    await self.daemon.add("error", f"MCP server '{name}' unavailable: {e}")
                    del self.connections[name]
                    continue
                for t in conn.tools:
                    self.functions[f"{name}__{t.name}"] = (conn, t.name)
        tools = []
        for fname, (conn, tname) in self.functions.items():
            t = next(t for t in conn.tools if t.name == tname)
            tools.append({"type": "function", "function": {
                "name": fname, "description": t.description or "", "parameters": t.inputSchema}})
        return tools

    async def prompt(self, text: str, images: list[str] = ()):
        if not self.messages and self.system_prompt:
            self.messages.append({"role": "system", "content": self.system_prompt})
        if images and self.conf.get("vision"):
            content = [{"type": "text", "text": text}]
            for path in images:
                mime, data = image_data(path)
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        else:
            content = with_paths(text, images)
        self.messages.append({"role": "user", "content": content})
        tools = await self._tools()
        base = self.conf.get("base_url", "http://localhost:11434/v1").rstrip("/")
        headers = {}
        if self.conf.get("api_key_env"):
            headers["Authorization"] = f"Bearer {os.environ.get(self.conf['api_key_env'], '')}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
            for _ in range(int(self.conf.get("max_steps", 12))):
                content, calls = await self._complete(client, base, headers, tools)
                msg = {"role": "assistant", "content": content or None}
                if calls:
                    msg["tool_calls"] = calls
                self.messages.append(msg)
                if not calls:
                    return
                for call in calls:
                    self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                          "content": await self._run_tool(call)})
            await self.daemon.add("info", "stopped: max_steps")

    async def _complete(self, client, base, headers, tools):
        body = {"model": self.conf["model"], "messages": self.messages, "stream": True}
        if tools:
            body["tools"] = tools
        content, calls = "", {}
        async with client.stream("POST", f"{base}/chat/completions", json=body, headers=headers) as r:
            if r.status_code != 200:
                raise RuntimeError(f"{base}: HTTP {r.status_code} {(await r.aread()).decode()[:300]}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                choice = (json.loads(data).get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content += delta["content"]
                    await self.daemon.add("agent", delta["content"], stream=True)
                for tc in delta.get("tool_calls") or []:
                    c = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                              "function": {"name": "", "arguments": ""}})
                    c["id"] = tc.get("id") or c["id"]
                    fn = tc.get("function") or {}
                    c["function"]["name"] += fn.get("name") or ""
                    c["function"]["arguments"] += fn.get("arguments") or ""
        result = [c for _, c in sorted(calls.items())]
        for i, c in enumerate(result):
            c["id"] = c["id"] or f"call_{i}"
        return content, result

    async def _run_tool(self, call: dict) -> str:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
        except ValueError:
            return "error: arguments are not valid JSON"
        title = describe(name, args, self.daemon.language) or name
        target = self.functions.get(name)
        if not target:
            return f"error: unknown tool {name}"

        policy = self.conf.get("permissions", "allow")
        if policy == "deny":
            return "error: tool use is disabled"
        if policy == "ask":
            choice = await self.daemon.ask_permission(title, [
                {"id": "allow", "name": "Allow", "kind": "allow_once"},
                {"id": "reject", "name": "Deny", "kind": "reject_once"}])
            if choice != "allow":
                return "error: the user denied this action"

        await self.daemon.add("tool", title)
        conn, tool = target
        try:
            res = await conn.call(tool, args)
        except Exception as e:
            return f"error: {e}"
        parts = []
        for c in res.content:
            if getattr(c, "type", "") == "text":
                parts.append(c.text)
            else:
                parts.append(f"[{getattr(c, 'type', 'content')} omitted]")
        text = "\n".join(parts) or "(no output)"
        return ("error: " if res.isError else "") + text

    async def reset(self):
        self.messages = []

    async def shutdown(self):
        for conn in (self.connections or {}).values():
            await conn.close()
