# 1s Archive + Fibonacci Matrix V3 Plan

## Summary

Build this as two parts on top of the existing V2 source-independent candle core.

- **Part 1: 1s archive scraper**: independent Binance Spot 1-second kline downloader/validator using `data.binance.vision`, raw ZIP preservation, checksum verification, and SQLite metadata.
- **Part 2: Fib Matrix V3**: research-only script that reads the 1s archive, reconstructs Fibonacci intervals, builds an interval x period MA matrix, detects confluence interactions, and stores reaction events. No order/OCO output in V3 first slice.

Sources checked:

- Binance public data README: https://github.com/binance/binance-public-data
- Binance Spot daily 1s archive path example: https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-2026-08-10.zip

## Key Changes

### Part 1: 1s Scraper Component

- Add `scanner_v2/archive.py` and `binance_1s_scraper.py`.
- CLI:
  - `python binance_1s_scraper.py --symbols BTCUSDT,EIGENUSDC --start YYYY-MM-DD --end YYYY-MM-DD`
  - Optional: `--archive-dir data/binance_1s`, `--db scanner_archive.db`, `--force`, `--verify-only`, `--dry-run`.
- Store raw files under:
  - `data/binance_1s/raw/spot/daily/klines/{SYMBOL}/1s/{SYMBOL}-1s-YYYY-MM-DD.zip`
  - Same folder for `.CHECKSUM`.
- Add SQLite metadata tables for archive files, validation status, source URL, checksum, local sha256, row count, first/last timestamp, gaps, duplicates, and download timestamp.
- Parse Binance kline CSV columns exactly as official archive format:
  - open time, open, high, low, close, volume, close time, quote volume, trade count, taker buy base, taker buy quote, ignore.
- Timestamp handling:
  - file dates `>= 2025-01-01` use microseconds.
  - older Spot files use milliseconds.
  - do not infer timestamp unit from number magnitude.
- Add `ArchiveCandleSource.fetch(CandleQuery) -> CandleBatch` so V2/V3 can read validated archive data through the same source contract.
- Add `.gitignore` entries for archive data and archive DB. Update existing `README.md` / `help.md` only.

### Part 2: Fib Matrix V3 Script

- Add `scanner_v2/fib_matrix.py` and `binance_scanner_v3.py`.
- CLI:
  - `python binance_scanner_v3.py --symbols EIGENUSDC --start YYYY-MM-DD --end YYYY-MM-DD`
  - Optional: `--archive-dir`, `--archive-db`, `--event-db fib_matrix_v3.db`, `--bootstrap-missing`.
- V3 reads only validated 1s archive data unless `--bootstrap-missing` is supplied.
- Matrix definition:
  - intervals: `5m, 8m, 13m, 21m, 34m, 55m`
  - MA types: `EMA, WMA, SMA`
  - MA periods: `5, 8, 13, 21, 34, 55`
  - total matrix elements per evaluation point: `108`.
- Evaluate on a 1-minute cadence using the latest closed value from each matrix element.
- Cluster levels into confluence zones using 1m ATR as scale:
  - default cluster width: `0.10 * ATR`
  - minimum members: `4`
  - record member list, zone low/high/mid, width, interval diversity, period diversity, MA-type diversity.
- Detect event types:
  - `SUPPORT_REJECTION`
  - `RESISTANCE_REJECTION`
  - `BREAK_UP`
  - `BREAK_DOWN`
  - `TOUCH_ONLY`
- For each event, use 1s candles to measure reaction windows:
  - `+60s, +300s, +900s, +1800s, +3600s, +14400s`
  - record return, MFE, MAE, first-touch time, time inside zone, penetration depth, and crossing count.
- Store results in `fib_matrix_v3.db` and log machine-readable `V3_EVENT` / `V3_SUMMARY` JSON lines.
- V3 first slice is research-only: no trade commands, no OCO levels, no execution-readiness claim.

## Test Plan

- Archive tests in `tests/test_scanner_v2.py` or new `tests/test_archive.py`:
  - URL construction for daily 1s ZIP and `.CHECKSUM`.
  - checksum pass/fail.
  - CSV parse for 2025 microsecond files and pre-2025 millisecond files.
  - duplicate/gap detection.
  - skip already-valid files unless `--force`.
  - `ArchiveCandleSource` returns gap-free `CandleBatch`.
- V3 tests in new `tests/test_fib_matrix_v3.py`:
  - deterministic 1s fixture resamples to all fib intervals.
  - EMA/WMA/SMA matrix values match expected fixture calculations.
  - confluence clustering groups close levels and rejects weak clusters.
  - wick-based support/resistance rejection classification.
  - break-up / break-down classification.
  - reaction metrics compute MFE/MAE and window returns from 1s candles.
  - CLI emits `V3_EVENT` and persists event rows.
- Verification commands:
  - `python -m py_compile binance_1s_scraper.py binance_scanner_v3.py`
  - `python -m compileall -q scanner_v2`
  - `python -m unittest discover -s tests`
  - small dry-run scraper check against one known Binance archive URL.

## Assumptions

- First implementation uses Raw+SQLite.
- V3 first output is Research Events.
- Matrix uses Interval x Period.
- No dashboard route for V3 in this slice; CLI first keeps the scope tighter.
- No third-party data providers.
