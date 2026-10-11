"""Synchronous client for the daemon socket (stdlib only). Used by `oring send/state/...`,
by the test suite and by the test skills."""

import json
import socket
import time

from . import config


class Client:
    def __init__(self, path: str | None = None, timeout: float = 5):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(timeout)
        self.sock.connect(str(path or config.socket_path()))
        self.file = self.sock.makefile("r", encoding="utf-8")

    def close(self):
        self.file.close()
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def op(self, op: str, **fields):
        self.sock.sendall((json.dumps({"op": op, **fields}) + "\n").encode())

    def event(self, timeout: float | None = None) -> dict | None:
        """Next event, or None on timeout / closed connection."""
        self.sock.settimeout(timeout)
        try:
            line = self.file.readline()
        except (TimeoutError, socket.timeout):
            return None
        return json.loads(line) if line else None

    def state(self) -> dict:
        self.op("hello")
        while ev := self.event(5):
            if ev["ev"] == "state":
                return ev
        raise TimeoutError("no state from the daemon")

    def turn(self, text: str, auto: str | None = None, timeout: float = 300, on_event=None) -> list[dict]:
        """Send a message and collect the events until the turn ends.
        auto: "allow" / "deny" answers permission requests automatically (first allow_* / reject_*
        option); None leaves them to the panel."""
        self.op("send", text=text)
        events, started, deadline = [], False, time.monotonic() + timeout
        while time.monotonic() < deadline:
            ev = self.event(max(0.1, deadline - time.monotonic()))
            if ev is None:
                break
            events.append(ev)
            if on_event:
                on_event(ev)
            if ev["ev"] == "busy":
                if ev["value"]:
                    started = True
                elif started:
                    return events
            elif ev["ev"] == "permission" and auto:
                prefix = "allow" if auto == "allow" else "reject"
                option = next((o["id"] for o in ev["options"] if o.get("kind", "").startswith(prefix)), None)
                self.op("permission", id=ev["id"], option=option)
            elif ev["ev"] == "append" and ev["role"] == "error" and ev["text"] == "busy" and not started:
                return events
        raise TimeoutError(f"turn not finished after {timeout}s")


def transcript(events: list[dict]) -> list[dict]:
    """Rebuild the conversation entries (role, text) produced by a list of events."""
    out = []
    for ev in events:
        if ev["ev"] == "append":
            out.append({"role": ev["role"], "text": ev["text"]})
        elif ev["ev"] == "extend" and out:
            out[-1]["text"] += ev["text"]
    return out
