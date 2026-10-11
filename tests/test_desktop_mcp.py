"""Desktop MCP server functions, with fake hyprctl/grim/wlrctl on PATH: lock guard, capture targets."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
import desktop_mcp  # noqa: E402

WINDOW = {"address": "0xabc", "class": "code", "title": "Editor", "mapped": True, "workspace": {"id": 3},
          "at": [100, 50], "size": [800, 600], "stableId": "18000006"}

FAKE_HYPRCTL = """#!/bin/sh
case "$*" in
  "-j monitors") if [ -n "$FAKE_LOCKED" ]; then b='["LOCK","WINDOWED"]'; else b='["WINDOWED"]'; fi
                 echo '[{"x":0,"y":0,"width":3440,"height":1440,"focused":true,"solitaryBlockedBy":'"$b"'}]' ;;
  "-j clients") echo '[__WINDOW__]' ;;
  "-j activewindow") echo '__WINDOW__' ;;
  *) echo ok ;;
esac
"""

FAKE_TOOL = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_TOOLS_LOG"
for last; do :; done
case "$(basename "$0")" in grim) printf 'png' > "$last" ;; esac
"""


@pytest.fixture
def fake_desktop(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "hyprctl").write_text(FAKE_HYPRCTL.replace("__WINDOW__", json.dumps(WINDOW)))
    for tool in ("grim", "wlrctl", "wtype"):
        (bin_dir / tool).write_text(FAKE_TOOL)
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "tools.log"
    log.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TOOLS_LOG", str(log))
    monkeypatch.delenv("ORING_SOCKET", raising=False)       # no confirmation gate here
    monkeypatch.delenv("FAKE_LOCKED", raising=False)

    class Desktop:
        def lock(self):
            monkeypatch.setenv("FAKE_LOCKED", "1")

        def calls(self):
            return log.read_text().splitlines()

    return Desktop()


def test_lock_detection(fake_desktop):
    assert desktop_mcp._schermo_bloccato() is False
    fake_desktop.lock()
    assert desktop_mcp._schermo_bloccato() is True


@pytest.mark.parametrize("tool, args", [
    ("clicca", {"x": 10, "y": 20}), ("scorri", {}), ("muovi_mouse", {"x": 1, "y": 1}),
    ("scrivi_testo", {"testo": "hunter2"}), ("premi_tasti", {"combinazione": "invio"})])
def test_mouse_and_keyboard_refused_when_locked(fake_desktop, tool, args):
    fake_desktop.lock()
    with pytest.raises(RuntimeError, match="bloccato"):
        getattr(desktop_mcp, tool)(**args)
    assert not any(c.startswith(("wlrctl", "wtype")) for c in fake_desktop.calls())


def test_mouse_works_when_unlocked(fake_desktop):
    desktop_mcp.clicca(x=10, y=20)
    assert any(c.startswith("wlrctl pointer click") for c in fake_desktop.calls())


def test_window_capture_uses_toplevel(fake_desktop):
    _, note = desktop_mcp.screenshot(finestra="code")
    assert fake_desktop.calls()[-1].startswith("grim -T 18000006 -s 0.6")
    assert "code — Editor" in note and "+ 100" in note and "+ 50" in note


def test_window_capture_works_when_locked(fake_desktop):
    fake_desktop.lock()
    desktop_mcp.screenshot(cosa="finestra")                # active window
    desktop_mcp.screenshot(finestra="0xabc")
    assert [c.split()[:3] for c in fake_desktop.calls()] == [["grim", "-T", "18000006"]] * 2


@pytest.mark.parametrize("cosa", ["schermo", "area"])
def test_screen_capture_refused_when_locked(fake_desktop, cosa):
    fake_desktop.lock()
    with pytest.raises(RuntimeError, match="parametro finestra"):
        desktop_mcp.screenshot(cosa=cosa, x=0, y=0, larghezza=10, altezza=10)
    assert not any(c.startswith("grim") for c in fake_desktop.calls())


def test_screen_capture_when_unlocked(fake_desktop):
    desktop_mcp.screenshot(cosa="area", x=5, y=6, larghezza=70, altezza=80)
    desktop_mcp.screenshot()
    assert fake_desktop.calls() == ["grim -g 5,6 70x80 -s 0.6 " + fake_desktop.calls()[0].split()[-1],
                                    "grim -g 0,0 3440x1440 -s 0.4 " + fake_desktop.calls()[1].split()[-1]]


def test_launching_apps_goes_through_the_gate(fake_desktop, monkeypatch, tmp_path):
    # avvia_applicazione runs any command: with oring's socket set and no daemon answering, it is denied
    monkeypatch.setenv("ORING_SOCKET", str(tmp_path / "nobody.sock"))
    with pytest.raises(PermissionError):
        desktop_mcp.avvia_applicazione(comando="rm -rf ~")
    assert fake_desktop.calls() == []


def test_tools_keep_their_signatures():
    # the decorators must not hide the parameters from FastMCP (the agents see them)
    tools = {t.name: t for t in desktop_mcp.mcp._tool_manager.list_tools()}
    assert set(tools["clicca"].parameters["properties"]) == {"x", "y", "tasto", "doppio"}
    assert "finestra" in tools["screenshot"].parameters["properties"]
    assert "finestra" in tools["leggi_testo"].parameters["properties"]
