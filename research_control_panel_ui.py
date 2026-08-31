#!/usr/bin/env python3
"""Curses UI for the research control panel (runs on the VM only)."""

from __future__ import annotations

import curses
import json
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import research_control_panel as ctl


def _now_hhmm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%SZ")


def _safe_add(win, y: int, x: int, text: str, maxw: int, attr: int = 0) -> None:
    """Write text defensively so a small/resized terminal never crashes."""
    if y < 0 or x < 0:
        return
    try:
        wh, ww = win.getmaxyx()
        if y >= wh or x >= ww:
            return
        room = ww - x
        if room <= 0:
            return
        win.addnstr(y, x, text[: min(maxw, room)], min(maxw, room), attr)
    except Exception:
        pass


class _Pane:
    """Helper for writing bounded text with word-wrap prevention."""

    def __init__(self, win, h: int, w: int) -> None:
        self.win = win
        self.h = h
        self.w = w
        self.row = 0

    def line(self, text: str = "", attr: int = 0) -> None:
        if self.row >= self.h:
            return
        _safe_add(self.win, self.row, 0, text, self.w, attr)
        self.row += 1

    def blank(self) -> None:
        for r in range(self.row, self.h):
            _safe_add(self.win, r, 0, " " * self.w, self.w)


class _Panel:
    def __init__(self, stdscr) -> None:
        self.stdscr = stdscr
        self.view = "live"
        self.msg = ""
        self.monitor_db = ctl.open_monitor_db()
        self.config = ctl.PanelConfig.load()
        self.last_snapshot: Optional[ctl.MonitorSnapshot] = None
        self.in_dialog: Optional[Tuple[str, str]] = None  # (prompt, buf)
        self.edits: Dict[str, str] = {}

    # -- helpers ------------------------------------------------------------- #

    def _status(self, msg: str) -> None:
        self.msg = msg

    def _run(self, unit: str, action: str) -> None:
        code, msg = ctl.set_services(unit, action)
        self._status(f"{action} {unit}: {msg}")

    # -- views --------------------------------------------------------------- #

    def draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 12 or w < 60:
            _safe_add(self.stdscr, 0, 0,
                      f"Terminal too small ({w}x{h}); enlarge to >=60x12", w)
            self.stdscr.refresh()
            return

        # Header
        header = f" RESEARCH CONTROL PANEL  [VM]  {_now_hhmm()} UTC "
        _safe_add(self.stdscr, 0, 0, header, w - 1, curses.A_REVERSE)

        # Menu bar
        menu = " 1:LIVE   2:V3   3:MONITOR   4:REPORT   Q:quit "
        _safe_add(self.stdscr, 1, 0, menu, w - 1, curses.A_BOLD)

        # Content
        body_h = h - 4
        content = curses.newwin(body_h, w, 2, 0)
        content.erase()
        pane = _Pane(content, body_h, w)
        if self.in_dialog:
            self._draw_dialog(pane, body_h, w)
        elif self.view == "live":
            self._draw_live(pane)
        elif self.view == "v3":
            self._draw_v3(pane)
        elif self.view == "monitor":
            self._draw_monitor(pane)
        elif self.view == "report":
            self._draw_report(pane)
        pane.blank()
        content.refresh()

        # Status line
        _safe_add(self.stdscr, h - 2, 0, (" " + self.msg[: w - 2]) if self.msg else "",
                  w - 1, curses.A_DIM)

        # Key hints
        if self.view == "live":
            hints = " [e] enable+start  [s] start  [t] stop  [d] disable  [R] reset-failed  [c] max-scan  "
            hints += "[r] refresh  [m] latest receipt  "
        elif self.view == "v3":
            hints = " [e] enable+start  [s] start  [t] stop  [d] disable  [R] reset-failed  [c] config  "
            hints += "[r] refresh  [m] latest receipt  "
        elif self.view == "monitor":
            hints = " [c] capture snapshot  [r] refresh  [S] snapshots  [h] set retention(h)  "
        else:
            hints = " [r] refresh  [m] latest receipts  "
        _safe_add(self.stdscr, h - 1, 0, hints, w - 1)
        self.stdscr.refresh()

    def _service_block(self, pane: _Pane, name: str, unit: str,
                       status: Dict[str, str], override_label: str) -> None:
        pane.line(f"=== {name} ({unit}) ===", curses.A_BOLD)
        pane.line(f"  state      : {status['active']}  |  boot       : {status['enabled']}")
        pid = status.get("pid", "—")
        mem = status.get("memory", "—")
        if mem and mem != "—":
            try:
                mem = f"{(int(mem) / 1024):.0f} MiB"
            except ValueError:
                pass
        pane.line(f"  pid        : {pid}  |  memory     : {mem}")
        pane.line("")

    def _draw_live(self, pane: _Pane) -> None:
        unit = ctl.SERVICES["live"]
        st = ctl.service_status(unit)
        ov = ctl.read_overrides("live")
        self._service_block(pane, "LIVE SCANNER (hourly V1 -> V2)", unit, st, "max-scan")
        pane.line(f"  — override: max-scan={ov['RESEARCH_SCANNER_MAX_SCAN']}  "
                  f"cycle sleep={ov.get('RESEARCH_SCANNER_LIVE_SLEEP_S','3600')}s")
        pane.line("")
        rec = ctl.latest_receipt("live")
        pane.line("  last receipt:", curses.A_BOLD)
        if rec:
            pane.line(f"    {rec.get('utc_start','?')}  {rec.get('result','?')}  "
                      f"run={rec.get('run_id','?')}  dur={rec.get('duration_s','?')}s")
            v1 = rec.get("v1", {}); v2 = rec.get("v2", {})
            pane.line(f"    v1 ok={v1.get('ok')}  v2 ok={v2.get('ok')}  "
                      f"v2 results={rec.get('v2_v2_result_count','?')}  sha={rec.get('git_sha','?')[:7]}")
        else:
            pane.line("    (no receipt yet — service not run or failed)")

    def _draw_v3(self, pane: _Pane) -> None:
        unit = ctl.SERVICES["v3"]
        st = ctl.service_status(unit)
        ov = ctl.read_overrides("v3")
        self._service_block(pane, "V3 SCANNER (daily archive)", unit, st, "config")
        pane.line(f"  — override: symbols={ov['RESEARCH_SCANNER_V3_SYMBOLS']}  "
                  f"{ov.get('RESEARCH_SCANNER_V3_START','?')}..{ov.get('RESEARCH_SCANNER_V3_END','?')}")
        pane.line("")
        rec = ctl.latest_receipt("v3")
        pane.line("  last receipt:", curses.A_BOLD)
        if rec:
            pane.line(f"    {rec.get('utc_start','?')}  {rec.get('result','?')}  "
                      f"run={rec.get('run_id','?')}  dur={rec.get('duration_s','?')}s")
            pane.line(f"    events={rec.get('v3_event_count','?')}  skips={rec.get('v3_skip_count','?')}  "
                      f"sha={rec.get('git_sha','?')[:7]}")
        else:
            pane.line("    (no receipt yet)")

    def _draw_monitor(self, pane: _Pane) -> None:
        pane.line("=== LIVE MONITOR ===", curses.A_BOLD)
        snap = self.last_snapshot
        if snap:
            pane.line(f"  RAM: total={snap.ram_total_mb}MiB  used={snap.ram_used_mb}MiB  "
                      f"avail={snap.ram_avail_mb}MiB  cpu={snap.cpu_pct}%")
        else:
            pane.line("  (no snapshot yet — press [c] to capture)")
        pane.line("")
        pane.line("  services:", curses.A_BOLD)
        for name, unit in ctl.SERVICES.items():
            pane.line(f"    {name:6s} {ctl.service_status(unit)['active']}")
        if snap:
            pane.line("")
            pane.line("  top RSS procs:", curses.A_BOLD)
            for p in snap.procs[:6]:
                pane.line(f"    {p['rss_mb']:>6} MiB  {p['pid']:>7}  {p['comm'][:30]}")
        pane.line("")
        pane.line(f"  retention: {self.config.retention_hours}h  "
                  f"(min {ctl.RETENTION_MIN_H}h, max {ctl.RETENTION_MAX_H}h)", curses.A_BOLD)

    def _draw_report(self, pane: _Pane) -> None:
        pane.line("=== EVIDENCE / REPORT ===", curses.A_BOLD)
        snaps = ctl.read_snapshots(self.monitor_db, limit=3)
        pane.line(f"  stored snapshots: {len(snaps)}  "
                  f"(retention {self.config.retention_hours}h)")
        for name, unit in ctl.SERVICES.items():
            st = ctl.service_status(unit)
            pane.line(f"  service {name:6s}: active={st['active']} enabled={st['enabled']}")
        pane.line("")
        pane.line("  latest receipts:", curses.A_BOLD)
        for wl in ("live", "v3"):
            rec = ctl.latest_receipt(wl)
            if rec:
                pane.line(f"    {wl}: {rec.get('result','?')}  "
                          f"start={rec.get('utc_start','?')}  dur={rec.get('duration_s','?')}s  "
                          f"sha={str(rec.get('git_sha','?'))[:7]}")

    def _draw_dialog(self, pane: _Pane, h: int, w: int) -> None:
        prompt, buf = self.in_dialog
        pane.line(prompt, curses.A_BOLD)
        pane.line("")
        pane.line("  " + buf + "█")
        pane.line("")
        pane.line("  Enter: apply    Esc: cancel")

    # -- dialog -------------------------------------------------------------- #

    def ask(self, prompt: str, initial: str = "") -> None:
        self.in_dialog = (prompt, initial)

    def _commit_dialog(self) -> None:
        prompt, buf = self.in_dialog
        self.in_dialog = None
        if prompt.startswith("MAX-SCAN"):
            value = buf.strip()
            if value.isdigit():
                ov = ctl.read_overrides("live")
                ov["RESEARCH_SCANNER_MAX_SCAN"] = value
                ctl.write_overrides("live", ov)
                self._status(f"max-scan set to {value} (applies on restart)")
            else:
                self._status("invalid max-scan; cancelled")
        elif prompt.startswith("RETENTION"):
            try:
                hours = ctl.parse_retention_hours(buf)
                self.config.retention_hours = hours
                self.config.save()
                deleted = ctl.purge_expired(self.monitor_db, hours)
                self._status(f"retention set to {hours}h; purged {deleted} old snapshot(s)")
            except ValueError as exc:
                self._status(f"invalid retention: {exc}")
        elif prompt.startswith("V3 CONFIG"):
            self._apply_v3_config(buf)

    def _apply_v3_config(self, buf: str) -> None:
        # Accept: symbols,start,end[,sleep_s]
        parts = [p.strip() for p in buf.split(",")]
        if len(parts) < 3:
            self._status("v3 config format: SYMBOLS,START,END[,SLEEP_S]")
            return
        ov = ctl.read_overrides("v3")
        ov["RESEARCH_SCANNER_V3_SYMBOLS"] = parts[0]
        ov["RESEARCH_SCANNER_V3_START"] = parts[1]
        ov["RESEARCH_SCANNER_V3_END"] = parts[2]
        if len(parts) >= 4 and parts[3]:
            ov["RESEARCH_SCANNER_V3_SLEEP_S"] = parts[3]
        ctl.write_overrides("v3", ov)
        self._status(f"v3 config set: {', '.join(parts)} (applies on restart)")

    # -- input --------------------------------------------------------------- #

    def handle(self, key: int) -> None:
        if self.in_dialog:
            self._handle_dialog(key)
            return
        ch = chr(key) if 0 < key < 256 else ""
        if ch == "1":
            self.view = "live"; self._status("live view")
        elif ch == "2":
            self.view = "v3"; self._status("v3 view")
        elif ch == "3":
            self.view = "monitor"; self._status("monitor view")
        elif ch == "4":
            self.view = "report"; self._status("report view")
        elif ch in ("q", "Q"):
            raise KeyboardInterrupt
        elif self.view in ("live", "v3"):
            self._handle_service(ch)
        elif self.view == "monitor":
            self._handle_monitor(ch)

    def _handle_service(self, ch: str) -> None:
        wl = self.view
        unit = ctl.SERVICES[wl]
        actions = {"e": "enable", "s": "start", "t": "stop", "d": "disable",
                   "R": "reset-failed"}
        if ch in actions:
            self._run(unit, actions[ch])
        elif ch == "r":
            self._status(f"refreshed {wl}")
        elif ch == "m":
            rec = ctl.latest_receipt(wl)
            self._status(f"latest {wl} receipt: "
                         f"{rec.get('result') if rec else 'none'}")
        elif ch == "c" and wl == "live":
            ov = ctl.read_overrides("live")
            self.ask("MAX-SCAN (apply on next restart): ",
                     ov["RESEARCH_SCANNER_MAX_SCAN"])
        elif ch == "c" and wl == "v3":
            ov = ctl.read_overrides("v3")
            self.ask("V3 CONFIG (SYMBOLS,START,END[,SLEEP_S]): ",
                     f"{ov['RESEARCH_SCANNER_V3_SYMBOLS']},"
                     f"{ov['RESEARCH_SCANNER_V3_START']},"
                     f"{ov['RESEARCH_SCANNER_V3_END']},"
                     f"{ov.get('RESEARCH_SCANNER_V3_SLEEP_S','21600')}")

    def _handle_monitor(self, ch: str) -> None:
        if ch == "c":
            snap = ctl.collect_monitor()
            self.last_snapshot = snap
            ctl.record_snapshot(self.monitor_db, {
                "ts": snap.ts,
                "ram_total_mb": snap.ram_total_mb,
                "ram_used_mb": snap.ram_used_mb,
                "ram_avail_mb": snap.ram_avail_mb,
                "cpu_pct": snap.cpu_pct,
                "procs": [p.__dict__ for p in snap.procs],
                "services": snap.services,
                "receipts": snap.receipts,
            }, self.config.retention_hours)
            with self.monitor_db:  # noqa: SIM117
                deleted = ctl.purge_expired(self.monitor_db,
                                            self.config.retention_hours)
            self._status(f"snapshot captured {snap.ts}; purged {deleted} old")
        elif ch == "r":
            self._status("refreshed monitor")
        elif ch == "S":
            snaps = ctl.read_snapshots(self.monitor_db, limit=5)
            n = len(snaps)
            latest = snaps[0].ts if snaps else "—"
            self._status(f"{n} snapshots stored; latest {latest}")
        elif ch == "h":
            self.ask(f"RETENTION hours (1..72, default 48): ",
                     str(self.config.retention_hours))

    def _handle_dialog(self, key: int) -> None:
        prompt, buf = self.in_dialog
        if key == 27:  # ESC
            self.in_dialog = None
            self._status("cancelled")
        elif key in (10, 13):  # Enter
            self._commit_dialog()
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.in_dialog = (prompt, buf[:-1])
        elif 32 <= key < 127:
            self.in_dialog = (prompt, buf + chr(key))


def run_ui() -> int:
    try:
        curses.wrapper(_main)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"control panel error: {exc}", file=sys.stderr)
        return 1
    return 0


def _main(stdscr) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(200)
    panel = _Panel(stdscr)
    while True:
        panel.draw()
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue
        if key == -1:
            continue
        try:
            panel.handle(key)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            panel._status(f"error: {exc}")


if __name__ == "__main__":
    sys.exit(run_ui())
