---
title: Binance Scanner Reliability Fixes - Plan
type: fix
date: 2026-08-10
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Binance Scanner Reliability Fixes - Plan

## Goal Capsule

Fix all review findings in `binance_scanner_v1.py` and `binance_scanner_proto.py`. Keep the prototype standalone and presentation-friendly. V1 retains its CLI, logging, and SQLite features. Do not add live trading or authenticated Binance API calls.

---

## Product Contract

### Requirements

- R1. Both scripts must print safely on Windows consoles that default to cp1252.
- R2. A failed ticker request must abort cleanly without passing incomplete pair records downstream.
- R3. Displayed quantities and OCO prices must comply with Binance tick, step, quantity, price, notional, budget, and ordering constraints.
- R4. Backtests must score only signals with a complete forward window.
- R5. Same-candle TP/SL outcomes must use progressively smaller Binance klines; unresolved or failed drill-downs count as losses.
- R6. Pair filtering must not infer leveraged products from symbol suffixes or exclude legitimate assets such as JUP and SYRUP.
- R7. Empty eligible-pair lists must complete without runtime errors.
- R8. V1 must reject invalid CLI ranges and incompatible parameter relationships before scanning.
- R9. V1 must create configured database and log parent directories.
- R10. V1 must persist the actual signal count before minimum-signal rejection.
- R11. V1 history must distinguish interrupted runs from valid zero-result runs.

### Scope Boundaries

- Keep both scanners as standalone scripts so the prototype remains easy to demonstrate as a PoC.
- Add one focused standard-library test module; do not introduce a new runtime dependency.
- Do not place orders, use API keys, change the trading strategy, or perform unrelated refactoring.

---

## Planning Contract

### Key Technical Decisions

- KTD1. Configure stdout and stderr as UTF-8 with replacement fallback before help text or headers are printed.
- KTD2. Preserve Binance filter values as decimal strings and perform order calculations with `Decimal`, avoiding binary-float modulo checks.
- KTD3. Quantize quantity down, TP up, stop trigger up, and stop-limit price down. Recalculate displayed gain, loss, and risk/reward from those executable values.
- KTD4. Use lower-timeframe klines for same-candle ambiguity. `(session-settled: user-directed — chosen over immediate conservative loss or exclusion: use finer market data before applying a fallback)`
- KTD5. Count an outcome as a loss when the drill-down request fails or a one-second candle remains ambiguous. `(session-settled: user-approved — chosen over exclusion or aggregate-trade pagination: deterministic conservative fallback)`
- KTD6. Keep the existing V1 database schema. A run is incomplete while its final result fields remain null, and history must render that state explicitly.

### Lower-Timeframe Resolution

Use the parent candle timestamps and recurse only into the ambiguous child candle:

- `1M`, `1w`, or `3d` to `1d`
- `1d` to `12h`; `12h` to `6h`; `8h` to `4h`; `6h` or `4h` to `2h`
- `2h` to `1h`; `1h` to `30m`; `30m` to `15m`
- `15m` to `5m`; `5m` or `3m` to `1m`; `1m` to `1s`

Binance supports timestamp-bounded klines down to one second with up to 1,000 rows per request: [Kline documentation](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market).

---

## Implementation Units

### U1. Runtime and market-data safety

- **Requirements:** R1, R2, R6, R7, R9
- **Files:** `binance_scanner_v1.py`, `binance_scanner_proto.py`, `tests/test_scanners.py`
- **Approach:** Configure console streams before parsing or printing. Create V1 output parents before opening files. Abort after ticker retries fail. Return cleanly for an empty input list. Remove suffix-based token filtering and rely on Binance `TRADING` and spot-permission metadata.
- **Test scenarios:** Simulate cp1252 output; return no ticker payload; pass an empty pair list; verify JUP and SYRUP remain eligible; verify nested V1 output paths are created.
- **Verification:** Neither script raises encoding or missing-field errors in these cases.

### U2. Backtest outcome correctness

- **Requirements:** R4, R5
- **Dependencies:** U1
- **Files:** `binance_scanner_v1.py`, `binance_scanner_proto.py`, `tests/test_scanners.py`
- **Approach:** Stop signal iteration at the last index that owns all `fwd_bars`. Resolve dual-hit candles through the interval chain in KTD4. Apply KTD5 only when ordering still cannot be proven.
- **Test scenarios:** Ignore a tail signal with a truncated horizon; resolve TP-first and SL-first child sequences correctly; recurse through another ambiguous child; count failed fetches and unresolved one-second bars as losses.
- **Verification:** Both scripts produce identical deterministic outcomes for the same fixture.

### U3. Binance-valid displayed orders

- **Requirements:** R3
- **Dependencies:** U1
- **Files:** `binance_scanner_v1.py`, `binance_scanner_proto.py`, `tests/test_scanners.py`
- **Approach:** Read exact `PRICE_FILTER`, `LOT_SIZE`, `MIN_NOTIONAL`, and `NOTIONAL` values without fabricated tick or step defaults. Quantize per KTD3, then validate price and quantity bounds, entry/TP/SL notionals, budget, and `TP > current price > trigger > SL`. Reject candidates that fail validation.
- **Test scenarios:** Verify every proposed value is an exact tick or step multiple; verify rounding directions; reject below-minimum quantity/notional and collapsed OCO levels; verify displayed economics use quantized values.
- **Verification:** Proposed orders satisfy Binance's [symbol filters](https://developers.binance.com/en/docs/products/spot/filters) and [SELL OCO ordering rules](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade).

### U4. V1 CLI and run-history integrity

- **Requirements:** R8, R10, R11
- **Dependencies:** U1, U2
- **Files:** `binance_scanner_v1.py`, `tests/test_scanners.py`
- **Approach:** Validate ordered win-rate and RSI bounds, supported Binance intervals, positive budget and multiplier/count values, `0 < trig_mult < sl_mult`, `60 <= n_candles <= 1000`, and enough candles for one full forward window. Set `n_signals` before early rejection. Render incomplete history rows with an explicit state and dashes instead of zeros; keep incomplete runs out of the completed recent-candidate summary.
- **Test scenarios:** Reject zero/negative and contradictory arguments; accept defaults and valid boundary values; persist signal counts for `FEW_SIGNALS`; distinguish an interrupted row from a finalized zero-result row.
- **Verification:** Invalid configurations fail during parsing, and history never presents an interrupted run as completed.

---

## Verification Contract

- Run Python compilation for both scanner files and the test module.
- Run the complete `unittest` suite with all Binance responses mocked.
- Perform optional public-market-data smoke scans for both scripts without credentials or order endpoints.
- Confirm every review finding has a direct regression test or an explicit smoke check.

---

## Definition of Done

- All eleven review findings are addressed in V1 and every applicable shared finding is addressed in the prototype.
- Both scripts remain directly runnable and the prototype remains suitable for PoC demonstrations.
- The deterministic test suite passes without live Binance access.
- Optional live smoke scans complete without placing orders.
- Every scan run (proto and V1) writes an always-on, timestamped UTF-8 log under `logs/` (`proto_YYYYmmdd_HHMMSS.log` / `v1_YYYYmmdd_HHMMSS.log`), and V1's `Logger._ts()` timestamps use the full date.
- `log_dashboard.py` serves a local web dashboard (stdlib only) listing `logs/` files with a clickable, paginated, searchable line table.
- No unrelated files, documentation, or abandoned implementation attempts remain.
