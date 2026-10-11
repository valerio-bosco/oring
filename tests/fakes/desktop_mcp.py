"""A fake desktop MCP server for the tests: the real confirmation gate, no desktop access.

elenca_finestre → "3 windows"; scrivi_appunti(testo) → goes through the gate of the real server
(_con_conferma) and appends the text to $FAKE_MCP_LOG.
"""

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp"))
from desktop_mcp import _con_conferma  # noqa: E402

mcp = FastMCP("desktop")


@mcp.tool()
def elenca_finestre() -> str:
    """List windows."""
    return "3 windows"


@mcp.tool()
@_con_conferma
def scrivi_appunti(testo: str) -> str:
    """Write the clipboard."""
    with open(os.environ["FAKE_MCP_LOG"], "a") as f:
        f.write(testo + "\n")
    return "ok"


if __name__ == "__main__":
    mcp.run()
