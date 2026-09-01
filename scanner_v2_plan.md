# Scanner V2: Adaptive TP Research Scanner

STE-inspired review — 0 unresolved wording findings. Source: public principles; no official compliance claim.

## Summary

- Add a separate `binance_scanner_v2.py`. Keep the V1 and prototype scripts unchanged.
- Replace `WR_TOO_HIGH` with adaptive TP selection. Define **TP hit rate** as `TP hits / (TP hits + stop-trigger hits + timeouts)`.
- The DODO audit proved the denominator defect: at 18 ATR, `2 wins / 15 opportunities = 13.33%`, not `2 / 13 = 15.38%`.
- Correct related inconsistencies: hidden timeouts, changing signal populations, wrong stop barrier, open-candle use, overlapping positions, unquantized backtests, misleading EV, and raw-win-rate ranking.

## Core Implementation

### Source-independent architecture

Create a small `scanner_v2` package with these interfaces:

- `CandleSource.fetch(CandleQuery) -> CandleBatch`
- `QuoteSource.get_best_quote(symbol) -> BookQuote`
- `IndicatorEngine.compute(CandleBatch, IndicatorSpec) -> IndicatorFrame`
- `ScanStore` for operational results
- `ResearchStore.record(StrategyTrace)` for future research

The strategy receives normalized data and returns `PairEvaluation` plus `StrategyTrace`. It must not call Binance, SQLite, or source-specific candle functions.

Use canonical UTC microseconds, half-open time ranges, exact OHLCV values, explicit interval anchors, completeness flags, gaps, content hashes, and data provenance. Adapters must declare their timestamp unit; they must not infer it from magnitude. This supports Binance archive data, whose timestamp format changed from milliseconds to microseconds in 2025. [Official archive format](https://github.com/binance/binance-public-data/blob/master/README.md).

Implement now:

- REST candle and quote adapters with dependency-injected HTTP functions.
- Four paginated 500-candle requests per symbol, producing 2,000 closed candles under one run cutoff.
- A deterministic resampler for arbitrary fixed intervals, including 5/8/13/21/34/55 minutes.
- UTC epoch anchoring; first-open, maximum-high, minimum-low, last-close, and exact volume/trade sums.
- Rejection of gaps, duplicates, conflicting candles, and partial buckets.
- Versioned ATR/RSI computation that matches current V1 results.

Defer archive downloading, WebSocket ingestion, heatmaps, matrix reconstruction, and validator research. The future archive adapter will use checksum-backed manifests without strategy changes. One-second OHLCV still cannot determine event ordering inside the same second; such dual hits remain ambiguous and conservative.

### Adaptive strategy

- Exclude the open candle using Binance-adjusted server time.
- Confirm historical signals on closed candles and enter at the next candle’s open.
- Freeze non-overlapping opportunities before TP testing. After each accepted signal, block new signals for `max(fwd_bars, cool_down)` bars.
- Score every candidate against exactly the same opportunities:
  - TP before stop trigger: win.
  - Stop trigger before TP: loss.
  - Neither within the window: timeout.
  - Same-candle hits: use lower-resolution candles; unresolved ordering is a conservative loss.
- Keep the current ATR stop-limit and trigger. Do not add support-based stops in V2.

Generate resistance candidates from the last 100 closed candles:

- Confirm five-candle swing highs using two candles on each side.
- Cluster levels within `0.25 × ATR`.
- Require at least two touches.
- Use the cluster median as resistance.
- Calculate `buffer = max(tick size, bid-ask spread, cluster median absolute deviation)`.
- Set TP below resistance and round down to the exchange tick.

Use `--tp-mult 8` as the minimum target. Test resistance candidates from nearest to farthest and choose the farthest executable TP whose inclusive TP hit rate is within `--min-wr` and `--max-wr`.

If no resistance candidate qualifies, test ATR candidates formed from historical favorable-excursion breakpoints. Label the result `ATR_FALLBACK`. If none qualifies, return `NO_FEASIBLE_TP`; never relax the ceiling silently.

Keep results explicitly `IN_SAMPLE`. Show opportunity, win, loss, and timeout counts. Report discrete-rate warnings when the configured band is too narrow for the sample. Remove the unsupported EV figure; show executable R:R to both the stop trigger and stop-limit instead.

An absent current signal does not hide the setup. Store `signal_state=ACTIVE|INACTIVE` and label both as research output. Rank active setups first, then selected executable R:R, opportunity count, and volume.

### Order construction

- Use the live best ask as a tick-valid limit-entry basis via Binance’s public book-ticker endpoint. [Official market-data API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints#symbol-order-book-ticker).
- Quantize entry, quantity, TP, trigger, and stop-limit before scoring and display.
- Apply `LOT_SIZE` to the limit entry and sell OCO quantity; do not apply `MARKET_LOT_SIZE`.
- Validate buy-side and sell-side percent-price limits separately, plus price, quantity, step, and notional filters. [Official Binance filters](https://github.com/binance/binance-spot-api-docs/blob/master/filters.md).
- Fail closed when quote, spread, reference-price, or required filter data is unavailable.

## Commands, Storage, and Dashboard

- Require `/scan -v 1`, `/scan -v 2`, or `/scan -v p`. Support `--version` and `--version=`.
- Reject missing, duplicate, or unknown versions without starting a process.
- Retire `/proto`; return guidance to use `/scan -v p`.
- Require `/history -v 1|2`; prototype history directs users to `/logs`.
- Use a fixed server-side version registry so later versions require only a new registry entry.
- Store V2 scans in `scanner_v2.db` and logs as `v2_*.log`.
- Keep operational scan storage separate from the no-op research store.
- Persist strategy/indicator versions, cutoff, parameter hash, provenance, signal state, baseline and selected TP rates, timeouts, target source, resistance evidence, buffer, and quantized order levels.
- Extend dashboard parsing, filtering, pagination, and log labels for V2. Update only `README.md` and `help.md`; add no extra documentation files.

## Verification and Delivery

- Preserve all 116 existing tests.
- Add deterministic tests for source interchangeability, timestamps, pagination, resampling, gaps, indicator parity, and provenance.
- Add strategy tests for fixed opportunity sets, full-window lockout, timeout accounting, stop-trigger scoring, dual hits, pivot clustering, adaptive buffer, hardest passing TP, ATR fallback, discrete bands, inactive signals, quantization, and filter failures.
- Include a DODO-shaped scorer fixture proving `2W/11L/2TO = 13.33%`; do not promise that live DODO will retain the same result after the new lockout rules.
- Test every `/scan -v` and `/history -v` route, including invalid and duplicate selectors.
- Run compilation, the complete suite, `python binance_scanner_v2.py --max-scan 3`, and dashboard loopback smoke tests for both palettes, invalid parameters, pagination, filtering, and traversal rejection.
- Inspect the final diff and Git status. Exclude logs, databases, generated HTML, and plan files.
- Create two signed commits: `feat: add adaptive TP scanner v2` and `feat: require explicit scanner versions`, each with `Signed-off-by: Scout`, then push to the existing public origin.

Planning used the [code-review skill](C:/Users/and_v/.codex/plugins/cache/compound-engineering-plugin/compound-engineering/3.21.4/skills/ce-code-review/SKILL.md), [planning skill](C:/Users/and_v/.codex/plugins/cache/compound-engineering-plugin/compound-engineering/3.21.4/skills/ce-plan/SKILL.md), and [ASD-STE100 reviewer](C:/Users/and_v/.codex/skills/asd-ste100-reviewer/SKILL.md).
