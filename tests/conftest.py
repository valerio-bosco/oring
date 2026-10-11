"""Test fixtures: a real daemon process in an isolated environment (its own runtime, state and
config directories), with fake agents. Nothing touches the user's session or desktop."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKES = ROOT / "tests" / "fakes"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(FAKES))

from oring.client import Client  # noqa: E402


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_config(path: Path, agents: list[dict], **top):
    lines = [f"{k} = {toml_value(v)}" for k, v in top.items()]
    for a in agents:
        lines.append("[[agents]]")
        lines += [f"{k} = {toml_value(v)}" for k, v in a.items()]
    path.write_text("\n".join(lines) + "\n")


def fake_acp_agent(id="fake", **extra):
    return {"id": id, "name": "Fake", "type": "acp",
            "command": [sys.executable, str(FAKES / "acp_agent.py")], **extra}


class DaemonProcess:
    def __init__(self, tmp: Path, agents: list[dict], env: dict | None = None, **top):
        self.tmp = tmp
        self.run = tmp / "run"
        self.run.mkdir(mode=0o700, exist_ok=True)
        self.config = tmp / "config.toml"
        top.setdefault("language", "en")
        top.setdefault("desktop_mcp", False)
        write_config(self.config, agents, **top)
        inherited = {k: v for k, v in os.environ.items()
                     if k not in ("ORING_SOCKET", "ORING_DESKTOP_MCP", "ORING_BIN", "ORING_CONFIG")}
        self.env = {**inherited, "XDG_RUNTIME_DIR": str(self.run), "XDG_STATE_HOME": str(tmp / "state"),
                    "ORING_CONFIG": str(self.config), "PYTHONPATH": str(ROOT), "ORING_LOG": "DEBUG",
                    **(env or {})}
        self.socket = self.run / "oring" / "daemon.sock"
        self.log = tmp / "daemon.log"
        self.start()

    def start(self):
        self.socket.unlink(missing_ok=True)
        self.proc = subprocess.Popen([sys.executable, "-m", "oring", "daemon"], env=self.env, cwd=ROOT,
                                     stdout=self.log.open("a"), stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 10
        while not self.socket.exists():
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("daemon did not start:\n" + self.log.read_text())
            time.sleep(0.05)

    def client(self) -> Client:
        return Client(self.socket)

    def stop(self, timeout: float = 10) -> float:
        """SIGTERM and wait; returns how long the shutdown took."""
        t = time.monotonic()
        self.proc.terminate()
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise AssertionError("daemon did not stop on SIGTERM:\n" + self.log.read_text())
        return time.monotonic() - t


@pytest.fixture
def daemon(tmp_path):
    started = []

    def make(agents, **kw):
        d = DaemonProcess(tmp_path, agents, **kw)
        started.append(d)
        return d

    yield make
    for d in started:
        if d.proc.poll() is None:
            d.stop()
