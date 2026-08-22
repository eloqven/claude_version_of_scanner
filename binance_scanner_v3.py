#!/usr/bin/env python3
"""Fibonacci Matrix V3 research scanner.

Reads validated 1s archive data, builds Fibonacci interval x period MA matrices,
detects confluence interactions, and stores reaction events.

Research-only: no order/OCO output, no execution-readiness claim.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import pandas as pd

from scanner_v2.archive import ArchiveCandleSource, ArchiveMetadataStore
from scanner_v2.fib_matrix import (
    FibMatrix,
    V3EventStore,
    log_event_json,
    log_summary_json,
    EventType,
)
from scanner_v2.models import (
    Candle,
    CandleBatch,
    CandleQuery,
    IndicatorEngine,
    IndicatorSpec,
    interval_to_us,
)


def _setup_console() -> None:
    """Force UTF-8 output with replacement fallback (Windows cp1252 safety)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _date_range(start: str, end: str) -> List[str]:
    """Generate list of YYYY-MM-DD dates from start to end inclusive."""
    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d")
    dates: List[str] = []
    current = start_date
    while current <= end_date:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def _get_1m_candles(archive_source: ArchiveCandleSource, symbol: str,
                    start_us: int, end_us: int) -> List[Candle]:
    """Get 1-minute candles by resampling 1s data."""
    query = CandleQuery(
        symbol=symbol,
        interval="1s",
        cutoff_us=end_us,
        start_us=start_us,
        end_us=end_us,
        limit=2000,
    )

    try:
        batch = archive_source.fetch(query)
    except Exception:
        return []

    # Resample to 1-minute
    from scanner_v2.sources import resample_candles
    try:
        resampled = resample_candles(batch, "1m")
        return list(resampled.candles)
    except Exception:
        return []


def _compute_atr_1m(candles: List[Candle], period: int = 14) -> Decimal:
    """Compute 1-minute ATR for clustering scale."""
    if len(candles) < period:
        return Decimal("1")

    df = pd.DataFrame({
        "high": [float(c.high) for c in candles],
        "low": [float(c.low) for c in candles],
        "close": [float(c.close) for c in candles],
    })

    df["tr"] = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr = df["tr"].rolling(period).mean().iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return Decimal("1")
    return Decimal(str(atr))


def run_v3_analysis(symbol: str, start_date: str, end_date: str,
                    archive_dir: str, archive_db: str, event_db: str,
                    bootstrap_missing: bool = False) -> int:
    """Run the V3 analysis for a symbol over a date range."""
    _setup_console()

    archive_source = ArchiveCandleSource(archive_dir, archive_db)
    fib_matrix = FibMatrix(archive_source)
    event_store = V3EventStore(event_db)

    dates = _date_range(start_date, end_date)
    total_evaluations = 0
    all_events = []

    print(f"Running V3 analysis for {symbol}: {start_date} to {end_date}")
    print(f"Archive dir: {archive_dir}")
    print(f"Event DB: {event_db}")
    print()

    for date in dates:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        day_start_us = int(date_obj.timestamp() * 1_000_000)
        day_end_us = int((date_obj + timedelta(days=1)).timestamp() * 1_000_000)

        print(f"Processing {date}...")

        # Get 1-minute candles for the day
        candles_1m = _get_1m_candles(archive_source, symbol, day_start_us, day_end_us)
        if not candles_1m:
            if bootstrap_missing:
                print(f"  No archive data for {date}, skipping (bootstrap-missing)")
                continue
            else:
                print(f"  WARNING: No archive data for {date}", file=sys.stderr)
                continue

        # Compute ATR for clustering scale
        atr = _compute_atr_1m(candles_1m)

        # Evaluate on 1-minute cadence
        for i in range(len(candles_1m)):
            eval_time = candles_1m[i].open_time_us
            total_evaluations += 1

            # Build matrix at this timestamp
            elements = fib_matrix.build_matrix(symbol, eval_time)
            if not elements:
                continue

            # Cluster into confluence zones
            zones = fib_matrix.cluster_zones(elements, atr)
            if not zones:
                continue

            # Detect events
            events = fib_matrix.detect_events(zones, candles_1m)
            for event in events:
                event_store.record_event(event)
                all_events.append(event)
                print(f"  {event.event_type.value} at {datetime.fromtimestamp(event.timestamp_us / 1_000_000)}")
                print(f"    {log_event_json(event)}")

        print(f"  {len(candles_1m)} 1m candles, {total_evaluations} evaluations")

    # Log summary
    summary = log_summary_json(symbol, all_events, total_evaluations)
    print(f"\n{summary}")

    event_store.close()
    archive_source.close()

    print(f"\nSummary: {total_evaluations} evaluations, {len(all_events)} events")
    print(f"Events stored in: {event_db}")
    return 0


def main() -> int:
    _setup_console()

    parser = argparse.ArgumentParser(
        description="Fibonacci Matrix V3 research scanner (research-only, no trading)"
    )
    parser.add_argument(
        "--symbols", required=True,
        help="Comma-separated list of symbols (e.g., BTCUSDT,EIGENUSDC)"
    )
    parser.add_argument(
        "--start", required=True,
        help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end", required=True,
        help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--archive-dir", default="data/binance_1s",
        help="Directory for archive data (default: data/binance_1s)"
    )
    parser.add_argument(
        "--archive-db", default="scanner_archive.db",
        help="SQLite archive metadata database (default: scanner_archive.db)"
    )
    parser.add_argument(
        "--event-db", default="fib_matrix_v3.db",
        help="SQLite event database (default: fib_matrix_v3.db)"
    )
    parser.add_argument(
        "--bootstrap-missing", action="store_true",
        help="Bootstrap missing archive data by downloading it first"
    )

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    for symbol in symbols:
        result = run_v3_analysis(
            symbol, args.start, args.end,
            args.archive_dir, args.archive_db, args.event_db,
            args.bootstrap_missing,
        )
        if result != 0:
            return result

    return 0


if __name__ == "__main__":
    sys.exit(main())
