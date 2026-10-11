"""The commit-msg hook enforcing Conventional Commits."""

import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".githooks" / "commit-msg"


def run(tmp_path, message):
    f = tmp_path / "MSG"
    f.write_text(message)
    return subprocess.run(["bash", str(HOOK), str(f)], capture_output=True, text=True).returncode


@pytest.mark.parametrize("message", [
    "feat(mcp): capture a window even when the screen is locked",
    "fix: close client writers on shutdown\n\nwait_closed() waited forever.\n",
    "docs(skills): describe the release procedure",
    "refactor(daemon)!: rename the gate operation\n\nBREAKING CHANGE: clients must send op=check",
    "chore(nix): bump nixpkgs\n# a comment git adds\n",
    "Merge branch 'main' of github.com:valerio-bosco/oring",
    "fixup! feat(ui): agent menu",
])
def test_accepted(tmp_path, message):
    assert run(tmp_path, message) == 0


@pytest.mark.parametrize("message", [
    "Add project tooling",                                  # no type
    "feature(ui): agent menu",                              # unknown type
    "feat(UI): agent menu",                                 # scope not lowercase
    "feat(ui):agent menu",                                  # no space
    "feat(ui): Agent menu",                                 # capitalized summary
    "feat(ui): agent menu.",                                # trailing period
    "feat(ui): " + "x" * 70,                                # too long
    "feat(ui): agent menu\nbody right after the subject",   # no blank line
])
def test_rejected(tmp_path, message):
    assert run(tmp_path, message) == 1
