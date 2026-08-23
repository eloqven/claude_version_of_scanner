# Code Review — Fib Matrix V3 + 1s Archive (feat/adaptive-tp-scanner-v2)

Scope: diff vs `origin/master`, focused on the current feature work —
`scanner_v2/archive.py`, `scanner_v2/fib_matrix.py`, `binance_1s_scraper.py`,
`binance_scanner_v3.py` — plus the tests that cover them.

Reviewer lenses: correctness, integration, performance, standards, test-gap.
No cross-model peer route is configured in this checkout, so this is a
single-pass local review.

## Verdict: BLOCK on 1 critical defect

The V3 engine is wired together and the unit tests pass, but it is
**non-functional end-to-end** due to a source/interval mismatch that the mock
tests cannot see. Below, in priority order.

## CRITICAL — `FibMatrix.build_matrix` is a silent no-op

`fib_matrix.py:100-114` loops over `FIB_INTERVALS` (`5m, 8m, 13m, ...`) and
calls `self.archive_source.fetch(query)` with `query.interval` set to each
fib interval. But `ArchiveCandleSource.fetch` (`archive.py:256`) raises
`ValueError("ArchiveCandleSource only supports 1s interval")` for any
non-`1s` query, and `build_matrix` wraps the call in `except Exception:
continue`. Result: every interval is dropped, `elements` is always `[]`,
`cluster_zones([])` returns `[]`, and `detect_events` returns `[]`. **V3 emits
zero events for any real symbol.**

The plan (`fib_matrix_v3_plan.md`) and the test name `test_deterministic_1m_resample`
both imply the intended design is: fetch `1s` data, then
`resample_candles(batch, interval)` per fib interval, then compute MAs. That
resample step is missing entirely. Fix: in `build_matrix`, fetch `1s` once per
evaluation, resample to each `interval`, and compute the MA on the resampled
candles.

## HIGH — wrong `symbol` stored on every V3 event

`fib_matrix.py:271`: `symbol=zone.members[0].interval`. `MatrixElement.interval`
is `"5m"`/`"8m"`/etc., so the event's `symbol` field is an interval string, not
the trading symbol. `detect_events(zones, candles_1m)` (`binance_scanner_v3.py:168`)
is called without the symbol, so the CLI `--symbols` value never reaches
`V3EventStore.record_event`. DB queries by symbol (`idx_v3_symbol`) will be
useless. Carry the real symbol through `detect_events` → `_classify_zone_event`.

## MEDIUM — MA computation is non-standard

`fib_matrix.py:142-175`:
- `_compute_ema` seeds the recursion with `values[0]` over exactly the last
  `period` closes, instead of seeding with an SMA of the first `period` values.
  This diverges from conventional EMA (e.g. TA-Lib) and from the "period-EMA"
  the plan's matrix implies.
- `_compute_wma` is fine arithmetically but, like EMA, only sees the trailing
  `period` window, so it equals a plain weighted average of the last `period`
  closes (acceptable, but document the intended semantics).

## MEDIUM — performance will be severe even after the fix

- `binance_scanner_v3.py:153` rebuilds the full matrix for **every 1m candle**
  across the whole date range.
- `build_matrix` (`fib_matrix.py:102`) currently calls
  `archive_source.fetch` per interval; `ArchiveCandleSource.fetch`
  (`archive.py:264`) re-reads and re-parses **all** validated archive files for
  the symbol each time. After the resample fix this is 6 intervals × N₁ₘ ×
  (full-day 1s parse). For a single symbol/day that is tens of millions of
  candle parses. Cache the 1s batch per evaluation and resample once.

## LOW — standards / cleanup

- `binance_1s_scraper.py:162`: `except (BadZipFile, ValueError, Exception)` —
  `Exception` already subsumes the others; drop the redundant entries.
- `archive.py:137-138` and `:149-150`: parse `date` with `strptime` then
  immediately `strftime` back to the same string; pass the original `date_str`
  through.

## Additional CRITICAL findings (found during live end-to-end validation)

Two further defects surfaced when actually running the tools against Binance:

1. **Scraper cannot download any 1s data** (`binance_1s_scraper.py`, `_process_symbol_date`).
   `data.binance.vision` publishes the daily `.zip` for `1s` (and `1m`) klines but
   **does not publish a `.CHECKSUM`** (verified: `.CHECKSUM` returns 404 for every
   granularity/date; `.zip` returns 200). The scraper hard-required a successful
   checksum download and aborted before fetching the zip, so it never acquired a
   single archive file and V3 had nothing to read.
   **Fix:** checksum download is now optional — proceed without verification when
   the checksum is unavailable; store `""` for `expected_checksum` (DB column is
   `NOT NULL`). `--verify-only` still works when a checksum *is* present.

2. **`binance_scanner_v3.py` fails at import** (line 31-38). It imported
   `IndicatorEngine` from `scanner_v2.models`, but `IndicatorEngine` lives in
   `scanner_v2.indicators` (`IndicatorSpec` is the only one in `models`). The CLI
   crashed with `ImportError` before processing anything, so V3 could never run.
   **Fix:** import `IndicatorEngine` from `scanner_v2.indicators`; keep
   `IndicatorSpec` in the `models` import.

After both fixes plus the earlier `build_matrix`/symbol work, V3 runs end-to-end
(see validation: 1440 evaluations, 938 events on BTCUSDT 2026-08-10).

## Test gap — masks the critical bug

`tests/test_fib_matrix_v3.py:394` builds `FibMatrix(mock.MagicMock(spec=ArchiveCandleSource))`;
the mock's `fetch` returns a Mock for any interval, so the `1s`-only guard is
never exercised. There is **no test that calls `build_matrix` against a real
`1s` `ArchiveCandleSource` + `resample_candles`**. Add an integration test
with a small in-memory 1s batch and assert `build_matrix` returns the expected
108 (or valid-subset) elements and that `detect_events` yields events with the
correct `symbol`.

## Coverage note

Unit tests for clustering, zone creation, reaction metrics, checksum parsing,
and CSV timestamp units are solid. The gap is purely the missing
source→resample→matrix integration path, which is exactly where the defect
lives.
