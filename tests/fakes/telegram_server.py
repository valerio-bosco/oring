"""A fake Telegram Bot API for the tests (stdlib only): a queue of updates for getUpdates, and a
record of the bot's messages (text as the user sees it, raw markup, buttons, edit history) and calls.
parse_mode HTML is checked like Telegram does (known tags, balanced) and rendered to plain text;
set reject_html to make it refuse any markup."""

import itertools
import json
import threading
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123:TEST"
TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "blockquote",
        "tg-spoiler", "span"}


class _Render(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.open = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            raise ValueError(f'Bad Request: can\'t parse entities: unsupported start tag "{tag}"')
        self.open.append(tag)

    def handle_endtag(self, tag):
        if not self.open or self.open.pop() != tag:
            raise ValueError(f'Bad Request: can\'t parse entities: unexpected end tag "{tag}"')

    def handle_data(self, data):
        self.text.append(data)


def render_html(markup: str) -> str:
    r = _Render()
    r.feed(markup)
    r.close()
    if r.open:
        raise ValueError(f'Bad Request: can\'t parse entities: can\'t find end tag "{r.open[-1]}"')
    return "".join(r.text)


class FakeTelegram:
    def __init__(self):
        self.lock = threading.Condition()
        self.updates = []
        self.messages = {}          # message_id → {chat, text, buttons, history}
        self.calls = []             # (method, params)
        self.reject_html = False
        self.files = {}             # file_id → bytes, downloadable through getFile
        self.ids = itertools.count(1)
        self.update_ids = itertools.count(1)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    # ── test side ──
    def user_says(self, chat: int, text: str):
        self._push({"message": {"message_id": next(self.ids), "chat": {"id": chat, "type": "private"},
                                "from": {"id": chat}, "text": text}})

    def user_sends_photo(self, chat: int, data: bytes | None, caption: str | None = None):
        """data None: a photo whose download fails."""
        file_id = f"photo{next(self.ids)}"
        if data is not None:
            self.files[file_id] = data
        sizes = [{"file_id": "thumb-not-downloadable", "width": 90, "height": 90},
                 {"file_id": file_id, "width": 1280, "height": 960}]
        msg = {"message_id": next(self.ids), "chat": {"id": chat, "type": "private"}, "from": {"id": chat},
               "photo": sizes}
        if caption is not None:
            msg["caption"] = caption
        self._push({"message": msg})

    def user_presses(self, chat: int, message_id: int, data: str):
        self._push({"callback_query": {"id": f"cb{next(self.ids)}", "data": data, "from": {"id": chat},
                                       "message": {"message_id": message_id, "chat": {"id": chat}}}})

    def _push(self, update):
        with self.lock:
            update["update_id"] = next(self.update_ids)
            self.updates.append(update)
            self.lock.notify_all()

    def wait_for(self, predicate, timeout: float = 15):
        """Wait until predicate(self) is truthy; returns its value."""
        deadline = time.monotonic() + timeout
        with self.lock:
            while not (value := predicate(self)):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError("timeout; bot messages: " + json.dumps(self.messages, ensure_ascii=False))
                self.lock.wait(min(left, 0.2))
            return value

    def find(self, chat: int, contains: str):
        return next((mid for mid, m in self.messages.items() if m["chat"] == chat and contains in m["text"]), None)

    def texts(self, chat: int):
        return [m["text"] for m in self.messages.values() if m["chat"] == chat]

    # ── API side ──
    def _api(self, method, p):
        with self.lock:
            self.calls.append((method, p))
            self.lock.notify_all()
        if method == "getMe":
            return {"id": 1, "is_bot": True, "username": "oring_test_bot"}
        if method == "getFile":
            if p["file_id"] not in self.files:
                raise ValueError("Bad Request: invalid file_id")
            return {"file_id": p["file_id"], "file_path": f"photos/{p['file_id']}.jpg"}
        if method == "getUpdates":
            deadline = time.monotonic() + 0.5
            with self.lock:
                while True:
                    ready = [u for u in self.updates if u["update_id"] >= p.get("offset", 0)]
                    if ready or time.monotonic() > deadline:
                        return ready
                    self.lock.wait(0.1)
        if method in ("sendMessage", "editMessageText") and p.get("parse_mode") == "HTML":
            if self.reject_html:
                raise ValueError("Bad Request: can't parse entities: rejected by the test")
            p = {**p, "markup": p["text"], "text": render_html(p["text"])}
        if method == "sendMessage":
            mid = next(self.ids)
            with self.lock:
                self.messages[mid] = {"chat": int(p["chat_id"]), "text": p["text"], "history": [p["text"]],
                                      "markup": p.get("markup"), "buttons": self._buttons(p)}
                self.lock.notify_all()
            return {"message_id": mid}
        if method == "editMessageText":
            with self.lock:
                m = self.messages[int(p["message_id"])]
                if m["text"] == p["text"] and m["buttons"] == self._buttons(p):
                    raise ValueError("Bad Request: message is not modified")
                m["text"], m["markup"], m["buttons"] = p["text"], p.get("markup"), self._buttons(p)
                m["history"].append(p["text"])
                self.lock.notify_all()
            return True
        return True

    @staticmethod
    def _buttons(p):
        rows = (p.get("reply_markup") or {}).get("inline_keyboard", [])
        return [(b["text"], b["callback_data"]) for row in rows for b in row]

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                _, bot, method = self.path.split("/", 2)
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if bot != f"bot{TOKEN}":
                    reply = {"ok": False, "description": "Unauthorized"}
                else:
                    try:
                        reply = {"ok": True, "result": fake._api(method, body)}
                    except ValueError as e:
                        reply = {"ok": False, "description": str(e)}
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):                        # file downloads: /file/bot<token>/photos/<id>.jpg
                parts = self.path.split("/")
                file_id = parts[-1].rsplit(".", 1)[0]
                if parts[1:3] != ["file", f"bot{TOKEN}"] or file_id not in fake.files:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(fake.files[file_id])))
                self.end_headers()
                self.wfile.write(fake.files[file_id])

        return Handler
