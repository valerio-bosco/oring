"""Telegram gateway (Bot API, long polling: no inbound connection to this machine).

Options ([[gateways]] with type = "telegram"):
  token_file      file holding the bot token from @BotFather (keep it private, chmod 600)
  allowed_chats   chat ids allowed to talk to the agents (empty: setup mode, see base.py)
  api_url         default https://api.telegram.org (the tests point it to a fake server)
"""

import asyncio
import html
import logging
import os
import re
import time

import httpx

from .base import COMMANDS, Gateway, Incoming

log = logging.getLogger("oring.gateway.telegram")

POLL_TIMEOUT = 50


# ── Markdown → Telegram HTML ────────────────────────────────────────────────
# Agents answer in Markdown, which Telegram does not render, and its own MarkdownV2 rejects any
# unescaped "." or "-". HTML only needs &, <, > escaped. Partial texts (live edits, messages split
# at the length limit) must convert too: an unclosed fence runs to the end, stray markers stay.

FENCE = re.compile(r"^```[ \t]*([\w+-]*)[^\n]*\n(.*?)(?:^```[ \t]*$|\Z)", re.M | re.S)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
INLINE = [
    (re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)"), lambda m: f'<a href="{m[2].replace(chr(34), "&quot;")}">{m[1]}</a>'),
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)"), r"<b>\1</b>"),
    (re.compile(r"(?<![*\w])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![*\w])"), r"<i>\1</i>"),
    (re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])"), r"<i>\1</i>"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"<s>\1</s>"),
]


def _inline(text: str) -> str:
    codes = []

    def keep(m):
        codes.append(f"<code>{html.escape(m[1], quote=False)}</code>")
        return f"\0{len(codes) - 1}\0"

    text = html.escape(INLINE_CODE.sub(keep, text), quote=False)
    for pattern, repl in INLINE:
        text = pattern.sub(repl, text)
    return re.sub(r"\0(\d+)\0", lambda m: codes[int(m[1])], text)


def _blocks(text: str) -> str:
    out, table, quote = [], [], []

    def flush():
        if table:
            out.append("<pre>" + html.escape("\n".join(table), quote=False) + "</pre>")
            table.clear()
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for line in text.split("\n"):
        if line.lstrip().startswith("|"):
            if quote:
                flush()
            table.append(line.strip())
            continue
        if m := re.match(r"\s*>\s?(.*)", line):
            if table:
                flush()
            quote.append(_inline(m[1]))
            continue
        flush()
        if m := re.match(r"#{1,6}\s+(.*?)\s*#*$", line):
            out.append(f"<b>{_inline(m[1])}</b>")
        elif re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            out.append("──────────")
        elif m := re.match(r"(\s*)[-*+]\s+(.*)", line):
            out.append(m[1] + "• " + _inline(m[2]))
        else:
            out.append(_inline(line))
    flush()
    return "\n".join(out)


def to_html(text: str) -> str:
    """Convert Markdown (as written by agents) to the HTML subset of Telegram's parse_mode."""
    out, pos = [], 0
    for m in FENCE.finditer(text):
        out.append(_blocks(text[pos:m.start()]))
        lang = f' class="language-{m[1]}"' if m[1] else ""
        out.append(f"<pre><code{lang}>{html.escape(m[2].rstrip(chr(10)), quote=False)}</code></pre>")
        pos = m.end()
    out.append(_blocks(text[pos:]))
    return "".join(out)


class TelegramGateway(Gateway):
    name = "telegram"

    def __init__(self, conf: dict):
        super().__init__(conf)
        path = os.path.expanduser(conf.get("token_file", "~/.config/oring/telegram-token"))
        try:
            token = open(path).read().strip()
        except OSError as e:
            raise RuntimeError(f"telegram: cannot read the bot token from {path}: {e}") from e
        api = conf.get("api_url", "https://api.telegram.org").rstrip("/")
        self.base, self.files = f"{api}/bot{token}", f"{api}/file/bot{token}"
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
                if "message" in u and (images := self._images(u["message"])) is not None:
                    m = u["message"]
                    yield Incoming(chat=str(m["chat"]["id"]), text=m.get("text") or m.get("caption") or "",
                                   images=images)
                elif "callback_query" in u:
                    q = u["callback_query"]
                    msg = q.get("message") or {}
                    yield Incoming(chat=str(msg.get("chat", {}).get("id", "")), button=q.get("data", ""),
                                   ref=msg.get("message_id"), callback=q["id"])

    @staticmethod
    def _images(m: dict) -> list | None:
        """file_ids of the images in a message ([] for plain text), None for anything else."""
        if m.get("photo"):
            return [m["photo"][-1]["file_id"]]                 # sizes in ascending order
        if (m.get("document") or {}).get("mime_type", "").startswith("image/"):
            return [m["document"]["file_id"]]                  # sent as a file: not recompressed
        return [] if "text" in m else None

    async def download(self, ref, directory):
        f = await self.call("getFile", file_id=ref)
        r = await self.http.get(f"{self.files}/{f['file_path']}")
        if r.status_code != 200:                    # not raise_for_status: its message holds the token
            raise RuntimeError(f"telegram file download: HTTP {r.status_code}")
        path = directory / f"{int(time.time() * 1000)}-{os.path.basename(f['file_path'])}"
        path.write_bytes(r.content)
        return path

    @staticmethod
    def _keyboard(buttons):
        return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                    for row in buttons]}

    async def call_formatted(self, method: str, text: str, **params):
        """Send text as HTML converted from Markdown; if Telegram rejects the markup, as plain text."""
        try:
            return await self.call(method, text=to_html(text), parse_mode="HTML", **params)
        except RuntimeError as e:
            if "parse entities" not in str(e):
                raise
            log.warning("telegram: markup rejected, sending plain text: %s", e)
            return await self.call(method, text=text, **params)

    async def send(self, chat, text, buttons=None):
        params = {"chat_id": chat}
        if buttons:
            params["reply_markup"] = self._keyboard(buttons)
        return (await self.call_formatted("sendMessage", text, **params))["message_id"]

    async def edit(self, chat, ref, text, buttons=None):
        params = {"chat_id": chat, "message_id": ref, "reply_markup": self._keyboard(buttons or [])}
        try:
            await self.call_formatted("editMessageText", text, **params)
        except RuntimeError as e:
            if "not modified" not in str(e):
                raise

    async def answer_button(self, callback, text=None):
        if callback:
            await self.call("answerCallbackQuery", callback_query_id=callback, **({"text": text} if text else {}))
