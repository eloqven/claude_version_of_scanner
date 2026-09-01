# Polish Log — research control panel TUI (flicker)

- target: `research_control_panel_ui.py` (curses TUI, runs on VM only)
- focus: performance / interaction (eliminate flicker over SSH)
- status: committed `77048d3` on `deploy/cloud-scanner-20260831`

## Iteration 1 — dirty-triggered repaint

**Before:** `_main` loop called `panel.draw()` every 200ms poll, and every
`draw()` ran `stdscr.erase()` + a full header/menu/content/status/hints
paint with **two** `refresh()` calls (content window + stdscr). New
`curses.newwin(...)` created every frame. Result: the whole screen was
re-blitted ~5x/sec even when nothing changed — a constant flicker over SSH.

**After:** `draw()` repaints only when something actually changed:
- a keypress (loop sets `panel._dirty = True` after `handle()`),
- a `KEY_RESIZE` or any actual window-size change (`getmaxyx` compared to
  the cached size),
- the header clock's minute (`_now_hhmm()[:5]`) boundary.

Idle `getch() == -1` ticks return without touching the screen. The content
window is created once and reused/resized, not reallocated per frame.
Status and hints lines are `ljust`-padded to the full width so a shorter
message/`msg` edit erases any prior residue on repaint.

**Effect:** idle output dropped from ~8 KB of emitted escape bytes per smoke
run to ~0.9 KB (one initial paint + one clock refresh, no idle storm). The
TUI is now silent when idle and repaints crisply only on interaction.

## Final state

Live/V3/Monitor/Report views, dialogs, service controls, and monitor
snapshot/retention flows unchanged in behavior; only the redraw policy
changed. 13 control-panel unit tests pass; VM compile + 5s curses smoke run
clean (0 errors, no `addnwstr`/Traceback).

## Learnings

- In a curses TUI, "flicker" is almost never about drawing too fast — it is
  about redrawing when nothing changed. Gating repaints on a dirty flag +
  clock boundary removes the blink without any fancy incremental-diff work;
  curses already diffs cells, so fewer full `refresh()` calls is what stops
  the SSH repaint storm.
- Reusing a single `newwin` for the body (resize on change) avoids window
  churn and the extra clearing that came with per-frame allocation.
