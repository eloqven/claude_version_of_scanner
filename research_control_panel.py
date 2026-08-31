#!/usr/bin/env python3
"""VM-local research control panel (terminal TUI).

A curses control panel for the research scanner services on the Azure VM. It
provides three main views (plus a report view) so a single service can be
brought up, watched, and monitored one at a time:

  [1] LIVE   - manage research-scanner-live (hourly V1 -> V2)
  [2] V3     - manage research-scanner-v3  (daily archive V3)
  [3] MONITOR- live RAM/CPU/RSS, receipt ledger, journal, snapshot capture
  [4] REPORT - evidence summary for the confirmation-gated cutover

This panel is research-only: it controls local systemd services and reads
local monitoring data. It never touches trading, credentials, the recovery
agent, or the archive/DB research data.

Monitoring snapshots this panel records are the only auto-expiring data: they
are purged after a retention window (default 48h, min 1h, max 72h) so the VM
disk is not bloated with useless monitoring history. Archive data and scanner
databases are never auto-deleted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Paths (VM deployment defaults; overridable via env for tests)
# --------------------------------------------------------------------------- #

VM_ROOT = Path(os.environ.get("RESEARCH_PANEL_ROOT",
                              "/home/andrei/agent/projects/claude-scanner-cloud"))
RECEIPTS_DIR = Path(os.environ.get(
    "RESEARCH_PANEL_RECEIPTS",
    "/home/andrei/agent/data/research-collectors/current/scanner"))
PANEL_DIR = RECEIPTS_DIR / "panel"
CONFIG_PATH = Path(os.environ.get("RESEARCH_PANEL_CONFIG", str(PANEL_DIR / "panel.json")))
MONITOR_DB = Path(os.environ.get("RESEARCH_PANEL_DB", str(PANEL_DIR / "monitor.db")))
OVERRIDE_DIR = Path(os.environ.get("RESEARCH_PANEL_OVERRIDES",
                                   str(PANEL_DIR / "overrides")))

RETENTION_MIN_H = 1
RETENTION_MAX_H = 72
RETENTION_DEFAULT_H = 48

SERVICES = {
    "live": "research-scanner-live.service",
    "v3": "research-scanner-v3.service",
}

# Override env files written by the panel and consumed by the wrappers via
# EnvironmentFile on the unit.
OVERRIDE_FILES = {
    "live": OVERRIDE_DIR / "live.env",
    "v3": OVERRIDE_DIR / "v3.env",
}

# Defaults mirror the wrappers' own env defaults.
LIVE_DEFAULTS = {"RESEARCH_SCANNER_MAX_SCAN": "20", "RESEARCH_SCANNER_LIVE_SLEEP_S": "3600"}
V3_DEFAULTS = {
    "RESEARCH_SCANNER_V3_SYMBOLS": "BTCUSDT",
    "RESEARCH_SCANNER_V3_START": "2026-06-01",
    "RESEARCH_SCANNER_V3_END": "2026-08-31",
    "RESEARCH_SCANNER_V3_SLEEP_S": "21600",
}


@dataclass
class PanelConfig:
    retention_hours: int = RETENTION_DEFAULT_H

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "PanelConfig":
        cfg = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cfg.retention_hours = _bounded_retention(int(raw.get("retention_hours",
                                                                 cfg.retention_hours)))
        except Exception:
            pass
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"retention_hours": self.retention_hours},
                                   indent=2), encoding="utf-8")


def _bounded_retention(hours: int) -> int:
    return max(RETENTION_MIN_H, min(RETENTION_MAX_H, int(hours)))


def parse_retention_hours(text: str) -> int:
    """Parse user input like '48' or '2h' into bounded hours (1..72)."""
    m = re.search(r"(\d+)", text)
    if not m:
        raise ValueError(f"expected a number of hours, got {text!r}")
    return _bounded_retention(int(m.group(1)))


# --------------------------------------------------------------------------- #
# systemd control layer
# --------------------------------------------------------------------------- #

def run_cmd(cmd: List[str], timeout_s: int = 20) -> Tuple[int, str, str]:
    """Run a command, returning (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout_s}s"
    except Exception as exc:  # noqa: BLE001
        return -1, "", f"{type(exc).__name__}: {exc}"


def service_enabled(unit: str) -> bool:
    _, out, _ = run_cmd(["systemctl", "is-enabled", unit])
    return "enabled" in out


def service_active(unit: str) -> bool:
    _, out, _ = run_cmd(["systemctl", "is-active", unit])
    return "active" in out


def service_status(unit: str) -> Dict[str, str]:
    """Return a compact status dict for a unit (best-effort)."""
    result = {"unit": unit, "active": "unknown", "enabled": "unknown",
              "pid": "—", "memory": "—", "exec": "—"}
    _, out, _ = run_cmd(["systemctl", "show", unit, "-p", "ActiveState", "-p",
                         "ExecMainPID", "-p", "MemoryCurrent", "-p", "ExecStart"])
    for field, key in [("ActiveState", "active"), ("ExecMainPID", "pid"),
                       ("MemoryCurrent", "memory"), ("ExecStart", "exec")]:
        for line in out.splitlines():
            if line.startswith(f"{field}="):
                result[key] = line.split("=", 1)[1]
    result["enabled"] = "enabled" if service_enabled(unit) else "disabled"
    return result


def set_services(unit: str, action: str) -> Tuple[int, str]:
    """Apply a systemd action (enable --now, start, stop, disable, restart,
    reset-failed). Uses sudo only where required; falls back to sudo otherwise."""
    cmds: Dict[str, List[str]] = {
        "enable": ["sudo", "systemctl", "enable", "--now", unit],
        "start": ["sudo", "systemctl", "start", unit],
        "stop": ["sudo", "systemctl", "stop", unit],
        "disable": ["sudo", "systemctl", "disable", unit],
        "restart": ["sudo", "systemctl", "restart", unit],
        "reset-failed": ["sudo", "systemctl", "reset-failed", unit],
    }
    cmd = cmds.get(action)
    if cmd is None:
        return -1, f"unknown action {action!r}"
    code, out, err = run_cmd(cmd)
    msg = (err or out).strip()
    return code, msg or f"{action} returned {code}"


# --------------------------------------------------------------------------- #
# Receipt ledger
# --------------------------------------------------------------------------- #

def latest_receipt(workload: str) -> Optional[dict]:
    """Return the most recent complete receipt for a workload, or None."""
    path = RECEIPTS_DIR / f"{workload}.jsonl"
    if not path.exists():
        return None
    last: Optional[dict] = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            last = json.loads(line)
        except json.JSONDecodeError:
            continue
    return last


def read_receipts(workload: str, limit: int = 5) -> List[dict]:
    path = RECEIPTS_DIR / f"{workload}.jsonl"
    if not path.exists():
        return []
    out: List[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:]


# --------------------------------------------------------------------------- #
# Overrides / wrapper configuration
# --------------------------------------------------------------------------- #

def read_overrides_at(path: Path, defaults: Dict[str, str]) -> Dict[str, str]:
    """Read effective wrapper env from an override file, merged over defaults."""
    effective = dict(defaults)
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            effective[key.strip()] = value.strip().strip('"')
    return effective


def write_overrides_at(path: Path, values: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Research scanner wrapper overrides (managed by control panel).",
             "# Values apply on the next service restart.",
             ""]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_overrides(workload: str) -> Dict[str, str]:
    defaults = LIVE_DEFAULTS if workload == "live" else V3_DEFAULTS
    return read_overrides_at(OVERRIDE_FILES[workload], defaults)


def write_overrides(workload: str, values: Dict[str, str]) -> None:
    write_overrides_at(OVERRIDE_FILES[workload], values)


# --------------------------------------------------------------------------- #
# Monitoring snapshot store (auto-expiring metadata only)
# --------------------------------------------------------------------------- #

# The monitor snapshot history is the ONLY auto-expiring data: it is purged
# after the retention window. Archive data and scanner databases are never
# auto-deleted (per the plan: "never auto-delete research data").


def purge_expired(conn: sqlite3.Connection, retention_hours: int,
                  now: Optional[datetime] = None) -> int:
    """Delete snapshots older than ``retention_hours``; return rows deleted."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=retention_hours)
    cur = conn.execute("DELETE FROM monitor_snapshots WHERE ts < ?",
                       (cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),))
    conn.commit()
    return cur.rowcount


def record_snapshot(conn: sqlite3.Connection, snapshot: dict, retention_hours: int) -> None:
    conn.execute(
        "INSERT INTO monitor_snapshots (ts, ram_total_mb, ram_used_mb, ram_avail_mb,"
        " cpu_pct, procs_json, services_json, receipts_json, retention_h)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot["ts"], snapshot["ram_total_mb"], snapshot["ram_used_mb"],
         snapshot["ram_avail_mb"], snapshot["cpu_pct"],
         json.dumps(snapshot.get("procs", [])),
         json.dumps(snapshot.get("services", {})),
         json.dumps(snapshot.get("receipts", {})),
         retention_hours),
    )
    conn.commit()


@dataclass
class MonitorSnapshot:
    ts: str
    ram_total_mb: int
    ram_used_mb: int
    ram_avail_mb: int
    cpu_pct: float
    procs: List[dict]
    services: Dict[str, str]
    receipts: Dict[str, object]


def read_snapshots(conn: sqlite3.Connection, limit: int = 10) -> List[MonitorSnapshot]:
    rows = conn.execute(
        "SELECT ts, ram_total_mb, ram_used_mb, ram_avail_mb, cpu_pct,"
        " procs_json, services_json, receipts_json"
        " FROM monitor_snapshots ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
    snapshots = []
    for row in rows:
        snapshots.append(MonitorSnapshot(
            ts=row[0], ram_total_mb=row[1], ram_used_mb=row[2], ram_avail_mb=row[3],
            cpu_pct=row[4], procs=json.loads(row[5] or "[]"),
            services=json.loads(row[6] or "{}"), receipts=json.loads(row[7] or "{}"),
        ))
    return snapshots


def open_monitor_db(path: Path = MONITOR_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS monitor_snapshots ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT NOT NULL,"
        " ram_total_mb INTEGER,"
        " ram_used_mb INTEGER,"
        " ram_avail_mb INTEGER,"
        " cpu_pct REAL,"
        " procs_json TEXT,"
        " services_json TEXT,"
        " receipts_json TEXT,"
        " retention_h INTEGER)")
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# Monitoring data collection (reads only; never writes archive/DB research data)
# --------------------------------------------------------------------------- #

def _parse_free(text: str) -> Tuple[int, int, int, int]:
    total = used = avail = 0
    for line in text.splitlines():
        if line.lower().startswith("mem:"):
            parts = line.split()
            # free -m: Mem: total used free shared buff/cache available
            if len(parts) >= 7:
                total, used, avail = int(parts[1]), int(parts[2]), int(parts[6])
    return total, used, avail, (total - used)


def collect_monitor(capture_receipts: bool = True) -> MonitorSnapshot:
    code, out, _ = run_cmd(["free", "-m"])
    total = used = avail = 0
    if code == 0:
        total, used, avail, _ = _parse_free(out)

    # CPU: short vmstat sample for idle; cpu_pct = 100 - idle
    cpu_pct = 0.0
    code2, out2, _ = run_cmd(["vmstat", "1", "2"], timeout_s=5)
    if code2 == 0:
        lines = out2.strip().splitlines()
        if len(lines) >= 3:
            idle = float(lines[-1].split()[14])
            cpu_pct = round(100.0 - idle, 1)

    # Top memory processes (by RSS), excluding this panel
    procs = []
    code3, out3, _ = run_cmd(
        ["ps", "-eo", "pid,rss,comm", "--sort=-rss"], timeout_s=10)
    if code3 == 0:
        for line in out3.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid, rss_kb, comm = parts[0], parts[1], parts[2]
            rss_mb = int(rss_kb) // 1024
            procs.append({"pid": pid, "rss_mb": rss_mb, "comm": comm})
        procs = procs[:8]

    services = {name: service_status(unit)["active"]
                for name, unit in SERVICES.items()}

    receipts = {}
    if capture_receipts:
        for wl in ("live", "v3"):
            last = latest_receipt(wl)
            receipts[wl] = last or {"result": "none"}

    return MonitorSnapshot(
        ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ram_total_mb=total, ram_used_mb=used, ram_avail_mb=avail, cpu_pct=cpu_pct,
        procs=procs, services=services, receipts=receipts,
    )


def journal_tail(unit: str, lines: int = 30) -> List[str]:
    code, out, err = run_cmd(["sudo", "journalctl", "-u", unit, "-n", str(lines),
                              "--no-pager"], timeout_s=15)
    text = (out or err) if code == 0 else f"[journal unavailable: {err}]"
    return text.rstrip().splitlines()[-lines:]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> int:
    # The curses UI is imported lazily so the logic module stays importable
    # and unit-testable without a terminal.
    from research_control_panel_ui import run_ui
    return run_ui()


if __name__ == "__main__":
    sys.exit(main())
