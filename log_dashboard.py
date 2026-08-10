"""Local web dashboard for browsing scanner log files (stdlib only).

Run:
    python log_dashboard.py [--port 8666] [--logdir logs]

Endpoints:
    GET /                     HTML dashboard
    GET /api/logs             JSON list of *.log files
    GET /api/log?name=X&page=N&page_size=M[&q=text]   paginated lines
"""

import argparse
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8666
PAGE_SIZE = 200
MAX_PAGE_SIZE = 2000


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
    """Resolve a log file name safely inside logdir, or None."""
    if not name or name != os.path.basename(name):
        return None
    path = os.path.join(logdir, name)
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


def make_handler(logdir: str = "logs"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # keep the console clean

        def _send(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self._send_html(PAGE_HTML)
            elif url.path == "/api/logs":
                self._send({"logs": list_logs(logdir)})
            elif url.path == "/api/log":
                qs = parse_qs(url.query)
                name = qs.get("name", [""])[0]
                page = int(qs.get("page", ["1"])[0] or 1)
                page_size = min(max(1, int(qs.get("page_size", [str(PAGE_SIZE)])[0] or PAGE_SIZE)),
                                MAX_PAGE_SIZE)
                query = qs.get("q", [""])[0]
                data = read_page(logdir, name, page, page_size, query)
                if data is None:
                    self._send({"error": "log file not found"}, 404)
                else:
                    self._send(data)
            else:
                self._send({"error": "not found"}, 404)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Local dashboard for scanner logs")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--logdir", default="logs")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(args.logdir))
    httpd.daemon_threads = True
    print(f"Log dashboard → http://{args.host}:{args.port}")
    print(f"  Log directory : {os.path.abspath(args.logdir)}")
    print("  Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanner Log Dashboard</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, "Segoe UI", sans-serif; background: #0f1115; color: #d8dbe2; }
  header { padding: 14px 20px; border-bottom: 1px solid #232733; background: #13161d; }
  header h1 { margin: 0; font-size: 18px; }
  header p { margin: 2px 0 0; color: #8a92a6; font-size: 12px; }
  main { display: flex; height: calc(100vh - 66px); }
  aside { width: 320px; min-width: 320px; border-right: 1px solid #232733; overflow-y: auto; padding: 10px; }
  aside h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: #8a92a6; margin: 4px 6px 8px; }
  #empty { color: #8a92a6; padding: 10px; font-size: 13px; }
  .file { display: block; width: 100%; text-align: left; padding: 8px 10px; margin-bottom: 4px;
          border: 1px solid #232733; border-radius: 6px; background: #161a22; color: #d8dbe2;
          cursor: pointer; font-family: Consolas, monospace; font-size: 12px; }
  .file:hover { border-color: #3a6fd8; }
  .file.active { border-color: #3a6fd8; background: #1c2330; }
  .file .meta { display: block; color: #8a92a6; margin-top: 2px; }
  section { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #toolbar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid #232733; }
  #search { flex: 1; max-width: 380px; background: #161a22; border: 1px solid #2a3040; color: #d8dbe2;
            border-radius: 6px; padding: 7px 10px; font-size: 13px; }
  select { background: #1d2430; color: #d8dbe2; border: 1px solid #2a3040; border-radius: 6px; padding: 6px 8px; font-size: 13px; }
  button { background: #1d2430; color: #d8dbe2; border: 1px solid #2a3040; border-radius: 6px;
           padding: 6px 12px; cursor: pointer; font-size: 13px; }
  button:hover:not(:disabled) { border-color: #3a6fd8; }
  button:disabled { opacity: .4; cursor: default; }
  #page-info { color: #8a92a6; font-size: 13px; margin-left: auto; white-space: nowrap; }
  #scroll { flex: 1; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; font-family: Consolas, "Courier New", monospace; font-size: 12.5px; }
  th { text-align: left; font-family: system-ui, sans-serif; font-size: 11px; text-transform: uppercase;
       letter-spacing: .06em; color: #8a92a6; padding: 8px 12px; border-bottom: 1px solid #232733;
       background: #13161d; position: sticky; top: 0; }
  td { padding: 4px 12px; border-bottom: 1px solid #1a1e28; white-space: pre-wrap; word-break: break-all; vertical-align: top; }
  td.num { color: #5c6478; width: 56px; text-align: right; }
  tr:hover td { background: #151a24; }
</style>
</head>
<body>
<header>
  <h1>Scanner Log Dashboard</h1>
  <p id="subtitle">select a log file on the left</p>
</header>
<main>
  <aside>
    <h2>Log files</h2>
    <div id="file-list"></div>
  </aside>
  <section>
    <div id="toolbar">
      <input id="search" type="search" placeholder="Filter lines… (whole file)">
      <select id="page-size">
        <option value="100">100 / page</option>
        <option value="200" selected>200 / page</option>
        <option value="500">500 / page</option>
        <option value="1000">1000 / page</option>
      </select>
      <button id="prev">&#8249; prev</button>
      <span id="page-info">&#8212;</span>
      <button id="next">next &#8250;</button>
    </div>
    <table>
      <thead><tr><th style="width:56px">#</th><th>line</th></tr></thead>
    </table>
    <div id="scroll"><table><tbody id="rows"></tbody></table></div>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
let state = { name: null, page: 1 };

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(2) + " MB";
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function loadFiles() {
  const data = await (await fetch("/api/logs")).json();
  const box = $("file-list");
  box.innerHTML = "";
  if (!data.logs.length) {
    box.innerHTML = '<div id="empty">No log files yet — run a scan first.</div>';
    return;
  }
  for (const f of data.logs) {
    const b = document.createElement("button");
    b.className = "file" + (f.name === state.name ? " active" : "");
    b.innerHTML = esc(f.name) +
      '<span class="meta">' + fmtSize(f.size) + " &middot; " + esc(f.mtime) + "</span>";
    b.onclick = () => { state.name = f.name; state.page = 1; loadFiles(); loadPage(); };
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
    d.lines.forEach((ln, i) => {
      const tr = document.createElement("tr");
      const td1 = document.createElement("td");
      td1.className = "num";
      td1.textContent = start + i + 1;
      const td2 = document.createElement("td");
      td2.textContent = ln;
      tr.append(td1, td2);
      rows.appendChild(tr);
    });
  }
  $("page-info").textContent = d.total
    ? "page " + d.page + " / " + d.pages + " · " + d.total + " lines"
    : "0 lines";
  $("prev").disabled = d.page <= 1;
  $("next").disabled = d.page >= d.pages;
  $("subtitle").textContent = state.name;
}

$("prev").onclick = () => { if (state.page > 1) { state.page--; loadPage(); } };
$("next").onclick = () => { state.page++; loadPage(); };
$("search").addEventListener("input", () => { state.page = 1; loadPage(); });
$("page-size").addEventListener("change", () => { state.page = 1; loadPage(); });

loadFiles();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
