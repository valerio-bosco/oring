---
name: oring-release
description: Finish a development step in oring - full pipeline, plan and memory update, focused commit, and (when asked) deployment to the maintainer's NixOS machine. Use when a step from docs/PLAN.md is implemented and verified, or the user asks to commit/ship oring changes.
---

# Release a step

1. **Pipeline**: `scripts/check full` must be green. Do not commit on red; do not skip tests.
2. **Plan**: tick the step in `docs/PLAN.md` and add `→ YYYY-MM-DD, <what was verified>`. If the
   step was split or changed, say so there; new ideas go to the Backlog.
3. **Memory**: in `docs/MEMORY.md` add decisions (D…) and gotchas (G…) learned in this step, fix
   entries that became wrong, append a session-log line (date, what changed, how it was verified).
4. **Docs**: README/`config.example.toml` if user-visible behaviour or options changed; the
   protocol docstring in `oring/daemon.py` if the socket protocol changed; `AGENTS.md` if the
   structure, commands or rules changed.
5. **Commit** in the oring repo (as the repo owner if you are root: `runuser -u voodoo -- git …`):
   focused, imperative summary, body with the why and the verification. Show `git status` clean.
   Never push — the maintainer pushes.
6. **Deploy (only when the user asks)** on the maintainer's machine:
   ```sh
   sudo nix flake update oring --flake /etc/nixos
   nix build --no-link /etc/nixos#nixosConfigurations.nixos.config.system.build.toplevel
   ```
   then ask the user to run `sudo nixos-rebuild switch --flake /etc/nixos#nixos`; afterwards check
   `systemctl --user status oring oring-ui` and commit `/etc/nixos/flake.lock` there with a message
   naming the oring commit. Never run the rebuild or push yourself.

## Done when

Pipeline green, PLAN and MEMORY updated, commit made, working tree clean; deployment status stated
explicitly ("not deployed" or "deployed, waiting for rebuild").
