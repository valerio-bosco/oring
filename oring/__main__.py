"""oring control commands (python -m oring <command>):

  daemon                         run the daemon
  gateways                       run the messaging gateways ([[gateways]] in the config)
  send [--auto allow|deny] TEXT  send a message, print the answer (permissions: panel, or answered)
  state                          print agents, active agent, busy flag and history as JSON
  select AGENT                   switch agent (starts a new conversation)
  new                            start a new conversation
  cancel                         stop the running turn
"""

import json
import sys

from .client import Client


def _print_event(ev: dict):
    if ev["ev"] == "append" and ev["role"] != "user":
        print(f"\n[{ev['role']}] {ev['text']}", end="", flush=True)
    elif ev["ev"] == "extend":
        print(ev["text"], end="", flush=True)
    elif ev["ev"] == "permission":
        print(f"\n[permission] {ev['title']}", flush=True)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "daemon":
        import asyncio
        from .daemon import serve
        asyncio.run(serve())
        return
    if cmd == "gateways":
        import asyncio
        from .gateways import serve as serve_gateways
        asyncio.run(serve_gateways())
        return

    if cmd == "send" and len(args) > 1:
        auto = None
        if args[1] == "--auto" and len(args) > 3 and args[2] in ("allow", "deny"):
            auto, args = args[2], args[2:]
        with Client() as c:
            c.turn(" ".join(args[1:]), auto=auto, on_event=_print_event)
        print()
    elif cmd == "state":
        with Client() as c:
            print(json.dumps(c.state(), ensure_ascii=False, indent=2))
    elif cmd == "select" and len(args) == 2:
        with Client() as c:
            c.op("select", agent=args[1])
            print(c.state()["active"])
    elif cmd in ("new", "cancel") and len(args) == 1:
        with Client() as c:
            c.op(cmd)
            c.state()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
