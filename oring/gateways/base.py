"""Messaging gateways: clients of the daemon that bring the conversation to a chat app.

The platform-independent part lives here: the daemon connection, which events reach the chat,
live-updating answers, permission buttons, commands and the chat allowlist. A platform only
implements the small adapter interface at the bottom of `Gateway` (see telegram.py).

Behaviour:
- Messages from allowed chats start a turn with the active agent (images are saved to the inbox in
  the state directory and passed to the agent as files); the answer appears in one
  message that is edited while the agent works (tool calls as "⚙ …" lines).
- Turns started elsewhere (the panel) are not mirrored, but their permission requests are sent to
  every allowed chat, so they can be answered remotely. A request answered elsewhere is closed.
- Commands: /new, /agent (buttons), /stop, /status, /help.
- Chats not in `allowed_chats` are ignored and logged. With an empty list the gateway is in setup
  mode: it answers with the chat id to add, and does nothing else.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

log = logging.getLogger("oring.gateway")

STRINGS = {
    "it": {
        "help": "Scrivimi e passo il messaggio all'agente attivo.\n/new nuova conversazione\n"
                "/agent scegli l'agente\n/stop interrompi\n/status stato",
        "busy": "Sto ancora lavorando alla richiesta precedente: aspetta, oppure /stop.",
        "new": "Nuova conversazione con {agent}.", "stopped": "Interrotto.", "nothing": "Niente da interrompere.",
        "pick": "Agente attivo: {agent}. Scegline un altro (inizia una nuova conversazione):",
        "selected": "Agente: {agent}. Nuova conversazione.",
        "status": "Agente: {agent}\nStato: {state}\nMessaggi nella conversazione: {count}",
        "working": "sta lavorando", "idle": "pronto", "permission": "🔐 Richiesta di permesso",
        "answered": "risposto", "answered_elsewhere": "risposto da un altro dispositivo",
        "done": "✓ Fatto.", "offline": "Il daemon di oring non è raggiungibile.",
        "setup": "oring: questa chat non è ancora autorizzata.\nAggiungi questo id ad allowed_chats: {chat}",
        "cancelled": "Interrotto", "busy_err": "Occupato",
        "image_failed": "⚠ Non sono riuscito a scaricare l'immagine, riprova.",
    },
    "en": {
        "help": "Write to me and I pass the message to the active agent.\n/new new conversation\n"
                "/agent pick the agent\n/stop interrupt\n/status status",
        "busy": "Still working on the previous request: wait, or /stop.",
        "new": "New conversation with {agent}.", "stopped": "Stopped.", "nothing": "Nothing to stop.",
        "pick": "Active agent: {agent}. Pick another one (starts a new conversation):",
        "selected": "Agent: {agent}. New conversation.",
        "status": "Agent: {agent}\nState: {state}\nMessages in the conversation: {count}",
        "working": "working", "idle": "ready", "permission": "🔐 Permission request",
        "answered": "answered", "answered_elsewhere": "answered on another device",
        "done": "✓ Done.", "offline": "The oring daemon is unreachable.",
        "setup": "oring: this chat is not authorized yet.\nAdd this id to allowed_chats: {chat}",
        "cancelled": "Cancelled", "busy_err": "Busy",
        "image_failed": "⚠ Could not download the image, please try again.",
    },
}

COMMANDS = {
    "it": {"new": "nuova conversazione", "agent": "scegli l'agente", "stop": "interrompi",
           "status": "stato", "help": "aiuto"},
    "en": {"new": "new conversation", "agent": "pick the agent", "stop": "interrupt",
           "status": "status", "help": "help"},
}
MAX_MESSAGE = 3800          # platforms limit message length (Telegram: 4096)
EDIT_INTERVAL = 1.5         # seconds between edits of a live message (rate limits)
INBOX_KEEP = 50             # received images kept on disk (the agents read them from there)


def inbox() -> Path:
    """Directory for images received from the chats; only the newest INBOX_KEEP files are kept."""
    d = config.state_dir() / "inbox"
    d.mkdir(exist_ok=True)
    for old in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime)[:-INBOX_KEEP]:
        old.unlink(missing_ok=True)
    return d


@dataclass
class Incoming:
    """Something the user did in the chat app."""
    chat: str
    text: str = ""                 # a message (commands start with "/"), or an image caption
    images: list = field(default_factory=list)     # platform references of attached images
    button: str | None = None      # or a button press: its data
    ref: object = None             # platform reference of the message holding the button
    callback: object = None        # platform reference to acknowledge the button press


@dataclass
class LiveTurn:
    """A turn started from the chat: its answer is one message, edited as events arrive."""
    chat: str
    parts: list = field(default_factory=list)      # [kind, text]
    ref: object = None
    sent_text: str = ""
    last_edit: float = 0.0


class Gateway:
    name = "gateway"

    def __init__(self, conf: dict):
        self.conf = conf
        self.allowed = {str(c) for c in conf.get("allowed_chats", [])}
        self.state: dict = {"agents": [], "active": "", "busy": False, "language": "en", "history": []}
        self.writer: asyncio.StreamWriter | None = None
        self.turn: LiveTurn | None = None
        self.pending_chat: str | None = None     # we sent a message: the next turn is ours
        self.permissions: dict[str, dict] = {}   # id → {options, messages: [(chat, ref)]}
        self.connected = asyncio.Event()
        self.state_seen = asyncio.Event()

    def tr(self, key: str, **kw) -> str:
        return STRINGS.get(self.state["language"], STRINGS["en"])[key].format(**kw)

    def agent_name(self, agent_id: str | None = None) -> str:
        agent_id = agent_id or self.state["active"]
        return next((a["name"] for a in self.state["agents"] if a["id"] == agent_id), agent_id or "?")

    # ── Main loop ───────────────────────────────────────────────────────────

    async def run(self):
        await self.start()
        tasks = [asyncio.create_task(self._daemon_loop()), asyncio.create_task(self._chat_loop()),
                 asyncio.create_task(self._flush_loop())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                t.result()
        finally:
            for t in tasks:
                t.cancel()
            await self.stop()

    async def _daemon_loop(self):
        while True:
            try:
                reader, self.writer = await asyncio.open_unix_connection(
                    str(config.socket_path()), limit=16 * 1024 * 1024)
            except OSError:
                await asyncio.sleep(2)
                continue
            log.info("%s: connected to the daemon", self.name)
            await self.op("hello")
            self.connected.set()
            try:
                while line := await reader.readline():
                    await self.on_event(json.loads(line))
            except (ConnectionError, ValueError) as e:
                log.warning("%s: daemon connection lost: %s", self.name, e)
            self.connected.clear()
            self.writer = None
            await asyncio.sleep(1)

    async def op(self, op: str, **fields):
        if self.writer:
            self.writer.write((json.dumps({"op": op, **fields}) + "\n").encode())
            await self.writer.drain()

    async def op_and_wait_state(self, op: str, **fields):
        """Operations that end with a fresh state (new, select): reply only once it arrived, so
        a following /status does not read the old one."""
        self.state_seen.clear()
        await self.op(op, **fields)
        try:
            await asyncio.wait_for(self.state_seen.wait(), 10)
        except asyncio.TimeoutError:
            log.warning("%s: no state after %s", self.name, op)

    async def _chat_loop(self):
        async for inc in self.receive():
            try:
                await self.on_incoming(inc)
            except Exception:
                log.exception("%s: handling %s", self.name, inc)

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(EDIT_INTERVAL / 3)
            if self.turn and time.monotonic() - self.turn.last_edit >= EDIT_INTERVAL:
                await self._render(self.turn)

    # ── From the chat app ───────────────────────────────────────────────────

    async def on_incoming(self, inc: Incoming):
        log.debug("%s: incoming %s", self.name, inc)
        if not self.allowed:
            log.warning("%s: setup mode, message from chat %s", self.name, inc.chat)
            if not inc.button:
                await self.send(inc.chat, self.tr("setup", chat=inc.chat))
            return
        if inc.chat not in self.allowed:
            log.warning("%s: ignored message from unauthorized chat %s", self.name, inc.chat)
            return
        if not self.connected.is_set():
            await self.send(inc.chat, self.tr("offline"))
            return
        if inc.button:
            await self.on_button(inc)
        elif inc.text.startswith("/"):
            await self.on_command(inc.chat, inc.text[1:].split()[0].split("@")[0].lower())
        elif inc.text.strip() or inc.images:
            if self.state["busy"] or self.pending_chat:
                await self.send(inc.chat, self.tr("busy"))
                return
            self.pending_chat = inc.chat
            try:
                images = [str(await self.download(ref, inbox())) for ref in inc.images]
            except Exception as e:
                log.warning("%s: could not download an image: %s", self.name, e)
                self.pending_chat = None
                await self.send(inc.chat, self.tr("image_failed"))
                return
            await self.op("send", text=inc.text, images=images)

    async def on_command(self, chat: str, cmd: str):
        if cmd == "new":
            await self.op_and_wait_state("new")
            await self.send(chat, self.tr("new", agent=self.agent_name()))
        elif cmd == "stop":
            if self.state["busy"]:
                await self.op("cancel")
                await self.send(chat, self.tr("stopped"))
            else:
                await self.send(chat, self.tr("nothing"))
        elif cmd == "agent":
            buttons = [[(("● " if a["id"] == self.state["active"] else "") + a["name"], f"a:{a['id']}")]
                       for a in self.state["agents"]]
            await self.send(chat, self.tr("pick", agent=self.agent_name()), buttons)
        elif cmd == "status":
            await self.send(chat, self.tr("status", agent=self.agent_name(), count=len(self.state["history"]),
                                          state=self.tr("working" if self.state["busy"] else "idle")))
        else:
            await self.send(chat, self.tr("help"))

    async def on_button(self, inc: Incoming):
        kind, _, rest = inc.button.partition(":")
        if kind == "a":
            await self.answer_button(inc.callback)
            # the daemon sends a new state only when the agent actually changes
            if rest != self.state["active"] and any(a["id"] == rest for a in self.state["agents"]):
                await self.op_and_wait_state("select", agent=rest)
            await self.edit(inc.chat, inc.ref, self.tr("selected", agent=self.agent_name(rest)))
        elif kind == "p":
            pid, _, index = rest.partition(":")
            p = self.permissions.get(pid)
            if not p:
                await self.answer_button(inc.callback)
                return
            option = p["options"][int(index)]
            await self.op("permission", id=pid, option=option["id"])
            p["answer"] = option["name"]
            await self.answer_button(inc.callback, option["name"])

    # ── From the daemon ─────────────────────────────────────────────────────

    async def on_event(self, ev: dict):
        kind = ev["ev"]
        log.debug("%s: event %s", self.name, {k: (v if k != "history" else len(v)) for k, v in ev.items()})
        if kind == "state":
            self.state.update({k: ev[k] for k in ("agents", "active", "busy", "language", "history")})
            self.state_seen.set()
        elif kind == "busy":
            self.state["busy"] = ev["value"]
            if ev["value"] and self.pending_chat:
                self.turn, self.pending_chat = LiveTurn(self.pending_chat), None
            elif not ev["value"] and self.turn:
                turn, self.turn = self.turn, None
                if not turn.parts:
                    turn.parts.append(["info", self.tr("done")])
                await self._render(turn, final=True)
        elif kind == "append":
            self.state["history"].append({"role": ev["role"], "text": ev["text"]})
            if self.turn and ev["role"] != "user":
                self.turn.parts.append([ev["role"], self._localize(ev["role"], ev["text"])])
            elif ev["role"] == "error" and ev["text"] == "busy" and self.pending_chat:
                self.pending_chat = None
        elif kind == "extend":
            if self.state["history"]:
                self.state["history"][-1]["text"] += ev["text"]
            if self.turn and self.turn.parts:
                self.turn.parts[-1][1] += ev["text"]
        elif kind == "permission":
            await self._ask_permission(ev)
        elif kind == "permission_done":
            await self._close_permission(ev["id"])

    def _localize(self, role: str, text: str) -> str:
        if role == "info" and text == "cancelled":
            return self.tr("cancelled")
        if role == "error" and text == "busy":
            return self.tr("busy_err")
        return text

    async def _ask_permission(self, ev: dict):
        chats = [self.turn.chat] if self.turn else sorted(self.allowed)
        buttons = [[(("✅ " if o.get("kind", "").startswith("allow") else "❌ ") + o["name"], f"p:{ev['id']}:{i}")]
                   for i, o in enumerate(ev["options"])]
        text = f"{self.tr('permission')}\n{ev['title']}"
        if self.turn:
            await self._render(self.turn)              # show what led to the request first
        # registered before sending: a quick press can arrive before send() returns
        p = self.permissions[ev["id"]] = {"options": ev["options"], "messages": [], "title": ev["title"]}
        for chat in chats:
            p["messages"].append((chat, await self.send(chat, text, buttons)))

    async def _close_permission(self, pid: str):
        p = self.permissions.pop(pid, None)
        if not p:
            return
        outcome = f"{self.tr('answered')}: {p['answer']}" if p.get("answer") else self.tr("answered_elsewhere")
        for chat, ref in p["messages"]:
            await self.edit(chat, ref, f"{self.tr('permission')}\n{p['title']}\n→ {outcome}")

    # ── Live message rendering ──────────────────────────────────────────────

    @staticmethod
    def _format(parts: list) -> str:
        icon = {"tool": "⚙ ", "info": "ℹ ", "error": "⚠ "}
        return "\n\n".join(icon.get(kind, "") + text.strip() for kind, text in parts if text.strip())

    async def _render(self, turn: LiveTurn, final: bool = False):
        text = self._format(turn.parts)
        # too long for one message: freeze the current one and continue in a new message
        while len(text) > MAX_MESSAGE:
            cut = text.rfind("\n", 0, MAX_MESSAGE)
            cut = cut if cut > MAX_MESSAGE // 2 else MAX_MESSAGE
            head, text = text[:cut], text[cut:].lstrip()
            await self._show(turn, head)
            turn.parts, turn.ref, turn.sent_text = [["agent", text]], None, ""
        if text.strip():
            await self._show(turn, text + ("" if final else " …"))
        turn.last_edit = time.monotonic()

    async def _show(self, turn: LiveTurn, text: str):
        if not text.strip() or text == turn.sent_text:
            return
        try:
            if turn.ref is None:
                turn.ref = await self.send(turn.chat, text)
            else:
                await self.edit(turn.chat, turn.ref, text)
            turn.sent_text = text
        except Exception as e:      # a failed edit must not end the turn (rate limits, network)
            log.warning("%s: could not update the message: %s", self.name, e)

    # ── Platform adapter ────────────────────────────────────────────────────

    async def start(self):
        """Connect to the platform."""

    async def stop(self):
        pass

    def receive(self):
        """Async iterator of Incoming."""
        raise NotImplementedError

    async def send(self, chat: str, text: str, buttons: list | None = None):
        """Send a message; buttons: rows of (label, data). Returns a reference for edit()."""
        raise NotImplementedError

    async def edit(self, chat: str, ref, text: str, buttons: list | None = None):
        """Replace the text (and buttons; None removes them) of a sent message."""
        raise NotImplementedError

    async def answer_button(self, callback, text: str | None = None):
        """Acknowledge a button press (some platforms show a spinner until then)."""

    async def download(self, ref, directory: Path) -> Path:
        """Save an attached image (Incoming.images entry) into directory; returns its path."""
        raise NotImplementedError
