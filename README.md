# Binance Spot Scanner

A Python research scanner for Binance Spot markets. It filters USDT and USDC
pairs, backtests an RSI/ATR strategy, validates candidate order values against
live Binance symbol filters, and records detailed results for later review.

The project includes three scanner entry points and a local dashboard for
browsing logs, comparing candidates, and launching supervised scans.

> [!IMPORTANT]
> This project reads public market data only. It does not use API keys, submit
> orders, or manage funds. Backtest results are not a promise of future
> performance.

## Highlights

- Filters Binance Spot symbols by quote asset, trading status, volume, and
  exchange constraints.
- Backtests an RSI oversold bounce near a recent low with ATR-based take-profit
  and stop-loss levels.
- Resolves candles where TP and SL are both touched using lower-timeframe data.
- Validates prices, quantities, notional values, percent-price bounds, tick
  sizes, and lot sizes before reporting an order layout.
- Writes timestamped UTF-8 logs; V1 also stores run history in SQLite.
- Adds V2 adaptive TP research: fixed closed-candle opportunity sets, timeout
  accounting, resistance targets, ATR fallback, and explicit IN_SAMPLE output.
- Provides a loopback-only dashboard with search, pagination, candidate tables,
  two colour schemes, and a notebook-style command panel.
- Includes deterministic tests with mocked network access.

## Requirements

- Python 3.11 or newer
- `requests`
- `numpy`
- `pandas`

## Quick start

```powershell
git clone https://github.com/eloqven/claude_version_of_scanner.git
cd claude_version_of_scanner

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install requests numpy pandas

python binance_scanner_v1.py --max-scan 3

# V2 uses four closed 500-candle pages and writes scanner_v2.db
python binance_scanner_v2.py --max-scan 3
```

Run a full V1 scan with the defaults:

```powershell
python binance_scanner_v1.py
```

Review previous V1 runs without starting a new scan:

```powershell
python binance_scanner_v1.py --history
```

## Scanner variants

| Entry point | Purpose | Output |
|---|---|---|
| `binance_scanner_v1.py` | Configurable scanner with CLI validation and persistent history | Console, timestamped log, SQLite |
| `binance_scanner_v2.py` | Adaptive TP research scanner with source-independent strategy logic | Console, `V2_RESULT` log records, `scanner_v2.db` |
| `binance_scanner_proto.py` | Simpler constant-driven scanner for experimentation | Console, timestamped log, Markdown results |
| `binance_1s_scraper.py` | Binance Spot 1s archive downloader/validator | Raw ZIPs, SQLite metadata (`scanner_archive.db`) |
| `binance_scanner_v3.py` | Fib Matrix V3 research engine (archive-backed) | `V3_EVENT`/`V3_SUMMARY` logs, `fib_matrix_v3.db` |

V2 uses `LOT_SIZE` for its limit entry and sell OCO layout, and validates the
buy and sell percent-price rules separately. V1 and prototype behavior stays
unchanged. See [help.md](help.md) for the complete command reference.

## Local dashboard

Start the dashboard and open the printed loopback URL:

```powershell
python log_dashboard.py
```

The default address is `http://127.0.0.1:8666`. The dashboard intentionally
binds only to loopback. It can:

- list and open scanner logs;
- paginate and filter log lines;
- display sortable candidate results;
- switch between Classic and Ocean palettes;
- run `/scan -v 1|2|p`, `/history -v 1|2`, `/logs`, and `/status` commands;
- stream one background scanner job at a time.

Use a different port or log directory when needed:

```powershell
python log_dashboard.py --port 9000 --logdir logs
```

## How a scan works

1. Load Binance exchange information and require valid price, lot-size, and
   notional filters. Market-lot and percent-price filters are also validated
   when Binance supplies them.
2. Keep active USDT/USDC Spot pairs that meet the configured volume and budget
   constraints.
3. Fetch candles, calculate ATR and RSI, and backtest qualifying signals.
4. Drill down when a candle touches both TP and SL to determine which occurred
   first where lower-timeframe data is available.
5. Report only candidates whose win rate and generated order layout pass the
   configured strategy checks and supported parsed symbol constraints.

V2 fetches four paginated 500-candle pages beneath a Binance server-time
cutoff. It freezes non-overlapping historical opportunities, includes
timeouts in TP hit rate, and never silently relaxes the configured upper rate.
If a long custom interval leaves too little history for the forward window, it
reports `INSUFFICIENT_HISTORY` rather than scoring a partial sample.

The generated order layout is informational. No order-placement endpoint is
called.

## 1-second archive scraper

`binance_1s_scraper.py` downloads and validates Binance Spot 1s klines from
`data.binance.vision` (raw ZIPs preserved locally with checksum verification and
SQLite metadata). It is the data source for the V3 research engine.

```powershell
# Dry run: show what would be downloaded, no network writes
python binance_1s_scraper.py --symbols BTCUSDT,EIGENUSDC --start 2026-08-10 --end 2026-08-12 --dry-run

# Real download + validate
python binance_1s_scraper.py --symbols BTCUSDT --start 2026-08-10 --end 2026-08-12

# Re-verify existing files' checksums without re-downloading
python binance_1s_scraper.py --symbols BTCUSDT --start 2026-08-10 --end 2026-08-12 --verify-only
```

Raw files land under `data/binance_1s/raw/spot/daily/klines/{SYMBOL}/1s/`.
Metadata, including per-file checksum and row/gap/duplicate status, is stored in
`scanner_archive.db` (both gitignored). Files dated `>= 2025-01-01` use
microsecond timestamps; older Spot files use milliseconds.

## Fib Matrix V3 research engine

`binance_scanner_v3.py` reads only validated 1s archive data (use
`--bootstrap-missing` to download first) and builds Fibonacci interval × period
MA matrices, clusters confluence zones, and records reaction events. It is
research-only: no order/OCO output and no execution-readiness claim.

```powershell
python binance_scanner_v3.py --symbols EIGENUSDC --start 2026-08-10 --end 2026-08-12
```

Events (support/resistance rejection, break up/down, touch only) are stored in
`fib_matrix_v3.db` and emitted as machine-readable `V3_EVENT` / `V3_SUMMARY`
JSON lines.


## Generated data

The following runtime files stay local and are ignored by Git:

- `logs/`
- `scanner.db` and other `*.db` files
- `binance_scan_*.md`
- Python bytecode and `__pycache__/`

## Tests

```powershell
python -m py_compile scanner_common.py binance_scanner_v1.py binance_scanner_proto.py binance_scanner_v2.py binance_1s_scraper.py binance_scanner_v3.py log_dashboard.py
python -m compileall -q scanner_v2
python -m unittest discover -s tests
```

The unit suite uses mocks and temporary directories, so it does not require
network access or modify live Binance state.

## Maintainer / agent workflow

- Define the affected surface first: V1, V2, proto, dashboard, docs, or
  generated data.
- Gather only evidence that can change the decision: use `rg`, read the exact
  current code or documentation, and inspect targeted logs or tests.
- Refresh live scan evidence before making claims about current candidates or
  results.
- Keep scope tight: avoid unrelated refactors, do not add documentation unless
  requested, and never commit runtime artifacts.
- Match validation to risk: review the diff for documentation-only changes;
  run targeted tests for localized code; run the full suite and a smoke scan
  for scanner or dashboard behavior changes.
- Report changed files, commands run, pass/fail results, and remaining
  unknowns.

## Safety notes

- The scanners make unauthenticated requests to Binance public endpoints.
- The dashboard rejects non-loopback hosts, path traversal, malformed API
  parameters, and unauthorised notebook POST requests.
- Order values are estimates derived from public data and the configured
  strategy; verify them independently before making any trading decision.
- Cryptocurrency trading involves substantial risk. Use this software for
  research and education at your own discretion.

## Project layout

```text
binance_scanner_v1.py       Configurable scanner and SQLite history
binance_scanner_v2.py       Adaptive TP scanner and V2 SQLite history
binance_scanner_proto.py    Constant-driven prototype scanner
binance_1s_scraper.py       Binance Spot 1s archive downloader/validator
binance_scanner_v3.py       Fib Matrix V3 research engine (archive-backed)
scanner_common.py           Shared exchange-filter and log-path helpers
scanner_v2/                 Source, indicator, strategy, order, and store package
log_dashboard.py            Loopback-only log and notebook dashboard
help.md                     Full command reference
tests/test_scanners.py      Scanner and dashboard test suite
```
