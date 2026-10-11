"""Permission prompt for Claude Code in headless mode (--permission-prompt-tool mcp__oring__approve).

Claude Code calls this tool whenever a tool it wants to use is not pre-allowed; the request is
forwarded to the oring daemon, which shows it in the panel. Without an answer it is denied.
"""

import json
import os
import socket

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("oring")


# plain text result only: Claude Code reads the JSON decision from the text content
@mcp.tool(structured_output=False)
def approve(tool_name: str, input: dict, tool_use_id: str | None = None) -> str:
    """Ask the user, in the oring panel, whether Claude Code may use a tool."""
    request = {"op": "approve", "agent": os.environ.get("ORING_AGENT", ""), "tool": tool_name, "args": input}
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(os.environ["ORING_SOCKET"])
            s.sendall((json.dumps(request) + "\n").encode())
            answer = next((e for e in map(json.loads, s.makefile()) if e.get("ev") == "gate"), {})
    except (OSError, KeyError, ValueError):
        answer = {}
    if answer.get("allow"):
        return json.dumps({"behavior": "allow", "updatedInput": input})
    return json.dumps({"behavior": "deny", "message": "The user denied this action."})


if __name__ == "__main__":
    mcp.run()
