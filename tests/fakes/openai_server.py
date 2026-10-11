"""A minimal OpenAI-compatible streaming server for the tests (stdlib only).

First request of a turn (last message from the user): streams a tool call to
"desktop__scrivi_appunti" {"testo": "secret"} if the user text contains "clip", to
"desktop__elenca_finestre" if it contains "tool", otherwise streams "hello <text>".
After a tool result: streams "tool said: <result>".
Every request body is appended to `requests`.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOpenAI:
    def __init__(self):
        self.requests = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                last = dict(body["messages"][-1])
                if isinstance(last["content"], list):           # text + images: answer to the text
                    last["content"] = "".join(p.get("text", "") for p in last["content"])
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if last["role"] == "tool":
                    deltas = [{"content": "tool said: "}, {"content": last["content"][:40]}]
                elif "clip" in last["content"]:
                    args = json.dumps({"testo": "secret"})
                    deltas = [{"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                               "function": {"name": "desktop__scrivi_appunti", "arguments": args}}]}]
                elif "tool" in last["content"]:
                    deltas = [{"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                               "function": {"name": "desktop__elenca_finestre", "arguments": ""}}]},
                              {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}]
                else:
                    deltas = [{"content": "hello "}, {"content": last["content"]}]
                for d in deltas:
                    chunk = {"choices": [{"index": 0, "delta": d}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        return Handler
