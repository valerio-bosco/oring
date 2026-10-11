---
name: oring-panel-check
description: Visually verify oring's Quickshell panel (ui/shell.qml) on the real screen with the dev instance and screenshots. Use after any change to the panel, its strings, its colors, or to the events it renders (append/extend/permission/state).
---

# Panel check

## Load test (automatic)

`scripts/check full` loads the panel against a dead socket and fails on QML errors. Run it first.
For a quick loop: `ORING_SOCKET=/tmp/none quickshell -p ui/shell.qml` and read the output
(`is not a type`, `ReferenceError`, `TypeError`, `Cannot assign` are errors).

## Visual check

1. `scripts/dev start`, then `scripts/dev toggle` to show the dev panel. The installed panel may
   also be open; the dev one is the one you toggled (close the installed one with `oring close`).
2. Produce the state to check (e.g. `scripts/dev oring send --auto deny "..."` in the background
   for a permission card), then `scripts/dev shot /tmp/panel.png` and **look at the image**.
3. Checklist:
   - header: status dot (grey idle, pulsing while busy, red offline), agent name + ▾, status text,
     "Nuova chat"/"Ferma" button;
   - agent menu opens on click and lists every configured agent with its type;
   - user bubbles right, agent bubbles left with markdown rendered, tool lines with ⚙, info in
     italics, errors in red;
   - permission card above the input with one button per option, allow buttons accented;
   - empty state text; offline text after `scripts/dev stop` (daemon only) and recovery within
     ~2 s after starting it again (reconnection, MEMORY G1);
   - colors follow the serpantinum theme (change wallpaper/theme if the change is about colors);
   - language matches the config (`language = "it"` / `"en"`).
4. `scripts/dev toggle` to hide, `scripts/dev stop`.

Screenshots: grim uses logical pixels (MEMORY G11); `scripts/dev shot` computes the panel area.
Do not click on the screen to test: the user is working on it. Drive everything through the
daemon (`scripts/dev oring …`) and IPC (`scripts/dev toggle`).

## Done when

Load test green and the checklist items touched by the change look right in the screenshot.
Mention what you looked at in the summary.
