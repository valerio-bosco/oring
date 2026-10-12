"""Test fixtures: a real daemon process in an isolated environment (its own runtime, state and
config directories), with fake agents. Nothing touches the user's session or desktop."""

import json
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
    tables = {k: v for k, v in top.items() if isinstance(v, list) and v and isinstance(v[0], dict)}
    lines = [f"{k} = {toml_value(v)}" for k, v in top.items() if k not in tables]
    for name, entries in [("agents", agents), *tables.items()]:
        for entry in entries:
            lines.append(f"[[{name}]]")
            lines += [f"{k} = {toml_value(v)}" for k, v in entry.items()]
    path.write_text("\n".join(lines) + "\n")


def fake_acp_agent(id="fake", **extra):
    return {"id": id, "name": "Fake", "type": "acp",
            "command": [sys.executable, str(FAKES / "acp_agent.py")], **extra}


class DaemonProcess:
    def __init__(self, tmp: Path, agents: list[dict], env: dict | None = None, level: str | None = None, **top):
        self.tmp = tmp
        # the access level (oring/level.py) comes from a file in the test dir, never /var/lib/oring
        self.level_file = tmp / "level"
        if level:
            self.set_level(level)
        self.run = tmp / "run"
        self.run.mkdir(mode=0o700, exist_ok=True)
        self.config = tmp / "config.toml"
        top.setdefault("language", "en")
        top.setdefault("desktop_mcp", False)
        write_config(self.config, agents, **top)
        inherited = {k: v for k, v in os.environ.items()
                     if k not in ("ORING_SOCKET", "ORING_DESKTOP_MCP", "ORING_BIN", "ORING_CONFIG",
                                  "ORING_LEVEL_FILE", "ORING_LEVEL_ANY_OWNER")}
        self.env = {**inherited, "XDG_RUNTIME_DIR": str(self.run), "XDG_STATE_HOME": str(tmp / "state"),
                    "ORING_CONFIG": str(self.config), "PYTHONPATH": str(ROOT), "ORING_LOG": "DEBUG",
                    "ORING_LEVEL_FILE": str(self.level_file), "ORING_LEVEL_ANY_OWNER": "1",
                    **(env or {})}
        self.socket = self.run / "oring" / "daemon.sock"
        self.log = tmp / "daemon.log"
        self.children = []
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

    def set_level(self, value: str | None, until=None):
        """Write the access level file as `oring admin` would (None: remove it, i.e. sandbox)."""
        if value is None:
            self.level_file.unlink(missing_ok=True)
        else:
            self.level_file.write_text(json.dumps({"level": value, "until": until, "set_by": "test", "set_at": 0}))

    def spawn(self, *args) -> subprocess.Popen:
        """Another oring process (e.g. `gateways`) in the same isolated environment."""
        log = (self.tmp / f"{args[0]}.log").open("a")
        proc = subprocess.Popen([sys.executable, "-m", "oring", *args], env=self.env, cwd=ROOT,
                                stdout=log, stderr=subprocess.STDOUT)
        self.children.append(proc)
        return proc

    def client(self) -> Client:
        return Client(self.socket)

    def stop(self, timeout: float = 10) -> float:
        """SIGTERM and wait; returns how long the shutdown took."""
        for child in self.children:
            child.terminate()
            child.wait(10)
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
