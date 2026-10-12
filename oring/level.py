"""The access level, set by an administrator at the machine: "sandbox" (default) or "full".

The level lives outside the user's reach: /var/lib/oring/level, owned by root. oring and its agents
run as the user, so they can read it but never change it. It is changed with `oring admin`, which
goes through polkit (pkexec) and is allowed only to the local, active session with the admin
password: not over SSH, not from a chat, not from an agent.

  sandbox  Claude runs in its sandbox (oring/permissions.py); chat gateways may only use sandboxed
           agents (the others are available from the panel only)
  full     Claude without sandbox (permission requests still apply); every agent from the chats

Anything unexpected reads as "sandbox": no file, unreadable or malformed, expired, or a file that
root does not own or that others can write.
"""

import json
import logging
import os
import stat
import time
from pathlib import Path

LEVELS = ("sandbox", "full")
DEFAULT_PATH = "/var/lib/oring/level"

log = logging.getLogger("oring.level")


def path() -> Path:
    return Path(os.environ.get("ORING_LEVEL_FILE", DEFAULT_PATH))


def read() -> dict:
    """{"level", "until" (epoch seconds or None), "set_by", "set_at"}; sandbox when in doubt."""
    sandbox = {"level": "sandbox", "until": None, "set_by": None, "set_at": None}
    p = path()
    try:
        st = p.stat()
        if not os.environ.get("ORING_LEVEL_ANY_OWNER"):        # tests only
            if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                log.warning("%s is not owned by root or is writable by others: ignored", p)
                return sandbox
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return sandbox
    except (OSError, ValueError) as e:
        log.warning("%s unreadable: %s", p, e)
        return sandbox
    if not isinstance(data, dict) or data.get("level") not in LEVELS:
        return sandbox
    until = data.get("until")
    if until is not None and (not isinstance(until, (int, float)) or until <= time.time()):
        return sandbox
    return {"level": data["level"], "until": until, "set_by": data.get("set_by"), "set_at": data.get("set_at")}


def current() -> str:
    return read()["level"]
