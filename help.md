# Binance Scanner — Command Reference

Run everything from the project root (`D:\claude_version_of_scanner`) with Python 3.11+.
Requires `requests`, `numpy`, `pandas` (stdlib only otherwise). No API keys needed — public market data only.

## Tests

```powershell
python -m py_compile scanner_common.py binance_scanner_v1.py binance_scanner_proto.py log_dashboard.py
python -m unittest discover -s tests
```

All tests are deterministic and mocked — no network access.

## V1 scanner — `binance_scanner_v1.py`

```powershell
# Full scan with defaults
python binance_scanner_v1.py

# Scoped scan (3 pairs for a quick test)
python binance_scanner_v1.py --max-scan 3

# Custom strategy
python binance_scanner_v1.py --budget 25 --min-wr 8.5 --max-wr 16 --tp-mult 10 --sl-mult 1.5

# Custom database + explicit log file
python binance_scanner_v1.py --db ./data/scans.db --log-file run.log

# Past run history (from the DB, no scanning)
python binance_scanner_v1.py --history
python binance_scanner_v1.py --history --db ./data/scans.db
```

### Options

| Option | Default | Description |
|---|---|---|
| `--budget USD` | `10.0` | Trading budget in USD |
| `--min-wr PCT` | `9.87` | Minimum win rate % |
| `--max-wr PCT` | `14.40` | Maximum win rate % |
| `--tp-mult X` | `8.0` | TP = entry + ATR × X |
| `--sl-mult X` | `1.0` | SL = entry − ATR × X |
| `--trig-mult X` | `0.15` | SL trigger = SL + ATR × X (must be < sl-mult) |
| `--min-vol USDT` | `300000` | Minimum 24 h quote volume |
| `--max-scan N` | `200` | Max pairs to scan, by volume |
| `--interval TF` | `4h` | Candle interval: `1s 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M` |
| `--n-candles N` | `500` | Candles to fetch per pair (60–1000; must exceed fwd-bars + 35) |
| `--rsi-low N` | `20` | RSI oversold lower bound |
| `--rsi-high N` | `36` | RSI oversold upper bound (≥ rsi-low) |
| `--lo-lookback N` | `20` | Bars for "near recent low" check |
| `--lo-margin X` | `1.025` | Close ≤ low × X to qualify |
| `--min-atr-pct PCT` | `0.40` | Min ATR/price % — skip flat coins |
| `--fwd-bars N` | `72` | Max forward bars for outcome |
| `--cool-down N` | `5` | Min bars between signals |
| `--min-signals N` | `8` | Min backtest signals required |
| `--db DB` | `scanner.db` | SQLite database path |
| `--log-file FILE` | auto | Explicit log path; when omitted a timestamped `logs/v1_YYYYmmdd_HHMMSS.log` is used automatically |
| `--history` | — | Print past runs from DB and exit |

## Proto scanner — `binance_scanner_proto.py`

No CLI arguments — all parameters are module constants at the top of the file
(`BUDGET`, `MIN_WR`, `MAX_WR`, `TP_MULT`, `SL_MULT`, `TRIG_MULT`, `MIN_VOL`,
`MAX_SCAN`, `INTERVAL`, `N_CANDLES`, `RSI_LOW`, `RSI_HIGH`, `LO_LOOKBACK`,
`LO_MARGIN`, `MIN_ATR_PCT`, `FWD_BARS`, `COOL_DOWN`).

```powershell
python binance_scanner_proto.py
```

Output is V1-style verbose: `[HH:MM:SS]`-prefixed log lines, STEP 1/2/3
sections, per-ticker PASS/SKIP lines, and per-pair PAIR tree blocks
(`├─ Candle fetch`, `├─ ATR(14)`, `└─ VERDICT`) streaming in real time.
Rejected pairs show the exact rejection code (`CANDLE_FETCH_FAIL`,
`ATR_TOO_FLAT`, `FEW_SIGNALS`, `WR_TOO_LOW`, `WR_TOO_HIGH`, `ORDER_INVALID`).

Every run writes a timestamped `logs/proto_YYYYmmdd_HHMMSS.log` (the path is
printed at the end) and saves the results as `binance_scan_YYYYmmdd_HHMMSS.md`
in the project root (gitignored) — including when zero candidates are found.

## Log dashboard — `log_dashboard.py`

```powershell
python log_dashboard.py                          # http://127.0.0.1:8666
python log_dashboard.py --port 9000              # custom port
python log_dashboard.py --logdir logs            # custom log directory
```

Binds to `127.0.0.1:8666` by default (loopback only — the dashboard is
never exposed to the LAN). Open the printed URL in a browser:
the **Logs** tab lists log files (newest first) — click one to browse its
lines in a paginated, searchable table.

### Notebook tab

The **Notebook** tab is a lightweight Jupyter-style panel: type a command in
the input box, press `Enter`, and the output streams into a cell below
(↑/↓ recall history). One job runs at a time; a busy job replies `409` with
its `job_id` so you can attach to the running output.

**Colour coding:** log lines use restrained semantic accents instead of
painting the entire row. The palette selector switches between two distinct
schemes: **Classic** graphite and **Ocean** deep teal. The choice persists
across reloads and applies to both tabs.

| Command | What it does |
|---|---|
| `/help` | Renders `help.md` (this file) as formatted markdown |
| `/status` | DB size, log count, current running job |
| `/logs` | Table of recent log files |
| `/scan [args]` | Runs `binance_scanner_v1.py` with the given args (e.g. `/scan --max-scan 3`); no args = full scan |
| `/proto` | Runs `binance_scanner_proto.py` |
| `/history` | Prints past runs from the DB |
| `/clear` | Clears the notebook transcript |

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard HTML page |
| `GET /api/logs` | JSON list of log files (name, size, mtime) |
| `GET /api/log?name=X&page=N&page_size=M&q=text` | Paginated lines, optional text filter (page_size ≤ 2000) |
| `POST /api/run` | Run a notebook command: `{"command": "/scan --max-scan 3"}` |
| `GET /api/run?job=ID&after=N` | Poll a running job — new lines since `after`, plus `running`/`finished`/`exit_code` |

## Logs

- Both scanners write always-on UTF-8 logs to `logs/` (`*.log`, gitignored).
- V1 lines are prefixed `[YYYY-mm-dd HH:MM:SS]`; console colors are stripped in the file.
- Proto log lines: lines that already start with `[HH:MM:SS]` (the verbose logger) are written as-is; every other line gets a full `[YYYY-mm-dd HH:MM:SS]` prefix. ANSI colors are stripped from all file lines.

## Notes

- `--history` renders incomplete runs as `INCOMPLETE` and skips them from the summary.
- `--interval`/ranges are validated up front — invalid values exit with a usage error listing the allowed choices.
- Scans are rate-limit-safe (sleep between calls); a full 200-pair proto run takes several minutes by design.
- Stop the dashboard with `Ctrl+C`.

---

*Maintained with Scout, the local assistant. Commits are signed-off to
trace changes back to the session that made them.*
