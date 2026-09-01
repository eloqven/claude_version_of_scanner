"""Local web dashboard for scanner logs + notebook panel (stdlib only).

Run:
    python log_dashboard.py [--port 8666] [--logdir logs]

Endpoints:
    GET /                            HTML dashboard (Logs + Notebook tabs)
    GET /api/logs                    JSON list of *.log files
    GET /api/log?name=X&page=N&page_size=M[&q=text]   paginated lines
    GET /api/results?name=X&page=N&page_size=M[&quote=all|USDT|USDC]
        [&sort=<field>&direction=asc|desc]   candidate table for a log
    POST /api/run                    run a notebook command ({"command": "/help"})
    GET  /api/run?job=ID&after=N     poll a running job's output
"""

import argparse
import hmac
import json
import math
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import islice
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8666
PAGE_SIZE = 200
MAX_PAGE_SIZE = 2000
MAX_REQUEST_BODY = 16_384
MAX_JOB_LINES = 10_000
MAX_COMPLETED_JOBS = 8

NOTEBOOK_LOGDIR = "logs"
JOBS = {}  # job_id -> NotebookJob
JOBS_LOCK = threading.Lock()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def list_logs(logdir: str = "logs"):
    """Return *.log entries sorted newest-first: {name, size, mtime}."""
    entries = []
    try:
        names = os.listdir(logdir)
    except FileNotFoundError:
        return entries
    for name in names:
        if not name.endswith(".log"):
            continue
        path = os.path.join(logdir, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        entries.append({
            "name": name,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    entries.sort(key=lambda e: (e["mtime"], e["name"]), reverse=True)
    return entries


def _resolve(logdir: str, name: str):
    """Resolve a log file name safely inside logdir, or None.

    Accepts only plain basenames ending in `.log`. The resolved canonical
    path must stay inside the canonical logdir (symlink escapes rejected).
    """
    if not name or not name.endswith(".log") or name != os.path.basename(name):
        return None
    base = os.path.realpath(logdir)
    path = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([path, base]) != base:
        return None
    if not os.path.isfile(path):
        return None
    return path


def read_page(logdir: str, name: str, page: int = 1,
              page_size: int = PAGE_SIZE, query: str = ""):
    """Return one page of lines (optionally filtered by substring), or None."""
    path = _resolve(logdir, name)
    if path is None:
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        if query:
            lines = [ln.rstrip("\r\n") for ln in fh if query in ln]
        else:
            lines = [ln.rstrip("\r\n") for ln in fh]
    total = len(lines)
    pages = max(1, -(-total // page_size))
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    return {
        "name": name,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "lines": lines[start:start + page_size],
    }


# ── Candidate results ─────────────────────────────────────────────────────────

SCANNER_VERSION_REGISTRY = {
    "1": {"script": "binance_scanner_v1.py", "history": True, "args": True},
    "2": {"script": "binance_scanner_v2.py", "history": True, "args": True},
    "p": {"script": "binance_scanner_proto.py", "history": False, "args": False},
}

SORTABLE_RESULT_FIELDS = (
    "rank", "base", "pair", "price", "volume", "chg24", "atr", "atr_pct",
    "wr", "signals", "wins", "losses", "rr", "ev", "entry", "tp", "tp_pct",
    "trig", "trig_pct", "sl", "sl_pct", "qty", "gain", "loss", "timeouts",
    "signal_state", "target_source", "baseline_wr", "selected_wr", "rr_trigger", "rr_limit",
)

_TS_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")
_HEAD_RE = re.compile(r"^\s*#(\d+)\s+(\S+)\s+[^\s]*\s+(USDT|USDC)\s+pair\s*$")


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def _parse_block(lines: list) -> dict:
    """Parse one candidate block into a normalized row dict (None = absent)."""
    (rank, symbol, quote) = lines[0]
    row = {
        "rank": int(rank), "symbol": symbol, "base": None, "quote": quote,
        "price": None, "volume": None, "chg24": None,
        "atr": None, "atr_pct": None, "wr": None, "signals": None,
        "wins": None, "losses": None, "rr": None, "ev": None,
        "entry": None, "tp": None, "tp_pct": None,
        "trig": None, "trig_pct": None, "sl": None, "sl_pct": None,
        "qty": None, "gain": None, "loss": None, "version": "v1",
        "signal_state": None, "target_source": None, "timeouts": None,
        "baseline_wr": None, "selected_wr": None, "rr_trigger": None, "rr_limit": None,
    }
    for s in lines[1:]:
        m = re.match(r"^\s*Current Price\s+([\d,]+(?:\.[\d]+)?)\s+(\S+)", s)
        if m:
            if m.group(2) != row["quote"]:
                return None
            row["price"] = _num(m.group(1))
            continue
        m = re.match(r"^\s*24h Volume\s+([\d,]+(?:\.[\d]+)?)\s+(\S+)", s)
        if m:
            row["volume"] = _num(m.group(1))
            continue
        m = re.match(r"^\s*24h Change\s+([+-]?[\d.]+)%", s)
        if m:
            row["chg24"] = float(m.group(1))
            continue
        m = re.match(r"^\s*ATR\b.*?\s+([\d.]+)\s*\(\s*([\d.]+)%\s*\)", s)
        if m:
            row["atr"] = float(m.group(1))
            row["atr_pct"] = float(m.group(2))
            continue
        m = re.match(r"^\s*Win Rate\s+([\d.]+)%\s*\(\s*(\d+)\s*signals"
                     r"(?:\s*W:\s*(\d+)\s*/\s*L:\s*(\d+))?", s)
        if m:
            row["wr"] = float(m.group(1))
            row["signals"] = int(m.group(2))
            row["wins"] = int(m.group(3)) if m.group(3) else None
            row["losses"] = int(m.group(4)) if m.group(4) else None
            continue
        m = re.match(r"^\s*R\s*:\s*R\s+([\d.]+)\s*:", s)
        if m:
            row["rr"] = float(m.group(1))
            continue
        m = re.match(r"^\s*Exp\.?\s*Value\s+([+-]?[\d.]+)", s)
        if m:
            row["ev"] = float(m.group(1))
            continue
        m = re.match(r"^\s*[┌├└]\s*─*\s*ENTRY\s*─*\s+([\d.]+)\s+(\S+)"
                     r"(?:\s*\(\s*([+-]?[\d.]+)%\s*\))?", s)
        if m:
            row["entry"] = float(m.group(1))
            continue
        m = re.match(r"^\s*├\s*─*\s*TP\s*─*\s+([\d.]+)\s+(\S+)"
                     r"(?:\s*\(\s*([+-]?[\d.]+)%\s*\))?", s)
        if m:
            row["tp"] = float(m.group(1))
            row["tp_pct"] = float(m.group(3)) if m.group(3) else None
            continue
        m = re.match(r"^\s*├\s*─*\s*SL\s+Trig\s*─*\s+([\d.]+)\s+(\S+)"
                     r"(?:\s*\(\s*([+-]?[\d.]+)%\s*\))?", s)
        if m:
            row["trig"] = float(m.group(1))
            row["trig_pct"] = float(m.group(3)) if m.group(3) else None
            continue
        m = re.match(r"^\s*└\s*─*\s*SL\s*─*\s+([\d.]+)\s+(\S+)"
                     r"(?:\s*\(\s*([+-]?[\d.]+)%\s*\))?", s)
        if m:
            row["sl"] = float(m.group(1))
            row["sl_pct"] = float(m.group(3)) if m.group(3) else None
            continue
        m = re.match(r"^\s*Quantity\s+([\d.]+)\s+(\S+)", s)
        if m:
            row["qty"] = float(m.group(1))
            row["base"] = m.group(2)
            continue
        m = re.match(r"^\s*If TP hit\s+([+-][\d.]+)\s+(\S+)", s)
        if m:
            row["gain"] = float(m.group(1))
            continue
        m = re.match(r"^\s*If SL hit\s+([+-][\d.]+)\s+(\S+)", s)
        if m:
            row["loss"] = float(m.group(1))
            continue
    if row["price"] is None or row["entry"] is None or row["tp"] is None:
        return None
    return row


def _v2_num(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_v2_result(line: str):
    """Parse the V2 machine-readable log record; no display-text heuristics."""
    marker = "V2_RESULT "
    if marker not in line:
        return None
    try:
        payload = json.loads(line.split(marker, 1)[1])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != "v2":
        return None
    symbol = payload.get("symbol")
    base = payload.get("base")
    quote = payload.get("quote")
    rank = payload.get("rank")
    if (not isinstance(symbol, str) or not isinstance(base, str) or quote not in ("USDT", "USDC")
            or type(rank) is not int or rank < 1):
        return None
    state = payload.get("signal_state")
    if state not in ("ACTIVE", "INACTIVE"):
        return None
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    baseline = payload.get("baseline") if isinstance(payload.get("baseline"), dict) else {}
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    wins, losses, timeouts, opportunities = (
        payload.get("wins"), payload.get("losses"), payload.get("timeouts"),
        payload.get("opportunities"),
    )
    if not all(type(value) is int and value >= 0
               for value in (wins, losses, timeouts, opportunities)):
        return None
    if opportunities != wins + losses + timeouts:
        return None
    selected_wr = _v2_num(selected.get("hit_rate"))
    baseline_wr = _v2_num(baseline.get("hit_rate"))
    rr_limit = _v2_num(order.get("rr_to_limit"))
    return {
        "rank": rank, "symbol": symbol, "base": base, "quote": quote,
        "price": _v2_num(payload.get("price")), "volume": _v2_num(payload.get("volume")),
        "chg24": _v2_num(payload.get("chg24")), "atr": _v2_num(payload.get("atr")),
        "atr_pct": None, "wr": selected_wr * 100 if selected_wr is not None else None,
        "signals": opportunities, "wins": wins, "losses": losses, "timeouts": timeouts,
        "rr": rr_limit, "ev": None,
        "entry": _v2_num(order.get("entry")), "tp": _v2_num(order.get("take_profit")),
        "tp_pct": None, "trig": _v2_num(order.get("stop_trigger")), "trig_pct": None,
        "sl": _v2_num(order.get("stop_limit")), "sl_pct": None,
        "qty": _v2_num(order.get("quantity")), "gain": None, "loss": None,
        "version": "v2", "signal_state": state,
        "target_source": payload.get("target_source") if isinstance(payload.get("target_source"), str) else None,
        "baseline_wr": baseline_wr * 100 if baseline_wr is not None else None,
        "selected_wr": selected_wr * 100 if selected_wr is not None else None,
        "rr_trigger": _v2_num(order.get("rr_to_trigger")),
        "rr_limit": rr_limit,
    }


def extract_candidates(logdir: str, name: str):
    """Parse candidate blocks from a V1 or prototype scan log.

    Only content at/after the CANDIDATES section marker is considered.
    Returns (rows, warnings): rows are normalized dicts (None for absent
    values), warnings counts candidate blocks that could not be parsed.
    """
    path = _resolve(logdir, name)
    if path is None:
        return [], 0
    rows, warnings = [], 0
    block = None
    in_candidates = False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\r\n")
            if "V2_RESULT " in line:
                row = _parse_v2_result(line)
                if row is None:
                    warnings += 1
                else:
                    rows.append(row)
                continue
            if not in_candidates:
                if "CANDIDATES" in line:
                    in_candidates = True
                continue
            s = _TS_PREFIX_RE.sub("", line)
            match = _HEAD_RE.match(s)
            if match:
                if block is not None:
                    row = _parse_block(block)
                    if row is None:
                        warnings += 1
                    else:
                        rows.append(row)
                block = [match.groups(), s]
                continue
            if block is not None:
                if "OCO order" in s or "Saved Markdown results" in s:
                    break
                block.append(s)
    if not in_candidates:
        return rows, warnings
    if block is not None:
        row = _parse_block(block)
        if row is None:
            warnings += 1
        else:
            rows.append(row)
    return rows, warnings


def classify_line(line: str) -> str:
    """Return a semantic class used to colour notebook/log lines.

    The notebook client maps each class to a colour via CSS variables, so
    switching the colour scheme only swaps variables — no re-render needed.
    """
    s = _ANSI_RE.sub("", line).lstrip()
    if "VERDICT" in s:
        upper = s.upper()
        if "PASS" in upper or "ACCEPT" in upper:
            return "verdict-pass"
        return "verdict"
    if s.startswith("PAIR") and "/" in s:
        return "pair"
    if "CANDIDATES" in s:
        return "cand"
    if "STEP" in s:
        return "step"
    if "\u26a0" in s or "WARN" in s:
        return "warn"
    m = re.search(r"\[[\d\- :]+\]\s*([A-Z]+)", s)
    tag = m.group(1) if m else ""
    if tag == "PASS":
        return "pass"
    if tag == "SKIP":
        return "skip"
    if tag == "FAIL":
        return "fail"
    if tag in ("ERROR", "EXCEPTION"):
        return "error"
    if tag == "DB":
        return "db"
    if tag == "INFO":
        return "info"
    if s.startswith(("\u2500", "\u2550", "\u2501",
                     "\u2554", "\u2557", "\u255a", "\u255d")):
        return "rule"
    if s.startswith(("\u251c", "\u2514", "\u2502")):
        return "tree"
    return "plain"


class NotebookJob:
    """A background subprocess whose stdout is captured line by line."""

    def __init__(self, job_id: str, script: str, args: list[str],
                 env_overrides: dict = None):
        self.id = job_id
        self.script = script
        self.args = args
        self.env_overrides = dict(env_overrides or {})
        self.proc = None
        self.lines = deque(maxlen=MAX_JOB_LINES)
        self.line_base = 0
        self.finished = False
        self.exit_code = None
        self.error = None
        self.lock = threading.Lock()

    def start(self):
        root = os.path.dirname(os.path.abspath(__file__))
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(self.env_overrides)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            [sys.executable, "-u", self.script] + self.args,
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=env, creationflags=flags, bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _append_line(self, line):
        with self.lock:
            if len(self.lines) == MAX_JOB_LINES:
                self.line_base += 1
            self.lines.append(line)

    def _read(self):
        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip("\r\n")
                if line:
                    self._append_line(line)
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            self.proc.stdout.close()
            exit_code = self.proc.wait()
            with self.lock:
                self.exit_code = exit_code
                self.finished = True

    def is_finished(self):
        with self.lock:
            return self.finished

    def snapshot(self, after: int):
        with self.lock:
            base = self.line_base
            end = base + len(self.lines)
            start = min(max(after, base), end) - base
            retained = list(islice(self.lines, start, None))
            finished = self.finished
            exit_code = self.exit_code
            error = self.error
        return {
            "job_id": self.id,
            "script": self.script,
            "args": list(self.args),
            "lines": [{"t": ln, "c": classify_line(ln)} for ln in retained],
            "after": end,
            "truncated": after < base,
            "running": not finished,
            "finished": finished,
            "exit_code": exit_code,
            "error": error,
        }


def _active_job_unlocked():
    for job in JOBS.values():
        if not job.is_finished():
            return job
    return None


def _active_job():
    with JOBS_LOCK:
        return _active_job_unlocked()


def _prune_completed_jobs_unlocked():
    completed = [job_id for job_id, job in JOBS.items() if job.is_finished()]
    prune_count = max(0, len(completed) - MAX_COMPLETED_JOBS + 1)
    for job_id in completed[:prune_count]:
        del JOBS[job_id]


def start_job(script: str, args: list[str], env_overrides: dict = None):
    """Start one background job at a time; raises RuntimeError if busy."""
    with JOBS_LOCK:
        if _active_job_unlocked() is not None:
            raise RuntimeError("another command is still running — wait for it to finish")
        _prune_completed_jobs_unlocked()
        while True:
            job_id = "j" + secrets.token_hex(8)
            if job_id not in JOBS:
                break
        job = NotebookJob(job_id, script, list(args), env_overrides)
        JOBS[job.id] = job
    try:
        job.start()
    except Exception as exc:
        with JOBS_LOCK:
            if JOBS.get(job.id) is job:
                del JOBS[job.id]
        raise RuntimeError("cannot start command: %s" % exc) from exc
    return job


def notebook_status(logdir: str = NOTEBOOK_LOGDIR):
    root = os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(root, "scanner.db")
    db_size = os.path.getsize(db) if os.path.isfile(db) else 0
    v2_db = os.path.join(root, "scanner_v2.db")
    v2_db_size = os.path.getsize(v2_db) if os.path.isfile(v2_db) else 0
    logs = list_logs(logdir)
    job = _active_job()
    running = ("`%s %s`" % (job.script, " ".join(job.args))) if job else "none"
    return {
        "type": "markdown",
        "content": (
            "## Notebook status\n\n"
            "- **V1 DB:** `scanner.db` — %d bytes\n"
            "- **V2 DB:** `scanner_v2.db` — %d bytes\n"
            "- **Log files:** %d in `logs/` (latest: `%s`)\n"
            "- **Running job:** %s\n"
        ) % (db_size, v2_db_size, len(logs), logs[0]["name"] if logs else "\u2014", running),
    }


def _extract_version(args: list[str]):
    """Return (version, remaining_args, error), requiring one explicit selector."""
    values, remaining = [], []
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("-v", "--version"):
            if index + 1 >= len(args):
                return None, [], "missing scanner version; use -v 1, -v 2, or -v p"
            values.append(args[index + 1])
            index += 2
            continue
        if token.startswith("--version="):
            value = token.split("=", 1)[1]
            if not value:
                return None, [], "missing scanner version; use --version=1, --version=2, or --version=p"
            values.append(value)
            index += 1
            continue
        remaining.append(token)
        index += 1
    if not values:
        return None, [], "missing scanner version; use -v 1, -v 2, or -v p"
    if len(values) != 1:
        return None, [], "duplicate scanner version selector"
    version = values[0].lower()
    if version not in SCANNER_VERSION_REGISTRY:
        return None, [], "unknown scanner version; use 1, 2, or p"
    return version, remaining, None


def run_notebook_command(command: str, logdir: str = NOTEBOOK_LOGDIR):
    """Dispatch a notebook command; returns a JSON-serialisable response."""
    if not command.strip():
        return {"error": "empty command \u2014 try /help"}
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        return {"error": "cannot parse command: %s" % exc}
    parts = [part[1:-1] if (len(part) >= 2 and part[0] == part[-1]
                            and part[0] in ('"', "'")) else part
             for part in parts]
    name = parts[0].lstrip("/").lower()
    args = parts[1:]
    if name == "help":
        root = os.path.dirname(os.path.abspath(__file__))
        try:
            with open(os.path.join(root, "help.md"), "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            return {"error": "cannot read help.md: %s" % exc}
        intro = ("**Notebook commands:** `/help` `/logs` `/status` "
                 "`/scan -v 1|2|p [args]` `/history -v 1|2` `/clear`\n\n---\n\n")
        return {"type": "markdown", "content": intro + content}
    if name == "clear":
        return {"type": "ok", "content": "cleared"}
    if name == "logs":
        logs = list_logs(logdir)
        if not logs:
            return {"type": "markdown", "content": "No log files yet \u2014 run a scan first."}
        rows = "\n".join("| %s | %d | %s |" % (l["name"], l["size"], l["mtime"])
                         for l in logs)
        return {"type": "markdown",
                "content": "| Log file | Size (B) | Modified |\n|---|---|---|\n" + rows}
    if name == "status":
        return notebook_status(logdir)
    if name == "scan":
        version, args, error = _extract_version(args)
        if error:
            return {"error": error}
        spec = SCANNER_VERSION_REGISTRY[version]
        if not spec["args"] and args:
            return {"error": "/scan -v p takes no scanner arguments"}
        env = {"SCANNER_LOGDIR": os.path.abspath(logdir)}
        return {"type": "job",
                "job_id": start_job(spec["script"], args, env).id}
    if name == "proto":
        return {"error": "/proto is retired; use /scan -v p"}
    if name == "history":
        version, args, error = _extract_version(args)
        if error:
            return {"error": error}
        spec = SCANNER_VERSION_REGISTRY[version]
        if not spec["history"]:
            return {"type": "markdown", "content": "Prototype output has no history; use `/logs`."}
        return {"type": "job",
                "job_id": start_job(spec["script"], ["--history", *args]).id}
    return {"error": "unknown command '/%s' \u2014 try /help" % name}


def make_handler(logdir: str = "logs", request_token: str = None):
    request_token = request_token or secrets.token_hex(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # keep the console clean

        def _send(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self._send_html(PAGE_HTML.replace("__SCANNER_TOKEN__", request_token))
            elif url.path == "/api/logs":
                self._send({"logs": list_logs(logdir)})
            elif url.path == "/api/log":
                qs = parse_qs(url.query)
                name = qs.get("name", [""])[0]
                try:
                    page = int(qs.get("page", ["1"])[0] or 1)
                    page_size = min(max(1, int(qs.get("page_size", [str(PAGE_SIZE)])[0] or PAGE_SIZE)),
                                    MAX_PAGE_SIZE)
                except ValueError:
                    self._send({"error": "invalid page or page_size"}, 400)
                    return
                query = qs.get("q", [""])[0]
                data = read_page(logdir, name, page, page_size, query)
                if data is None:
                    self._send({"error": "log file not found"}, 404)
                else:
                    data["lines"] = [{"t": ln, "c": classify_line(ln)}
                                     for ln in data["lines"]]
                    self._send(data)
            elif url.path == "/api/results":
                qs = parse_qs(url.query)
                name = qs.get("name", [""])[0]
                if _resolve(logdir, name) is None:
                    self._send({"error": "log file not found"}, 404)
                    return
                try:
                    page = int(qs.get("page", ["1"])[0] or 1)
                    page_size = min(max(1, int(qs.get("page_size", ["25"])[0] or 25)),
                                    200)
                except ValueError:
                    self._send({"error": "invalid page or page_size"}, 400)
                    return
                quote = qs.get("quote", ["all"])[0]
                if quote not in ("all", "USDT", "USDC"):
                    self._send({"error": "quote must be all, USDT or USDC"}, 400)
                    return
                signal_state = qs.get("signal_state", ["all"])[0]
                if signal_state not in ("all", "ACTIVE", "INACTIVE"):
                    self._send({"error": "signal_state must be all, ACTIVE, or INACTIVE"}, 400)
                    return
                sort = qs.get("sort", ["rank"])[0]
                if sort not in SORTABLE_RESULT_FIELDS:
                    self._send({"error": "invalid sort field"}, 400)
                    return
                direction = qs.get("direction", ["asc"])[0]
                if direction not in ("asc", "desc"):
                    self._send({"error": "direction must be asc or desc"}, 400)
                    return
                rows, warnings = extract_candidates(logdir, name)
                is_v2_log = any(row.get("version") == "v2" for row in rows)
                if quote != "all":
                    rows = [r for r in rows if r["quote"] == quote]
                if signal_state != "all":
                    rows = [r for r in rows if r.get("signal_state") == signal_state]
                sort_key = "symbol" if sort == "pair" else sort
                present = [r for r in rows if r[sort_key] is not None]
                missing = [r for r in rows if r[sort_key] is None]
                present.sort(key=lambda r: r[sort_key],
                             reverse=(direction == "desc"))
                rows = present + missing
                total = len(rows)
                pages = max(1, -(-total // page_size))
                page = min(max(1, page), pages)
                start = (page - 1) * page_size
                self._send({
                    "name": name,
                    "rows": rows[start:start + page_size],
                    "total": total, "page": page, "page_size": page_size,
                    "pages": pages, "sort": sort, "direction": direction,
                    "quote": quote, "warnings": warnings,
                    "signal_state": signal_state,
                    "version": "v2" if is_v2_log else "v1",
                })
            elif url.path == "/api/run":
                qs = parse_qs(url.query)
                job_id = qs.get("job", [""])[0]
                try:
                    after = int(qs.get("after", ["0"])[0] or 0)
                    if after < 0:
                        raise ValueError
                except ValueError:
                    self._send({"error": "invalid after"}, 400)
                    return
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if job is None:
                    self._send({"error": "job not found"}, 404)
                else:
                    self._send(job.snapshot(after))
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):
            url = urlparse(self.path)
            if url.path != "/api/run":
                self._send({"error": "not found"}, 404)
                return
            host = self.headers.get("Host", "").lower()
            hostname = host.rsplit(":", 1)[0].strip("[]")
            if hostname not in ("127.0.0.1", "localhost", "::1"):
                self._send({"error": "invalid host"}, 403)
                return
            origin = self.headers.get("Origin")
            if origin:
                parsed_origin = urlparse(origin)
                if (parsed_origin.scheme != "http"
                        or parsed_origin.netloc.lower() != host):
                    self._send({"error": "cross-origin request rejected"}, 403)
                    return
            supplied_token = self.headers.get("X-Scanner-Token", "")
            if not hmac.compare_digest(supplied_token, request_token):
                self._send({"error": "invalid request token"}, 403)
                return
            if self.headers.get_content_type() != "application/json":
                self._send({"error": "Content-Type must be application/json"}, 415)
                return
            try:
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise ValueError
                length = int(raw_length)
                if length < 0:
                    raise ValueError
                if length > MAX_REQUEST_BODY:
                    self._send({"error": "request body too large"}, 413)
                    return
                raw = self.rfile.read(length)
                body = json.loads(raw.decode("utf-8"))
            except ValueError:
                self._send({"error": "bad request body"}, 400)
                return
            if not isinstance(body, dict) or not isinstance(body.get("command"), str):
                self._send({"error": "command must be a string"}, 400)
                return
            command = body["command"].strip()
            try:
                self._send(run_notebook_command(command, logdir))
            except RuntimeError as exc:
                job = _active_job()
                self._send({"error": str(exc), "job_id": job.id if job else None}, 409)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Local dashboard for scanner logs")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--logdir", default="logs")
    args = ap.parse_args()

    host = "127.0.0.1"   # loopback only — never expose the dashboard to the LAN
    httpd = ThreadingHTTPServer((host, args.port), make_handler(args.logdir))
    httpd.daemon_threads = True
    print(f"Scanner dashboard: http://{host}:{args.port}")
    print(f"  Log directory : {os.path.abspath(args.logdir)}")
    print("  Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanner Dashboard</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
  header { height: 54px; display: flex; align-items: center; gap: 16px; padding: 0 20px;
           border-bottom: 1px solid var(--border); background: var(--panel); }
  header h1 { margin: 0; font-size: 17px; }
  header p { margin: 0; color: var(--muted); font-size: 12px; }
  nav { margin-left: auto; display: flex; gap: 6px; }
  nav button { background: var(--surface); color: var(--muted); border: 1px solid var(--border); border-radius: 6px;
               padding: 6px 16px; cursor: pointer; font-size: 13px; }
  nav button.active { color: var(--text-strong); border-color: var(--accent); background: var(--surface-active); }
  main { height: calc(100vh - 54px); }
  .tab { display: none; }
  .tab.active { display: flex; }
  #tab-logs { flex-direction: row; }
  aside { width: 320px; min-width: 320px; border-right: 1px solid var(--border); overflow-y: auto; padding: 10px; }
  aside h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 4px 6px 8px; }
  #empty { color: var(--muted); padding: 10px; font-size: 13px; }
  .file { display: block; width: 100%; text-align: left; padding: 8px 10px; margin-bottom: 4px;
          border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text);
          cursor: pointer; font-family: Consolas, monospace; font-size: 12px; }
  .file:hover { border-color: var(--accent); }
  .file.active { border-color: var(--accent); background: var(--surface-active); }
  .file-title { display: flex; align-items: baseline; gap: 7px; }
  .file-kind { color: var(--text-strong); font-weight: 700; letter-spacing: .03em; }
  .file-date { color: var(--text); font-weight: 550; }
  .file-time { margin-left: auto; color: var(--accent); font-variant-numeric: tabular-nums; }
  .file .meta { display: block; color: var(--muted); margin-top: 3px; }
  section { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #toolbar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
  #search { flex: 1; max-width: 380px; background: var(--surface); border: 1px solid var(--border); color: var(--text);
            border-radius: 6px; padding: 7px 10px; font-size: 13px; }
  select { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; }
  button { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px;
           padding: 6px 12px; cursor: pointer; font-size: 13px; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .4; cursor: default; }
  #page-info { color: var(--muted); font-size: 13px; margin-left: auto; white-space: nowrap; }
  #scroll { flex: 1; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; font-family: Consolas, "Courier New", monospace; font-size: 12.5px; }
  th { text-align: left; font-family: system-ui, sans-serif; font-size: 11px; text-transform: uppercase;
       letter-spacing: .06em; color: var(--muted); padding: 8px 12px; border-bottom: 1px solid var(--border);
       background: var(--panel); position: sticky; top: 0; }
  td { padding: 4px 12px; border-bottom: 1px solid var(--row-border); white-space: pre-wrap; word-break: break-all; vertical-align: top; }
  td.num { color: var(--line-number); width: 56px; text-align: right; }
  tr:hover td { background: var(--hover); }
  #tab-notebook { flex-direction: column; padding: 12px 16px; gap: 8px; }
  #nb-transcript { flex: 1; overflow-y: auto; background: #0b0d12; border: 1px solid #232733;
                   border-radius: 8px; padding: 12px; font-family: Consolas, monospace; font-size: 13px; }
  .nb-cell { margin-bottom: 12px; }
  .nb-prompt { color: #7ee787; margin-bottom: 4px; }
  .nb-prompt b { color: #d8dbe2; font-weight: 600; }
  .nb-out { white-space: pre-wrap; word-break: break-word; color: #c8cdd9; line-height: 1.5; }
  .nb-err { color: #ff7b72; white-space: pre-wrap; }
  .nb-done { color: #8a92a6; font-size: 12px; margin-top: 4px; }
  .md h1, .md h2, .md h3 { margin: 10px 0 6px; color: #e6e9f0; }
  .md h1 { font-size: 17px; } .md h2 { font-size: 15px; } .md h3 { font-size: 13.5px; }
  .md p { margin: 6px 0; }
  .md hr { border: none; border-top: 1px solid #232733; margin: 10px 0; }
  .md table { border-collapse: collapse; margin: 8px 0; font-size: 12.5px; }
  .md th, .md td { border: 1px solid #2a3040; padding: 4px 10px; text-align: left; }
  .md th { background: #161a22; }
  .md code { background: #161a22; border: 1px solid #232733; border-radius: 4px; padding: 1px 5px; font-size: 12px; }
  .md pre { background: #161a22; border: 1px solid #232733; border-radius: 6px; padding: 10px; overflow-x: auto; }
  .md pre code { border: none; background: none; padding: 0; }
  .md-li { margin: 2px 0; padding-left: 6px; }
  .md a { color: #3a6fd8; }
  #nb-input-row { display: flex; align-items: center; gap: 8px; border: 1px solid #2a3040;
                  border-radius: 8px; background: #0b0d12; padding: 8px 12px; }
  #nb-input { flex: 1; background: none; border: none; outline: none; color: #d8dbe2;
              font-family: Consolas, monospace; font-size: 13px; }
  #nb-hint { color: #8a92a6; font-size: 12px; }
  .lg-line { white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
  .nb-done-ok { color: var(--ln-pass); }
  .nb-done-err { color: var(--ln-fail); }
  :root {
    --bg: #0d1117; --panel: #111720; --surface: #161d27; --surface-active: #1b2634;
    --hover: #151d28; --border: #273243; --row-border: #1d2633;
    --text: #c9d2df; --text-strong: #f1f5f9; --muted: #7d899b; --line-number: #556174;
    --accent: #5b8def;
    --pair-base: #e2e8f0; --quote-usdc: #3dd6c6; --quote-usdt: #b59cff;
    --label-price: #6ea8ef; --label-vol: #d8a75c; --label-chg: #c495e8; --label-generic: #73b7bd;
    --reject-reason: #ff6b76; --reject-value: #e6a0aa; --log-time: #637083;
    --a-90: #8a92a6; --a-91: #ff7b72; --a-92: #7ee787; --a-93: #e3b341;
    --a-94: #79c0ff; --a-95: #d2a8ff; --a-96: #79c0ff; --a-97: #f0f6fc;
    --ln-pass: #7ee787; --ln-skip: #7f8a9e; --ln-info: #8fb3e8; --ln-db: #e3b341;
    --ln-fail: #ff7b72; --ln-error: #ff7b72; --ln-step: #79c0ff; --ln-pair: #79c0ff;
    --ln-verdict: #ff7b72; --ln-verdict-pass: #7ee787; --ln-cand: #e3b341;
    --ln-warn: #e3b341; --ln-rule: #5c6478; --ln-tree: #8a92a6; --ln-plain: #d8dbe2;
    --nb-bold: #f0f6fc;
  }
  body[data-palette="ocean"] {
    --bg: #06151c; --panel: #091c24; --surface: #0d252e; --surface-active: #11313c;
    --hover: #0d2933; --border: #1b3d49; --row-border: #12303a;
    --text: #c7dde2; --text-strong: #effcfe; --muted: #759ba4; --line-number: #4e737c;
    --accent: #38a6bc;
    --pair-base: #e4f2f4; --quote-usdc: #44dbc8; --quote-usdt: #b7a2f2;
    --label-price: #67c2df; --label-vol: #e0b66b; --label-chg: #c0a7ec; --label-generic: #6fc7b8;
    --reject-reason: #ff7482; --reject-value: #dfa3ac; --log-time: #557b84;
    --a-90: #759ba4; --a-91: #ff7f8b; --a-92: #52c7a8; --a-93: #f0bd65;
    --a-94: #62c7df; --a-95: #b7a4ef; --a-96: #62c7df; --a-97: #e7f7fa;
    --ln-pass: #52c7a8; --ln-skip: #8aa5ac; --ln-info: #62c7df; --ln-db: #b7a4ef;
    --ln-fail: #ff7f8b; --ln-error: #ff7f8b; --ln-step: #62c7df; --ln-pair: #70b8df;
    --ln-verdict: #ff7f8b; --ln-verdict-pass: #52c7a8; --ln-cand: #f0bd65;
    --ln-warn: #f0bd65; --ln-rule: #31515a; --ln-tree: #779aa2; --ln-plain: #c7dde2;
    --nb-bold: #effcfe;
  }
  .a-90 { color: var(--a-90); } .a-91 { color: var(--a-91); }
  .a-92 { color: var(--a-92); } .a-93 { color: var(--a-93); }
  .a-94 { color: var(--a-94); } .a-95 { color: var(--a-95); }
  .a-96 { color: var(--a-96); } .a-97 { color: var(--a-97); }
  .a-30, .a-31, .a-32, .a-33, .a-34, .a-35, .a-36, .a-37 { color: var(--ln-plain); }
  .nb-b { font-weight: 600; color: var(--nb-bold); }
  .nb-dim { opacity: .62; }
  td[class*="ln-"] { color: var(--ln-plain); box-shadow: inset 2px 0 transparent; }
  td.ln-pass, td.ln-verdict-pass { box-shadow: inset 2px 0 var(--ln-pass); }
  td.ln-skip { box-shadow: inset 2px 0 var(--ln-skip); }
  td.ln-info, td.ln-step, td.ln-pair { box-shadow: inset 2px 0 var(--ln-info); }
  td.ln-db { box-shadow: inset 2px 0 var(--ln-db); }
  td.ln-fail, td.ln-error, td.ln-verdict { box-shadow: inset 2px 0 var(--ln-fail); }
  td.ln-cand, td.ln-warn { box-shadow: inset 2px 0 var(--ln-warn); }
  .ln-rule { color: var(--ln-rule); } .ln-tree { color: var(--ln-tree); }
  .ln-plain { color: var(--ln-plain); }
  .log-tag { font-weight: 650; letter-spacing: .02em; }
  .tag-pass, .tag-verdict-pass { color: var(--ln-pass); }
  .tag-skip { color: var(--ln-skip); }
  .tag-info, .tag-step, .tag-pair { color: var(--ln-info); }
  .tag-db { color: var(--ln-db); }
  .tag-fail, .tag-error, .tag-verdict { color: var(--ln-fail); }
  .tag-cand, .tag-warn { color: var(--ln-warn); }
  .log-time { color: var(--log-time); }
  .pair-base { color: var(--pair-base); font-weight: 650; }
  .quote-usdc { color: var(--quote-usdc); font-weight: 700; }
  .quote-usdt { color: var(--quote-usdt); font-weight: 700; }
  .log-label { font-weight: 600; }
  .label-price { color: var(--label-price); }
  .label-vol { color: var(--label-vol); }
  .label-chg { color: var(--label-chg); }
  .label-generic { color: var(--label-generic); }
  .reject-reason { color: var(--reject-reason); font-weight: 650; }
  .value-rejected { color: var(--reject-value); }
  #view-switch { display: flex; gap: 0; }
  #view-switch button { border-radius: 0; padding: 6px 14px; }
  #view-switch button:first-child { border-radius: 6px 0 0 6px; }
  #view-switch button:last-child { border-radius: 0 6px 6px 0; }
  #view-switch button.active { color: var(--text-strong); border-color: var(--accent); background: var(--surface-active); }
  #results-wrap { flex: 1; display: flex; flex-direction: column; min-height: 0; }
  #results-scroll { flex: 1; overflow: auto; }
  #res-warn { color: var(--ln-warn); background: var(--surface-active); border-bottom: 1px solid var(--border);
              padding: 8px 14px; font-size: 12.5px; }
  #results-table { border-collapse: separate; border-spacing: 0; width: max-content; min-width: 100%; }
  #results-table th, #results-table td { border-bottom: 1px solid var(--row-border); padding: 5px 12px;
                font-size: 12.5px; white-space: nowrap; text-align: right; font-variant-numeric: tabular-nums; }
  #results-table th { position: sticky; top: 0; background: var(--panel); z-index: 3; cursor: pointer;
                user-select: none; color: var(--muted); font-weight: 600; }
  #results-table th:hover { color: var(--text); }
  #results-table th.sorted { color: var(--accent); }
  #results-table th:first-child, #results-table td:first-child { position: sticky; left: 0; z-index: 2; text-align: center; }
  #results-table th:nth-child(2), #results-table td:nth-child(2) { position: sticky; left: 56px; z-index: 2; text-align: left; }
  #results-table th:first-child, #results-table th:nth-child(2) { z-index: 4; background: var(--panel); }
  #results-table td:first-child, #results-table td:nth-child(2) { background: var(--panel); }
  #results-table tbody tr:hover td { background: var(--hover); }
  #results-table tbody tr:nth-child(even) td { background: var(--surface); }
  #results-table tbody tr:nth-child(even):hover td { background: var(--hover); }
  .res-sub { color: var(--muted); font-size: 11px; }
  .res-num-pos { color: var(--ln-pass); font-weight: 600; }
  .res-num-neg { color: var(--ln-fail); font-weight: 600; }
  .res-ev-pos { color: var(--ln-pass); font-weight: 600; }
  .res-ev-neg { color: var(--ln-fail); font-weight: 600; }
  #empty-res { color: var(--muted); padding: 24px; text-align: center; }
</style>
</head>
<body>
<header>
  <h1>Scanner Dashboard</h1>
  <p>logs + notebook</p>
  <nav>
    <select id="palette" title="log colour scheme">
      <option value="classic">Classic</option>
      <option value="ocean">Ocean</option>
    </select>
    <button id="tab-logs-btn" class="active">Logs</button>
    <button id="tab-notebook-btn">Notebook</button>
  </nav>
</header>
<main>
  <div id="tab-logs" class="tab active">
    <aside>
      <h2>Log files</h2>
      <div id="file-list"></div>
    </aside>
    <section>
      <div id="toolbar">
        <input id="search" type="search" placeholder="Filter lines… (whole file)">
        <div id="view-switch">
          <button id="view-raw" class="view-btn active">Raw Data</button>
          <button id="view-results" class="view-btn">Results</button>
        </div>
        <span id="raw-controls">
          <select id="page-size">
            <option value="100">100 / page</option>
            <option value="200" selected>200 / page</option>
            <option value="500">500 / page</option>
            <option value="1000">1000 / page</option>
          </select>
        </span>
        <span id="results-controls" style="display:none">
          <select id="quote-filter">
            <option value="all" selected>All quotes</option>
            <option value="USDT">USDT</option>
            <option value="USDC">USDC</option>
          </select>
          <select id="signal-state-filter">
            <option value="all" selected>All states</option>
            <option value="ACTIVE">Active</option>
            <option value="INACTIVE">Inactive</option>
          </select>
          <select id="res-page-size">
            <option value="25" selected>25 / page</option>
            <option value="50">50 / page</option>
            <option value="100">100 / page</option>
          </select>
        </span>
        <button id="prev">&#8249; prev</button>
        <span id="page-info">&#8212;</span>
        <button id="next">next &#8250;</button>
      </div>
      <table id="raw-table">
        <thead><tr><th style="width:56px">#</th><th>line</th></tr></thead>
      </table>
      <div id="scroll"><table><tbody id="rows"></tbody></table></div>
      <div id="results-wrap" style="display:none">
        <div id="res-warn" style="display:none"></div>
        <div id="results-scroll">
          <table id="results-table">
            <thead id="res-head"></thead>
            <tbody id="res-rows"></tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
  <div id="tab-notebook" class="tab">
    <div id="nb-transcript"></div>
    <div id="nb-input-row">
      <span style="color:#7ee787">&#10095;</span>
      <input id="nb-input" placeholder="type a command — /help for help" autocomplete="off" spellcheck="false">
    </div>
    <div id="nb-hint">/help &#183; /logs &#183; /status &#183; /scan -v 1|2|p &#183; /history -v 1|2 &#183; /clear</div>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let state = { name: null, page: 1, view: "raw",
              sort: "rank", direction: "asc", quote: "all", signalState: "all", resPageSize: 25 };
const POLL_MS = 800;

function reloadCurrent() { (state.view === "raw" ? loadPage : loadResults)(); }

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(2) + " MB";
}

function parseLogName(name) {
  const m = name.match(/^(v1|v2|proto)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?:_\d+)?\.log$/i);
  if (!m) return null;
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m[3]) - 1];
  if (!month) return null;
  return {
    kind: m[1].toLowerCase() === "proto" ? "PROTO" : m[1].toUpperCase(),
    date: month + " " + Number(m[4]) + ", " + m[2],
    time: m[5] + ":" + m[6] + ":" + m[7]
  };
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* translate ANSI SGR codes (as emitted by the scanners) to palette-aware spans */
const ANSI_RE = /\x1b\[([0-9;]*)m/g;
function ansiToHtml(line) {
  const st = { fg: null, bold: false, dim: false };
  let html = "", last = 0, openCls = "", match;
  const clsOf = () => {
    const parts = [];
    if (st.bold) parts.push("nb-b");
    if (st.dim) parts.push("nb-dim");
    if (st.fg !== null) parts.push("a-" + st.fg);
    return parts.join(" ");
  };
  ANSI_RE.lastIndex = 0;
  while ((match = ANSI_RE.exec(line)) !== null) {
    html += esc(line.slice(last, match.index));
    const codes = match[1] ? match[1].split(";").filter(Boolean) : ["0"];
    for (const c of codes) {
      const n = parseInt(c, 10);
      if (n === 0) { st.fg = null; st.bold = false; st.dim = false; }
      else if (n === 1) st.bold = true;
      else if (n === 2 || n === 4) st.dim = true;
      else if (n >= 30 && n <= 37) st.fg = n;
      else if (n >= 90 && n <= 97) st.fg = n;
    }
    const next = clsOf();
    if (next !== openCls) {
      if (openCls) html += "</span>";
      if (next) html += '<span class="' + next + '">';
      openCls = next;
    }
    last = match.index + match[0].length;
  }
  html += esc(line.slice(last));
  if (openCls) html += "</span>";
  return html;
}

function semanticLineHtml(line, cls) {
  const clean = line.replace(ANSI_RE, "");
  const rejected = ["skip", "fail", "error", "verdict"].includes(cls);
  let html = "", rest = clean;

  const time = rest.match(/^\[[^\]]+\]\s*/);
  if (time) {
    html += '<span class="log-time">' + esc(time[0]) + '</span>';
    rest = rest.slice(time[0].length);
  }

  const colonLabel = rest.match(/^(\s*)([A-Za-z][A-Za-z0-9 /().%<>=!_-]*?)(\s*:)/);
  if (colonLabel) {
    html += esc(colonLabel[1]) + labelHtml(colonLabel[2], colonLabel[3]);
    rest = rest.slice(colonLabel[0].length);
  }

  const tokenRe = /([+-]?\d+(?:,\d{3})*(?:\.\d+)?%)|\b([A-Z0-9]{2,})(USDT|USDC)\b|\b([A-Za-z][A-Za-z0-9_.%-]*)(\s*=)|\b([A-Z][A-Z0-9_]{2,})\b|([+-]?\$?\d[\d,]*(?:\.\d+)?)/g;
  let last = 0, match;
  while ((match = tokenRe.exec(rest)) !== null) {
    html += esc(rest.slice(last, match.index));
    if (match[1]) {
      html += percentageHtml(match[1]);
    } else if (match[2]) {
      if (cls === "pass" || cls === "verdict-pass") {
        html += '<span class="pair-base">' + esc(match[2]) + '</span>';
        html += '<span class="quote-' + match[3].toLowerCase() + '">' + match[3] + '</span>';
      } else {
        html += '<span class="value-rejected">' + esc(match[0]) + '</span>';
      }
    } else if (match[4]) {
      html += labelHtml(match[4], match[5]);
    } else if (match[6]) {
      html += upperTokenHtml(match[6], cls, rejected);
    } else if (match[7] && rejected) {
      html += '<span class="value-rejected">' + esc(match[7]) + '</span>';
    } else {
      html += esc(match[0]);
    }
    last = match.index + match[0].length;
  }
  return html + esc(rest.slice(last));
}

function labelHtml(label, separator) {
  const key = label.trim().toLowerCase();
  let kind = "generic";
  if (key === "price" || key.endsWith("price")) kind = "price";
  else if (key === "vol" || key.includes("volume")) kind = "vol";
  else if (key === "chg" || key.includes("change")) kind = "chg";
  return '<span class="log-label label-' + kind + '">' + esc(label + separator) + '</span>';
}

function percentageHtml(raw) {
  const value = Number(raw.replace(/,/g, "").replace("%", ""));
  const magnitude = Math.min(Math.abs(value), 100) / 100;
  const saturation = Math.round(48 + magnitude * 47);
  const lightness = Math.round((value < 0 ? 40 : 36) + magnitude * 25);
  const hue = value < 0 ? 356 : Math.round(146 - magnitude * 10);
  return '<span class="metric-percent" style="color:hsl(' + hue + ' ' + saturation + '% ' + lightness + '%)">' + esc(raw) + '</span>';
}

function upperTokenHtml(token, cls, rejected) {
  if (token === "PASS" || token === "ACCEPTED")
    return '<span class="log-tag tag-pass">' + token + '</span>';
  if (token === "SKIP")
    return '<span class="log-tag tag-skip">' + token + '</span>';
  if (token === "INFO" || token === "STEP" || token === "PAIR")
    return '<span class="log-tag tag-info">' + token + '</span>';
  if (token === "DB")
    return '<span class="log-tag tag-db">' + token + '</span>';
  if (token === "WARN" || token === "WARNING")
    return '<span class="log-tag tag-warn">' + token + '</span>';
  if (rejected && (token === "USDT" || token === "USDC"))
    return '<span class="value-rejected">' + token + '</span>';
  if (rejected)
    return '<span class="reject-reason">' + token + '</span>';
  return esc(token);
}

/* ---------------- tabs ---------------- */

function showTab(name) {
  $("tab-logs").classList.toggle("active", name === "logs");
  $("tab-notebook").classList.toggle("active", name === "notebook");
  $("tab-logs-btn").classList.toggle("active", name === "logs");
  $("tab-notebook-btn").classList.toggle("active", name === "notebook");
  if (name === "notebook") $("nb-input").focus();
}
$("tab-logs-btn").onclick = () => showTab("logs");
$("tab-notebook-btn").onclick = () => showTab("notebook");

/* ---------------- log viewer ---------------- */

async function loadFiles() {
  const data = await (await fetch("/api/logs")).json();
  const box = $("file-list");
  box.innerHTML = "";
  if (!data.logs.length) {
    box.innerHTML = '<div id="empty">No log files yet — run a scan first.</div>';
    return;
  }
  for (const f of data.logs) {
    const parsed = parseLogName(f.name);
    const b = document.createElement("button");
    b.className = "file" + (f.name === state.name ? " active" : "");
    b.title = f.name;
    b.innerHTML = parsed
      ? '<span class="file-title"><span class="file-kind">' + parsed.kind +
        '</span><span class="file-date">' + parsed.date +
        '</span><span class="file-time">' + parsed.time + '</span></span>' +
        '<span class="meta">' + fmtSize(f.size) + ' &middot; scan started</span>'
      : esc(f.name) + '<span class="meta">' + fmtSize(f.size) +
        " &middot; " + esc(f.mtime) + "</span>";
    b.onclick = () => { state.name = f.name; state.page = 1; loadFiles(); reloadCurrent(); };
    box.appendChild(b);
  }
}

async function loadPage() {
  if (!state.name) return;
  const q = $("search").value.trim();
  const ps = $("page-size").value;
  const url = "/api/log?name=" + encodeURIComponent(state.name) +
              "&page=" + state.page + "&page_size=" + ps +
              (q ? "&q=" + encodeURIComponent(q) : "");
  const r = await fetch(url);
  if (r.status === 404) {
    $("rows").innerHTML = '<tr><td colspan="2">Log file not found.</td></tr>';
    $("page-info").textContent = "—";
    return;
  }
  const d = await r.json();
  state.page = d.page;
  const rows = $("rows");
  rows.innerHTML = "";
  if (!d.lines.length) {
    rows.innerHTML = '<tr><td colspan="2" style="color:#8a92a6">No matching lines.</td></tr>';
  } else {
    const start = (d.page - 1) * d.page_size;
    d.lines.forEach((raw, i) => {
      const ln = (typeof raw === "string") ? { t: raw, c: "plain" } : raw;
      const tr = document.createElement("tr");
      const td1 = document.createElement("td");
      td1.className = "num";
      td1.textContent = start + i + 1;
      const td2 = document.createElement("td");
      td2.className = "ln-" + ln.c;
      td2.innerHTML = semanticLineHtml(ln.t, ln.c);
      tr.append(td1, td2);
      rows.appendChild(tr);
    });
  }
  $("page-info").textContent = d.total
    ? "page " + d.page + " / " + d.pages + " · " + d.total + " lines"
    : "0 lines";
  $("prev").disabled = d.page <= 1;
  $("next").disabled = d.page >= d.pages;
}

$("prev").onclick = () => { if (state.page > 1) { state.page--; reloadCurrent(); } };
$("next").onclick = () => { state.page++; reloadCurrent(); };
$("search").addEventListener("input", () => { state.page = 1; loadPage(); });
$("page-size").addEventListener("change", () => { state.page = 1; loadPage(); });

/* ---------------- results view ---------------- */

const V1_RES_COLUMNS = [
  { key: "rank",   label: "Rank" },
  { key: "pair",   label: "Pair" },
  { key: "price",  label: "Price" },
  { key: "volume", label: "24h Volume" },
  { key: "chg24",  label: "24h Change" },
  { key: "atr",    label: "ATR (ATR%)" },
  { key: "wr",     label: "Win Rate" },
  { key: "signals", label: "Signals" },
  { key: "rr",     label: "R : R" },
  { key: "ev",     label: "Exp. Value" },
  { key: "entry",  label: "Entry" },
  { key: "tp",     label: "TP (TP%)" },
  { key: "trig",   label: "SL Trig (Trig%)" },
  { key: "sl",     label: "SL (SL%)" },
  { key: "qty",    label: "Quantity" },
  { key: "gain",   label: "If TP hit" },
  { key: "loss",   label: "If SL hit" },
];

const V2_RES_COLUMNS = [
  { key: "rank",        label: "Rank" },
  { key: "pair",        label: "Pair" },
  { key: "signal_state", label: "State" },
  { key: "target_source", label: "TP source" },
  { key: "price",       label: "Best ask" },
  { key: "volume",      label: "24h Volume" },
  { key: "atr",         label: "ATR" },
  { key: "baseline_wr", label: "Baseline TP" },
  { key: "selected_wr", label: "Selected TP" },
  { key: "signals",     label: "Opportunities" },
  { key: "rr_trigger",  label: "R:R trigger" },
  { key: "rr_limit",    label: "R:R limit" },
  { key: "entry",       label: "Entry" },
  { key: "tp",          label: "TP" },
  { key: "trig",        label: "Stop trigger" },
  { key: "sl",          label: "Stop limit" },
  { key: "qty",         label: "Quantity" },
];

function resultColumns(data) { return data.version === "v2" ? V2_RES_COLUMNS : V1_RES_COLUMNS; }

function fmtFloat(v, maxDec) {
  if (v === null || v === undefined) return "—";
  let s = v.toFixed(maxDec);
  s = s.replace(/\.?0+$/, "");
  return s === "-0" ? "0" : s;
}

function fmtPct(v, signed) {
  if (v === null || v === undefined) return "—";
  const sign = signed && v > 0 ? "+" : "";
  return sign + v.toFixed(2) + "%";
}

function fmtMoney(v, signed) {
  if (v === null || v === undefined) return "—";
  const sign = signed && v > 0 ? "+" : "";
  return sign + v.toFixed(4);
}

function fmtVol(v) {
  return v === null ? "—" : Math.round(v).toLocaleString("en-US");
}

function pctSub(pct) {
  return pct === null ? "" : ' <span class="res-sub">(' +
         percentageHtml(fmtPct(pct, true)) + ")</span>";
}

function resCellHtml(r, c) {
  switch (c.key) {
    case "rank": return "<b>" + r.rank + "</b>";
    case "pair": {
      if (r.quote !== "USDT" && r.quote !== "USDC") return "â€”";
      const base = r.symbol.slice(0, -r.quote.length);
      return '<span class="pair-base">' + esc(base) + "</span>" +
             '<span class="quote-' + r.quote.toLowerCase() + '">' + esc(r.quote) + "</span>";
    }
    case "price": return fmtFloat(r.price, 8);
    case "volume": return fmtVol(r.volume);
    case "chg24": return r.chg24 === null ? "—" : percentageHtml(fmtPct(r.chg24, true));
    case "atr": return r.atr === null ? "—"
        : fmtFloat(r.atr, 8) + pctSub(r.atr_pct);
    case "wr": return r.wr === null ? "—" : percentageHtml(fmtPct(r.wr, false));
    case "baseline_wr": return r.baseline_wr === null ? "—" : percentageHtml(fmtPct(r.baseline_wr, false));
    case "selected_wr": return r.selected_wr === null ? "—" : percentageHtml(fmtPct(r.selected_wr, false));
    case "signal_state": return r.signal_state === null ? "—" : esc(r.signal_state);
    case "target_source": return r.target_source === null ? "—" : esc(r.target_source);
    case "signals": {
      if (r.signals === null) return "—";
      if (r.wins === null) return String(r.signals);
      return r.signals + ' <span class="res-sub">W:' + r.wins + " L:" + r.losses +
        (r.timeouts === null || r.timeouts === undefined ? "" : " TO:" + r.timeouts) + "</span>";
    }
    case "rr": return r.rr === null ? "—" : fmtFloat(r.rr, 1) + " : 1";
    case "rr_trigger": return r.rr_trigger === null ? "—" : fmtFloat(r.rr_trigger, 2) + " : 1";
    case "rr_limit": return r.rr_limit === null ? "—" : fmtFloat(r.rr_limit, 2) + " : 1";
    case "ev": return r.ev === null ? "—"
        : '<span class="' + (r.ev >= 0 ? "res-ev-pos" : "res-ev-neg") + '">' +
          fmtMoney(r.ev, true) + "</span>";
    case "entry": return fmtFloat(r.entry, 8);
    case "tp": return fmtFloat(r.tp, 8) + pctSub(r.tp_pct);
    case "trig": return fmtFloat(r.trig, 8) + pctSub(r.trig_pct);
    case "sl": return fmtFloat(r.sl, 8) + pctSub(r.sl_pct);
    case "qty": return fmtFloat(r.qty, 8);
    case "gain": return r.gain === null ? "—"
        : '<span class="' + (r.gain >= 0 ? "res-num-pos" : "res-num-neg") + '">' +
          fmtMoney(r.gain, true) + "</span>";
    case "loss": return r.loss === null ? "—"
        : '<span class="res-num-neg">' + fmtMoney(r.loss, true) + "</span>";
    default: return "—";
  }
}

function renderResultsHeader(d) {
  const head = $("res-head");
  head.innerHTML = "";
  const columns = resultColumns(d);
  columns.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c.label;
    th.onclick = () => {
      if (state.sort === c.key) {
        state.direction = state.direction === "asc" ? "desc" : "asc";
      } else {
        state.sort = c.key; state.direction = "asc";
      }
      state.page = 1;
      loadResults();
    };
    head.appendChild(th);
  });
  [...head.children].forEach((th, i) => {
    const c = columns[i];
    th.classList.toggle("sorted", c.key === d.sort);
    th.textContent = c.label + (c.key === d.sort
      ? (d.direction === "asc" ? " ▲" : " ▼") : "");
  });
}

async function loadResults() {
  if (!state.name) return;
  const url = "/api/results?name=" + encodeURIComponent(state.name) +
              "&page=" + state.page + "&page_size=" + state.resPageSize +
              "&quote=" + state.quote + "&sort=" + state.sort +
              "&direction=" + state.direction + "&signal_state=" + state.signalState;
  const r = await fetch(url);
  if (r.status === 404) {
    $("res-rows").innerHTML = '<tr><td id="empty-res">Log file not found.</td></tr>';
    $("page-info").textContent = "—";
    return;
  }
  const d = await r.json();
  state.page = d.page;
  renderResultsHeader(d);
  const tbody = $("res-rows");
  tbody.innerHTML = "";
  if (!d.rows.length) {
    tbody.innerHTML = '<tr><td id="empty-res">No candidates in this scan' +
                      (state.quote !== "all" ? " for " + state.quote : "") +
                      ".</td></tr>";
  } else {
    d.rows.forEach((row) => {
      const tr = document.createElement("tr");
      resultColumns(d).forEach((c) => {
        const td = document.createElement("td");
        td.innerHTML = resCellHtml(row, c);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }
  $("res-warn").style.display = d.warnings ? "" : "none";
  $("res-warn").textContent = d.warnings +
    " candidate block(s) skipped because they could not be parsed.";
  $("page-info").textContent = d.total
    ? "page " + d.page + " / " + d.pages + " · " + d.total + " results"
    : "0 results";
  $("prev").disabled = d.page <= 1;
  $("next").disabled = d.page >= d.pages;
}

function setView(v) {
  state.view = v;
  state.page = 1;
  $("view-raw").classList.toggle("active", v === "raw");
  $("view-results").classList.toggle("active", v === "results");
  const raw = v === "raw";
  $("search").style.display = raw ? "" : "none";
  $("raw-controls").style.display = raw ? "" : "none";
  $("results-controls").style.display = raw ? "none" : "";
  $("raw-table").style.display = raw ? "" : "none";
  $("scroll").style.display = raw ? "" : "none";
  $("results-wrap").style.display = raw ? "none" : "";
  reloadCurrent();
}
$("view-raw").onclick = () => setView("raw");
$("view-results").onclick = () => setView("results");
$("quote-filter").onchange = (e) => { state.quote = e.target.value; state.page = 1; loadResults(); };
$("signal-state-filter").onchange = (e) => { state.signalState = e.target.value; state.page = 1; loadResults(); };
$("res-page-size").onchange = (e) => { state.resPageSize = Number(e.target.value); state.page = 1; loadResults(); };

/* ---------------- notebook ---------------- */

function mdInline(s) {
  s = esc(s);
  s = s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  return s;
}

function mdToHtml(src) {
  const lines = src.split(/\n/);
  let html = "", inCode = false, codeBuf = [], tableBuf = [];
  const flushCode = () => {
    if (!codeBuf.length) return;
    html += "<pre><code>" + codeBuf.map(esc).join("\n") + "</code></pre>";
    codeBuf = [];
  };
  const flushTable = () => {
    if (!tableBuf.length) return;
    const head = tableBuf[0];
    const body = tableBuf.slice(2);
    html += "<table><thead><tr>" + head + "</tr></thead><tbody>" +
            body.map((r) => "<tr>" + r + "</tr>").join("") + "</tbody></table>";
    tableBuf = [];
  };
  for (const raw of lines) {
    const line = raw.replace(/\r$/, "");
    if (line.trim().startsWith("```")) {
      flushTable();
      if (inCode) { flushCode(); inCode = false; } else { inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    if (!line.trim()) { flushCode(); flushTable(); continue; }
    if (line.trim() === "---") { flushCode(); flushTable(); html += "<hr>"; continue; }
    if (line.startsWith("|")) {
      flushCode();
      const cells = line.split("|").slice(1, -1).map((c) => "<td>" + mdInline(c.trim()) + "</td>").join("");
      tableBuf.push(cells);
      continue;
    }
    flushTable();
    const h = line.match(/^(#{1,3})\s+(.*)/);
    if (h) { html += "<h" + h[1].length + ">" + mdInline(h[2]) + "</h" + h[1].length + ">"; continue; }
    const li = line.match(/^([-*])\s+(.*)/);
    if (li) { html += "<div class='md-li'>&bull; " + mdInline(li[2]) + "</div>"; continue; }
    html += "<p>" + mdInline(line) + "</p>";
  }
  flushCode();
  flushTable();
  return '<div class="md">' + html + "</div>";
}

const REQUEST_TOKEN = "__SCANNER_TOKEN__";
const nb = { history: [], hIdx: 0, pollTimer: null };

function nbScroll() {
  const t = $("nb-transcript");
  t.scrollTop = t.scrollHeight;
}

function nbCell() {
  const cell = document.createElement("div");
  cell.className = "nb-cell";
  $("nb-transcript").appendChild(cell);
  nbScroll();
  return cell;
}

function nbPrint(cell, text, cls) {
  const div = document.createElement("div");
  if (cls) div.className = cls;
  div.textContent = text;
  cell.appendChild(div);
  nbScroll();
}

function nbRender(cell, html) {
  const div = document.createElement("div");
  div.className = "nb-out";
  div.innerHTML = html;
  cell.appendChild(div);
  nbScroll();
}

async function pollJob(cell, jobId, after, out) {
  const r = await fetch("/api/run?job=" + encodeURIComponent(jobId) + "&after=" + after);
  if (!r.ok) {
    nbPrint(cell, r.status === 404
      ? "job no longer exists — the dashboard server was restarted"
      : "poll failed: " + r.status, "nb-err");
    nb.pollTimer = null;
    return;
  }
  const d = await r.json();
  for (const raw of d.lines) {
    const ln = (typeof raw === "string") ? { t: raw, c: "plain" } : raw;
    const div = document.createElement("div");
    div.className = "lg-line ln-" + ln.c;
    div.innerHTML = ansiToHtml(ln.t);
    out.appendChild(div);
    nbScroll();
  }
  if (!d.finished) {
    nb.pollTimer = setTimeout(() => pollJob(cell, jobId, d.after, out), POLL_MS);
  } else {
    const tag = document.createElement("div");
    tag.className = "nb-done" + (d.exit_code === 0 ? " nb-done-ok" : " nb-done-err");
    tag.textContent = "finished" + (d.exit_code === 0 ? " · ok" : " · exit " + d.exit_code) +
                      (d.error ? " · " + d.error : "");
    cell.appendChild(tag);
    nbScroll();
    nb.pollTimer = null;
  }
}

async function runCommand(raw) {
  const cmd = raw.trim();
  if (!cmd) return;
  nb.history.push(cmd);
  nb.hIdx = nb.history.length;
  $("nb-input").value = "";
  const cell = nbCell();
  const prompt = document.createElement("div");
  prompt.className = "nb-prompt";
  prompt.innerHTML = "<b>&#10095;</b> " + esc(cmd);
  cell.appendChild(prompt);
  if (nb.pollTimer) { clearTimeout(nb.pollTimer); nb.pollTimer = null; }
  try {
    const r = await fetch("/api/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Scanner-Token": REQUEST_TOKEN,
      },
      body: JSON.stringify({ command: cmd }),
    });
    const d = await r.json();
    if (d.error) {
      nbPrint(cell, d.error, "nb-err");
      if (d.job_id) {
        const out = document.createElement("div");
        out.className = "nb-out";
        cell.appendChild(out);
        nb.pollTimer = setTimeout(() => pollJob(cell, d.job_id, 0, out), POLL_MS);
      }
      return;
    }
    if (d.type === "markdown") {
      nbRender(cell, mdToHtml(d.content));
      if (d.link) {
        nbRender(cell, '<p><a href="' + d.link + '" target="_blank">Open ' + esc(d.link) + " &rarr;</a></p>");
      }
    } else if (d.type === "job") {
      const out = document.createElement("div");
      out.className = "nb-out";
      cell.appendChild(out);
      nb.pollTimer = setTimeout(() => pollJob(cell, d.job_id, 0, out), POLL_MS);
    } else if (d.type === "ok" && d.content === "cleared") {
      $("nb-transcript").innerHTML = "";
    } else {
      nbPrint(cell, JSON.stringify(d));
    }
  } catch (e) {
    nbPrint(cell, "request failed: " + e, "nb-err");
  }
}

const inp = $("nb-input");
inp.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    runCommand(inp.value);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (nb.hIdx > 0) { nb.hIdx--; inp.value = nb.history[nb.hIdx]; }
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    if (nb.hIdx < nb.history.length - 1) { nb.hIdx++; inp.value = nb.history[nb.hIdx]; }
    else { nb.hIdx = nb.history.length; inp.value = ""; }
  }
});

function setPalette(p) {
  document.body.dataset.palette = p;
  try { localStorage.setItem("nb.palette", p); } catch (e) {}
}
$("palette").onchange = (e) => setPalette(e.target.value);
(function initPalette() {
  let p = "classic";
  try { p = localStorage.getItem("nb.palette") || "classic"; } catch (e) {}
  if (p !== "classic" && p !== "ocean") p = "classic";
  $("palette").value = p;
  setPalette(p);
})();

(function boot() {
  const cell = nbCell();
  nbRender(cell, '<p style="color:#8a92a6">Notebook ready. Type <code>/help</code> to view ' +
                 '<code>help.md</code>, or <code>/scan -v 2 --max-scan 3</code> for a quick scan.</p>');
})();

loadFiles();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
