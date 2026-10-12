"""Agent backends. Each one drives a kind of agent and reports to the daemon through
daemon.add(role, text, stream) and daemon.ask_permission(title, options)."""

import asyncio
import base64
import collections
import mimetypes
import os
import shlex
from pathlib import Path

from .. import config


def with_paths(text: str, images) -> str:
    """For agents that cannot take images in the prompt: they open the files with their own tools."""
    if not images:
        return text
    return (text + "\n\n" if text else "") + "Attached images (open the files to see them):\n" + "\n".join(images)


def image_data(path: str) -> tuple[str, str]:
    """(mime type, base64 data) of an image file."""
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    return mime, base64.b64encode(Path(path).read_bytes()).decode()


class Backend:
    def __init__(self, conf: dict, daemon):
        self.conf = conf
        self.daemon = daemon

    @property
    def system_prompt(self) -> str:
        return self.conf.get("system_prompt", self.daemon.cfg["system_prompt"])

    def mcp_servers(self) -> list[dict]:
        """The MCP servers for this agent; they can reach the daemon to ask for confirmations."""
        extra = {"ORING_SOCKET": str(config.socket_path()), "ORING_AGENT": self.conf["id"]}
        return [{**s, "env": {**s["env"], **extra}} for s in self.daemon.mcp]

    def command(self, default: str) -> list[str]:
        cmd = self.conf.get("command", default)
        return shlex.split(cmd) if isinstance(cmd, str) else list(cmd)

    def env(self) -> dict:
        env = dict(os.environ)
        env.update({k: str(v) for k, v in self.conf.get("env", {}).items()})
        return env

    @property
    def sandboxed(self) -> bool:
        """Runs in a sandbox at the current access level (only claude-cli can, see level.py)."""
        return False

    @property
    def cwd(self) -> str:
        return os.path.expanduser(self.conf.get("cwd", "~"))

    async def prompt(self, text: str, images: list[str] = ()):
        """Run a turn; images are paths of image files sent along with the text."""
        raise NotImplementedError

    async def cancel(self):
        """Ask the running turn to stop gracefully (the daemon cancels the task if it does not)."""

    async def reset(self):
        """Forget the conversation: the next prompt starts a new session."""

    async def shutdown(self):
        pass

    # Resuming the conversation after a daemon restart (where the agent supports it)
    def session_state(self):
        return None

    def restore(self, state):
        pass


class StderrTail:
    """Keeps the last lines of a process' stderr, to explain failures."""

    def __init__(self, stream: asyncio.StreamReader, lines: int = 20):
        self.lines = collections.deque(maxlen=lines)
        self.task = asyncio.create_task(self._read(stream))

    async def _read(self, stream):
        while line := await stream.readline():
            self.lines.append(line.decode(errors="replace").rstrip())

    def text(self) -> str:
        return "\n".join(l for l in self.lines if l.strip())


def which(cmd: str) -> bool:
    if "/" in cmd:
        return Path(cmd).exists()
    return any((Path(p) / cmd).exists() for p in os.environ.get("PATH", "").split(":") if p)


def make_backend(conf: dict, daemon) -> Backend:
    from .acp import AcpBackend
    from .claude_cli import ClaudeCliBackend
    from .openai import OpenAIBackend
    return {"claude-cli": ClaudeCliBackend, "acp": AcpBackend, "openai": OpenAIBackend}[conf["type"]](conf, daemon)
