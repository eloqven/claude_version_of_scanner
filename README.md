# Binance Spot Scanner

A Python research scanner for Binance Spot markets. It filters USDT and USDC
pairs, backtests an RSI/ATR strategy, validates candidate order values against
live Binance symbol filters, and records detailed results for later review.

The project includes two scanner entry points and a local dashboard for
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
| `binance_scanner_proto.py` | Simpler constant-driven scanner for experimentation | Console, timestamped log, Markdown results |

Both scanners share the same Binance filter parser and order-validation rules.
See [help.md](help.md) for the complete command reference and every V1 option.

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
- run `/scan`, `/proto`, `/history`, `/logs`, and `/status` commands;
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

The generated order layout is informational. No order-placement endpoint is
called.

## Generated data

The following runtime files stay local and are ignored by Git:

- `logs/`
- `scanner.db` and other `*.db` files
- `binance_scan_*.md`
- Python bytecode and `__pycache__/`

## Tests

```powershell
python -m py_compile scanner_common.py binance_scanner_v1.py binance_scanner_proto.py log_dashboard.py
python -m unittest discover -s tests
```

The unit suite uses mocks and temporary directories, so it does not require
network access or modify live Binance state.

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
binance_scanner_proto.py    Constant-driven prototype scanner
scanner_common.py           Shared exchange-filter and log-path helpers
log_dashboard.py            Loopback-only log and notebook dashboard
help.md                     Full command reference
tests/test_scanners.py      Scanner and dashboard test suite
```
