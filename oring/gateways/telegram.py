"""Telegram gateway (Bot API, long polling: no inbound connection to this machine).

Options ([[gateways]] with type = "telegram"):
  token_file      file holding the bot token from @BotFather (keep it private, chmod 600)
  allowed_chats   chat ids allowed to talk to the agents (empty: setup mode, see base.py)
  api_url         default https://api.telegram.org (the tests point it to a fake server)
"""

import asyncio
import logging
import os

import httpx

from .base import COMMANDS, Gateway, Incoming

log = logging.getLogger("oring.gateway.telegram")

POLL_TIMEOUT = 50


class TelegramGateway(Gateway):
    name = "telegram"

    def __init__(self, conf: dict):
        super().__init__(conf)
        path = os.path.expanduser(conf.get("token_file", "~/.config/oring/telegram-token"))
        try:
            token = open(path).read().strip()
        except OSError as e:
            raise RuntimeError(f"telegram: cannot read the bot token from {path}: {e}") from e
        self.base = f"{conf.get('api_url', 'https://api.telegram.org').rstrip('/')}/bot{token}"
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(POLL_TIMEOUT + 15, connect=10))
        self.offset = 0

    async def call(self, method: str, **params):
        r = await self.http.post(f"{self.base}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description', r.status_code)}")
        return data["result"]

    async def start(self):
        me = await self.call("getMe")
        log.info("telegram: bot @%s", me.get("username"))
        commands = COMMANDS.get(self.conf.get("language"), COMMANDS["en"])
        await self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands.items()])

    async def stop(self):
        await self.http.aclose()

    async def receive(self):
        while True:
            try:
                updates = await self.call("getUpdates", offset=self.offset, timeout=POLL_TIMEOUT,
                                          allowed_updates=["message", "callback_query"])
            except (httpx.HTTPError, RuntimeError) as e:
                log.warning("telegram: polling failed: %s", e)
                await asyncio.sleep(5)
                continue
            for u in updates:
                self.offset = u["update_id"] + 1
                if "message" in u and "text" in u["message"]:
                    m = u["message"]
                    yield Incoming(chat=str(m["chat"]["id"]), text=m["text"])
                elif "callback_query" in u:
                    q = u["callback_query"]
                    msg = q.get("message") or {}
                    yield Incoming(chat=str(msg.get("chat", {}).get("id", "")), button=q.get("data", ""),
                                   ref=msg.get("message_id"), callback=q["id"])

    @staticmethod
    def _keyboard(buttons):
        return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                    for row in buttons]}

    async def send(self, chat, text, buttons=None):
        params = {"chat_id": chat, "text": text}
        if buttons:
            params["reply_markup"] = self._keyboard(buttons)
        return (await self.call("sendMessage", **params))["message_id"]

    async def edit(self, chat, ref, text, buttons=None):
        params = {"chat_id": chat, "message_id": ref, "text": text,
                  "reply_markup": self._keyboard(buttons or [])}
        try:
            await self.call("editMessageText", **params)
        except RuntimeError as e:
            if "not modified" not in str(e):
                raise

    async def answer_button(self, callback, text=None):
        if callback:
            await self.call("answerCallbackQuery", callback_query_id=callback, **({"text": text} if text else {}))
