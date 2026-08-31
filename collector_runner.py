#!/usr/bin/env python3
"""Append-only collector runner and run-receipt ledger.

This module provides a persistent, crash-safe way to drive the research
scanner collectors (V1, V2, V3). Every completed cycle appends exactly one
JSON line to an append-only receipt file. Prior receipts are never rewritten
or truncated, so the ledger survives process and host restarts.

The runner is the default entry point for the ``research-scanner-live``
(hourly V1 then V2, sequential and locked) and ``research-scanner-v3`` (daily)
systemd workloads on the research VM.

Research-only: no order placement, no credentials, no trading.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

REPO_DIR = Path(__file__).resolve().parent


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_git_sha(repo_dir: Path = REPO_DIR) -> str:
    """Return the current Git SHA of the repo, or 'unknown' if unavailable."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def config_hash(workload: str, **config: object) -> str:
    """Deterministic hash of the effective configuration for a workload."""
    payload = json.dumps(
        {"workload": workload, **config},
        sort_keys=True, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _acquire_lock(lock_path: Path) -> object:
    """Acquire an exclusive advisory lock, returning an object with a release().

    Uses ``fcntl.flock`` on POSIX (the deployment target). On platforms where
    fcntl is unavailable (local development, tests) it falls back to a
    directory-based ``mkdir`` lock which is still mutually exclusive per host.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        _fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)

        class _Flock:
            def release(self) -> None:  # noqa: D401
                try:
                    fcntl.flock(_fd, fcntl.LOCK_UN)
                finally:
                    os.close(_fd)

        fcntl.flock(_fd, fcntl.LOCK_EX)
        return _Flock()
    except ImportError:
        # Non-POSIX fallback: mkdir is atomic on the same filesystem.
        lock_dir = lock_path.with_suffix(lock_path.suffix + ".d")
        deadline = time.time() + 300
        while True:
            try:
                os.mkdir(lock_dir)
                break
            except FileExistsError:
                if time.time() > deadline:
                    raise TimeoutError(f"timed out waiting for lock {lock_dir}")
                time.sleep(0.5)

        class _MkdirLock:
            def release(self) -> None:
                try:
                    os.rmdir(lock_dir)
                except OSError:
                    pass

        return _MkdirLock()


def read_receipts(receipt_file: Path) -> List[Dict[str, object]]:
    """Read all complete, valid JSON receipt lines (ignoring a trailing partial)."""
    if not receipt_file.exists():
        return []
    receipts: List[Dict[str, object]] = []
    with open(receipt_file, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for line in lines:
        try:
            receipts.append(json.loads(line))
        except json.JSONDecodeError:
            # A partial last line from a crash is ignored but not deleted.
            continue
    return receipts


def append_receipt(receipt_file: Path, receipt: Dict[str, object]) -> None:
    """Append one JSON receipt line to the append-only ledger.

    Opens in append mode so existing lines are never rewritten or truncated.
    """
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    with open(receipt_file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _run_command(cmd: List[str], cwd: Path, out_log: Path,
                 timeout_s: Optional[int]) -> Dict[str, object]:
    """Run a scanner subprocess, capturing stdout/stderr to a log file.

    Returns a minimal result dict (ok, exit_code, output, error).
    """
    out_log.parent.mkdir(parents=True, exist_ok=True)
    started = utc_now_iso()
    try:
        with open(out_log, "ab") as log_fh:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "exit_code": proc.returncode,
            "output": str(out_log),
            "error": "" if ok else f"exit {proc.returncode}",
            "started": started,
            "finished": utc_now_iso(),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": None,
            "output": str(out_log),
            "error": f"timeout after {timeout_s}s",
            "started": started,
            "finished": utc_now_iso(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "exit_code": None,
            "output": str(out_log),
            "error": f"{type(exc).__name__}: {exc}",
            "started": started,
            "finished": utc_now_iso(),
        }


def _count_pattern(log_text: str, pattern: str) -> int:
    return log_text.count(pattern)


def _run_live(max_scan: int, receipts_dir: Optional[Path],
              python: str = "python3", run_dir: Optional[Path] = None) -> Dict[str, object]:
    """Run V1 then V2 sequentially; return a single cycle result."""
    run_dir = run_dir or receipts_dir or (REPO_DIR / "results" / "live")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cycle_dir = run_dir / f"cycle_{ts}"
    cycle_dir.mkdir(parents=True, exist_ok=True)

    v1 = _run_command([python, "binance_scanner_v1.py", "--max-scan", str(max_scan)],
                      REPO_DIR, cycle_dir / "v1.log", timeout_s=3600)
    v2 = _run_command([python, "binance_scanner_v2.py", "--max-scan", str(max_scan)],
                      REPO_DIR, cycle_dir / "v2.log", timeout_s=3600)

    v1_log = ""
    v2_log = ""
    try:
        v1_log = (cycle_dir / "v1.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    try:
        v2_log = (cycle_dir / "v2.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    result = {
        "workload": "live",
        "v1": v1,
        "v2": v2,
        "v1_v2_result_count": _count_pattern(v1_log, "V1_RESULT"),
        "v2_v2_result_count": _count_pattern(v2_log, "V2_RESULT"),
        "cycle_dir": str(cycle_dir),
    }
    if not (v1.get("ok") and v2.get("ok")):
        result["error"] = " ".join(filter(None, [v1.get("error"), v2.get("error")])) or "unknown"
    return result


def _run_v3(symbols: str, start: str, end: str, receipts_dir: Optional[Path],
            python: str = "python3", run_dir: Optional[Path] = None) -> Dict[str, object]:
    """Run V3 for symbols over a date range; return a single run result."""
    run_dir = run_dir or receipts_dir or (REPO_DIR / "results" / "v3")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cycle_dir = run_dir / f"run_{ts}"
    cycle_dir.mkdir(parents=True, exist_ok=True)

    cmd = [python, "binance_scanner_v3.py",
           "--symbols", symbols, "--start", start, "--end", end]
    v3 = _run_command(cmd, REPO_DIR, cycle_dir / "v3.log", timeout_s=7200)

    v3_log = ""
    try:
        v3_log = (cycle_dir / "v3.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    result = {
        "workload": "v3",
        "v3": v3,
        "v3_event_count": _count_pattern(v3_log, "V3_EVENT"),
        "v3_skip_count": _count_pattern(v3_log, "V3_SKIP"),
        "cycle_dir": str(cycle_dir),
    }
    if not v3.get("ok"):
        result["error"] = v3.get("error") or "unknown"
    return result


def run_cycle(workload: str, invoke: Callable[[], Dict[str, object]],
              lock_path: Path, receipt_file: Path,
              **config: object) -> Dict[str, object]:
    """Run a workload under an exclusive lock and append one receipt line.

    Returns the full receipt dict (also written to ``receipt_file``).
    """
    run_id = uuid.uuid4().hex[:12]
    receipt: Dict[str, object] = {
        "workload": workload,
        "run_id": run_id,
        "git_sha": current_git_sha(),
        "config_hash": config_hash(workload, **config),
        "utc_start": utc_now_iso(),
        "result": "running",
    }
    lock = _acquire_lock(lock_path)
    try:
        detail = invoke()
        receipt.update(detail)
        receipt["utc_end"] = utc_now_iso()
        start_parsed = datetime.strptime(receipt["utc_start"], "%Y-%m-%dT%H:%M:%SZ")
        end_parsed = datetime.strptime(receipt["utc_end"], "%Y-%m-%dT%H:%M:%SZ")
        receipt["duration_s"] = int((end_parsed - start_parsed).total_seconds())
        ok = detail.get("v1", {}).get("ok", False) and detail.get("v2", {}).get("ok", False) \
            if workload == "live" else detail.get("v3", {}).get("ok", False)
        receipt["result"] = "success" if ok else "failed"
        receipt.pop("error", None) if ok else None
        if not ok and "error" not in receipt:
            receipt["error"] = "unknown"
    finally:
        lock.release()
    append_receipt(receipt_file, receipt)
    return receipt


def _entry_live(args: argparse.Namespace) -> int:
    receipts_dir = Path(args.receipts_dir)
    receipt_file = receipts_dir / "live.jsonl"
    lock_path = receipts_dir / "live.lock"
    receipt = run_cycle(
        "live",
        lambda: _run_live(args.max_scan, receipts_dir, python=args.python),
        lock_path, receipt_file, max_scan=args.max_scan,
    )
    print(json.dumps(receipt, sort_keys=True, default=str))
    return 0 if receipt["result"] == "success" else 1


def _entry_v3(args: argparse.Namespace) -> int:
    receipts_dir = Path(args.receipts_dir)
    receipt_file = receipts_dir / "v3.jsonl"
    lock_path = receipts_dir / "v3.lock"
    receipt = run_cycle(
        "v3",
        lambda: _run_v3(args.symbols, args.start, args.end, receipts_dir, python=args.python),
        lock_path, receipt_file, symbols=args.symbols, start=args.start, end=args.end,
    )
    print(json.dumps(receipt, sort_keys=True, default=str))
    return 0 if receipt["result"] == "success" else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append-only collector runner (research-only)."
    )
    parser.add_argument("--python", default=sys.executable or "python3",
                        help="Python interpreter used to launch scanner subprocesses")
    parser.add_argument("--receipts-dir",
                        default=str(REPO_DIR / "data" / "run_receipts"),
                        help="Directory for append-only receipt ledgers and locks")
    sub = parser.add_subparsers(dest="command", required=True)

    live = sub.add_parser("live", help="Run V1 then V2 sequentially (hourly)")
    live.add_argument("--max-scan", type=int, default=20)
    live.set_defaults(func=_entry_live)

    v3 = sub.add_parser("v3", help="Run V3 over a symbol/date range (daily)")
    v3.add_argument("--symbols", default="BTCUSDT")
    v3.add_argument("--start")
    v3.add_argument("--end")
    v3.set_defaults(func=_entry_v3)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
