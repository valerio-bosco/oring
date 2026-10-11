"""oring daemon: owns the conversation and the agents; clients (the panel, future gateways)
connect to a Unix socket and exchange newline-delimited JSON.

Client → daemon            {"op": ...}
  hello                     → full "state" event
  send       text, images?  start a turn with the active agent (images: paths of image files)
  cancel                    stop the running turn
  new                       start a new conversation
  select     agent          switch agent (starts a new conversation)
  permission id, option     answer a permission request (option = optionId, or null to deny)

Daemon → clients           {"ev": ...}, broadcast to every client
  state      agents, active, busy, language, history, permissions
  append     role, text     new history entry (role: user | agent | tool | info | error)
  extend     text           streamed text appended to the last entry
  busy       value
  permission id, title, options[{id, name, kind}]
  permission_done id
"""

import asyncio
import json
import logging
import os
import signal
import uuid

from . import config
from .backends import make_backend
from .labels import describe

log = logging.getLogger("oring")

HISTORY_LIMIT = 500


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.language = cfg["language"]
        self.mcp = config.mcp_servers(cfg)
        self.agents = {a["id"]: make_backend(a, self) for a in cfg["agents"]}
        self.clients: set[asyncio.StreamWriter] = set()
        self.busy = False
        self.turn: asyncio.Task | None = None
        self.permissions: dict[str, dict] = {}
        self.state_file = config.state_dir() / "conversation.json"
        self.active = cfg["default_agent"]
        self.history: list[dict] = []
        self._load_state()

    # ── State persistence ──────────────────────────────────────────────────

    def _load_state(self):
        try:
            data = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return
        if data.get("active") in self.agents:
            self.active = data["active"]
            self.history = data.get("history", [])[-HISTORY_LIMIT:]
            self.agents[self.active].restore(data.get("session"))

    def _save_state(self):
        data = {"active": self.active, "history": self.history[-HISTORY_LIMIT:],
                "session": self.agents[self.active].session_state()}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(self.state_file)

    # ── Output towards clients (also used by backends) ──────────────────────

    async def broadcast(self, event: dict):
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode()
        for w in list(self.clients):
            try:
                w.write(line)
                await w.drain()
            except (ConnectionError, RuntimeError):
                self.clients.discard(w)

    async def add(self, role: str, text: str, stream: bool = False):
        """Add to the conversation. stream=True: consecutive chunks of the same role are merged."""
        if not text:
            return
        last = self.history[-1] if self.history else None
        if stream and last and last["role"] == role and last.get("open"):
            last["text"] += text
            await self.broadcast({"ev": "extend", "text": text})
            return
        if last:
            last.pop("open", None)
        entry = {"role": role, "text": text}
        if stream:
            entry["open"] = True
        self.history.append(entry)
        await self.broadcast({"ev": "append", "role": role, "text": text})

    async def ask_permission(self, title: str, options: list[dict]) -> str | None:
        """Ask the user (in every client) to choose among options; returns the optionId, or None."""
        pid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.permissions[pid] = {"id": pid, "title": title, "options": options, "future": fut}
        await self.broadcast({"ev": "permission", "id": pid, "title": title, "options": options})
        try:
            return await fut
        finally:
            self.permissions.pop(pid, None)
            await self.broadcast({"ev": "permission_done", "id": pid})

    async def gate(self, msg: dict, always_ask: bool = False) -> bool:
        """A desktop tool asks whether it may run (see _con_conferma in desktop_mcp.py), or Claude Code
        asks for a tool that is not pre-allowed (always_ask, see permission_mcp.py)."""
        agent = next((a for a in self.cfg["agents"] if a["id"] == msg.get("agent")), None)
        if agent is None or not self.busy or msg.get("agent") != self.active:
            return False        # only the active agent, during a turn
        tool = str(msg.get("tool", ""))
        if not always_ask and tool not in agent.get("confirm_tools", self.cfg["confirm_tools"]):
            return True
        title = describe(tool, msg.get("args"), self.language) or tool
        allow, deny = ("Consenti", "Nega") if self.language == "it" else ("Allow", "Deny")
        choice = await self.ask_permission(title, [{"id": "allow", "name": allow, "kind": "allow_once"},
                                                   {"id": "deny", "name": deny, "kind": "reject_once"}])
        return choice == "allow"

    def _cancel_permissions(self):
        for p in self.permissions.values():
            if not p["future"].done():
                p["future"].set_result(None)

    def state(self) -> dict:
        return {
            "ev": "state",
            "agents": [{"id": a["id"], "name": a["name"], "type": a["type"]} for a in self.cfg["agents"]],
            "active": self.active,
            "busy": self.busy,
            "language": self.language,
            "history": [{"role": h["role"], "text": h["text"]} for h in self.history],
            "permissions": [{k: p[k] for k in ("id", "title", "options")} for p in self.permissions.values()],
        }

    # ── Turns ───────────────────────────────────────────────────────────────

    async def _set_busy(self, value: bool):
        self.busy = value
        await self.broadcast({"ev": "busy", "value": value})

    async def send(self, text: str, images: list[str] = ()):
        text = text.strip()
        images = [p for p in images if os.path.isfile(p)]
        if not text and not images:
            return
        if self.busy:
            await self.add("error", "busy")
            return
        shown = ([text] if text else []) + [f"📎 {os.path.basename(p)}" for p in images]
        await self.add("user", "\n".join(shown))
        await self._set_busy(True)
        self.turn = asyncio.create_task(self._run_turn(text, images))

    async def _run_turn(self, text: str, images: list[str] = ()):
        agent = self.agents[self.active]
        try:
            await agent.prompt(text, images)
        except asyncio.CancelledError:
            await self.add("info", "cancelled")
        except Exception as e:  # shown to the user: the turn failed, the daemon goes on
            log.exception("turn failed")
            await self.add("error", str(e) or type(e).__name__)
        finally:
            if self.history:
                self.history[-1].pop("open", None)
            self._cancel_permissions()
            self.turn = None
            self._save_state()
            await self._set_busy(False)

    async def cancel(self):
        turn = self.turn
        if not turn:
            return
        self._cancel_permissions()
        await self.agents[self.active].cancel()
        try:
            await asyncio.wait_for(asyncio.shield(turn), 3)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            turn.cancel()
        except Exception:
            pass

    async def new_conversation(self):
        await self.cancel()
        await self.agents[self.active].reset()
        self.history = []
        self._save_state()
        await self.broadcast(self.state())

    async def select(self, agent_id: str):
        if agent_id not in self.agents or agent_id == self.active:
            return
        await self.cancel()
        await self.agents[self.active].reset()
        self.active = agent_id
        await self.agents[self.active].reset()
        self.history = []
        self._save_state()
        await self.broadcast(self.state())

    # ── Socket server ───────────────────────────────────────────────────────

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.clients.add(writer)
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                await self.handle(msg, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    async def handle(self, msg: dict, writer: asyncio.StreamWriter):
        op = msg.get("op")
        if op == "hello":
            writer.write((json.dumps(self.state(), ensure_ascii=False) + "\n").encode())
            await writer.drain()
        elif op == "send":
            await self.send(str(msg.get("text", "")), [str(p) for p in msg.get("images") or []])
        elif op == "cancel":
            await self.cancel()
        elif op == "new":
            await self.new_conversation()
        elif op == "select":
            await self.select(str(msg.get("agent", "")))
        elif op in ("gate", "approve"):
            allow = await self.gate(msg, always_ask=op == "approve")
            writer.write((json.dumps({"ev": "gate", "allow": allow}) + "\n").encode())
            await writer.drain()
        elif op == "permission":
            p = self.permissions.get(str(msg.get("id")))
            if p and not p["future"].done():
                p["future"].set_result(msg.get("option"))

    async def shutdown(self):
        await self.cancel()
        for agent in self.agents.values():
            await agent.shutdown()
        self._save_state()


async def serve():
    logging.basicConfig(level=os.environ.get("ORING_LOG", "INFO"),
                        format="%(levelname)s %(name)s: %(message)s")
    daemon = Daemon(config.load())
    path = config.socket_path()
    path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(daemon.handle_client, path=str(path), limit=16 * 1024 * 1024)
    os.chmod(path, 0o600)
    log.info("listening on %s (agents: %s)", path, ", ".join(daemon.agents))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    server.close()
    try:
        await asyncio.wait_for(daemon.shutdown(), 8)
    except Exception:
        log.exception("shutdown")
    # connected clients (the panel) would keep wait_closed() waiting forever
    for w in list(daemon.clients):
        w.close()
    path.unlink(missing_ok=True)
