# Brainstorm: 1s Archive + Fibonacci Matrix V3

## Problem Statement

Build a two-part system on top of the existing V2 source-independent candle core:
1. **Part 1**: A 1s archive scraper that downloads, validates, and stores Binance Spot 1-second kline data
2. **Part 2**: A Fib Matrix V3 research script that reads the 1s archive, reconstructs Fibonacci intervals, builds an interval x period MA matrix, detects confluence interactions, and stores reaction events

## Goals

- Enable high-resolution (1s) data analysis for Fibonacci confluence research
- Maintain compatibility with existing V2 source-independent architecture
- Provide research-only output (no trading signals or order generation in V3)
- Support deterministic testing with fixtures

## Key Requirements

### Part 1: 1s Archive Scraper
- Download Binance Spot 1s kline ZIP files from `data.binance.vision`
- Verify checksums for data integrity
- Parse CSV with correct timestamp handling (μs for >=2025-01-01, ms for older)
- Store raw ZIP files and metadata in SQLite
- Implement `ArchiveCandleSource.fetch(CandleQuery) -> CandleBatch`
- CLI with `--symbols`, `--start`, `--end`, `--archive-dir`, `--db`, `--force`, `--verify-only`, `--dry-run`

### Part 2: Fib Matrix V3
- Read validated 1s archive data (unless `--bootstrap-missing`)
- Build matrix: intervals (5m,8m,13m,21m,34m,55m) × MA types (EMA,WMA,SMA) × periods (5,8,13,21,34,55) = 108 elements
- Evaluate on 1-minute cadence
- Cluster levels into confluence zones using 1m ATR scale (width = 0.10 × ATR, min members = 4)
- Detect events: SUPPORT_REJECTION, RESISTANCE_REJECTION, BREAK_UP, BREAK_DOWN, TOUCH_ONLY
- Measure reaction windows: +60s, +300s, +900s, +1800s, +3600s, +14400s
- Store results in `fib_matrix_v3.db` and log JSON lines (V3_EVENT, V3_SUMMARY)
- CLI with `--symbols`, `--start`, `--end`, `--archive-dir`, `--archive-db`, `--event-db`, `--bootstrap-missing`

## Constraints

- V3 is research-only: no order/OCO output, no execution-readiness claim
- First implementation uses Raw+SQLite
- CLI first, no dashboard route
- No third-party data providers
- Update only README.md and help.md; add no extra documentation files
- Preserve all 116 existing tests

## Assumptions

- R (Requirement): Need 1s data for high-resolution Fibonacci analysis
- A (Assumption): Binance archive format is stable and accessible
- F (Fact): V2 architecture supports source-independent candle sources
- AE (Area of Expertise): Python, financial data processing, Binance API

## Open Questions

1. How to handle missing archive days? (Use `--bootstrap-missing` flag)
2. How to handle symbol delisting? (Skip with warning)
3. How to handle API rate limits? (Sequential downloads with retry)
4. How to handle partial archive files? (Checksum verification + gap detection)

## Risks

1. **Data integrity**: Corrupted ZIP files or checksum mismatches
2. **Timestamp handling**: μs vs ms confusion could cause incorrect analysis
3. **Performance**: 1s data is large; need efficient storage and querying
4. **Completeness**: Missing days could create gaps in analysis

## Stakeholders

- Research team: Uses V3 events for strategy development
- Engineering team: Maintains the scraper and matrix code
- Data team: Manages archive storage and metadata

## Success Metrics

- Scraper downloads and validates 1s data correctly
- Matrix produces correct Fibonacci interval values
- Confluence clustering groups close levels correctly
- Event detection classifies support/resistance correctly
- Reaction metrics compute MFE/MAE and window returns correctly
- All tests pass: `python -m unittest discover -s tests`
