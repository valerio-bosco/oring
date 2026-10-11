"""python -m oring daemon | send <text>"""

import asyncio
import json
import sys

from . import config


async def _send(text: str):
    """Send a message to the running daemon and print the conversation events until the turn ends."""
    reader, writer = await asyncio.open_unix_connection(str(config.socket_path()), limit=16 * 1024 * 1024)
    writer.write((json.dumps({"op": "send", "text": text}) + "\n").encode())
    await writer.drain()
    started = False
    while line := await reader.readline():
        ev = json.loads(line)
        if ev["ev"] == "busy":
            if ev["value"]:
                started = True
            elif started:
                break
        elif ev["ev"] == "append" and ev["role"] != "user":
            print(f"\n[{ev['role']}] {ev['text']}", end="", flush=True)
        elif ev["ev"] == "extend":
            print(ev["text"], end="", flush=True)
        elif ev["ev"] == "permission":
            print(f"\n[permission requested in the panel] {ev['title']}", flush=True)
    print()
    writer.close()


def main():
    args = sys.argv[1:]
    if args[:1] == ["daemon"]:
        from .daemon import serve
        asyncio.run(serve())
    elif args[:1] == ["send"] and len(args) > 1:
        asyncio.run(_send(" ".join(args[1:])))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
