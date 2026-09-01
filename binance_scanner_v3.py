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
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from scanner_v2.archive import ArchiveCandleSource, ArchiveMetadataStore
from scanner_v2.fib_matrix import (
    FibMatrix,
    V3EventStore,
    V3Event,
    log_event_json,
    log_summary_json,
    EventType,
    FIB_INTERVALS,
)
from scanner_v2.v3_checkpoint import (
    V3CheckpointStore,
    V3_COLLECTOR_VERSION,
    Outcome,
    EvaluationType,
    archive_checksum,
)
from scanner_v2.indicators import IndicatorEngine
from scanner_v2.models import (
    Candle,
    CandleBatch,
    CandleQuery,
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


def _resolve_window(checkpoint: V3CheckpointStore, archive_dir: str,
                    start: Optional[str], end: Optional[str],
                    fallback_start: str) -> Tuple[str, str]:
    """Resolve the scan window, watermarking from the last completed unit.

    When --start is not given, it becomes the day after the latest completed
    (SUCCESS/CHANGED) checkpoint; when --end is not given, it becomes the last
    archive date available on disk. Explicit dates always win.
    """
    resolved_end = end or _latest_archive_date(archive_dir) or fallback_start
    if start is not None:
        return start, resolved_end
    last = checkpoint.last_completed_date()
    if last is None:
        return fallback_start, resolved_end
    return (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"), resolved_end


def _latest_archive_date(archive_dir: str) -> Optional[str]:
    root = Path(archive_dir) / "raw" / "spot" / "daily" / "klines"
    latest: Optional[datetime] = None
    for path in root.glob("*/*/1s/*-1s-*.zip"):
        try:
            dt = datetime.strptime(path.name.split("-1s-")[1][:10], "%Y-%m-%d")
        except (IndexError, ValueError):
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest.strftime("%Y-%m-%d") if latest else None


def _get_1s_batch(archive_source: ArchiveCandleSource, symbol: str,
                  start_us: int, end_us: int) -> Optional[CandleBatch]:
    """Fetch the raw 1s candle batch for a date range from the archive."""
    query = CandleQuery(
        symbol=symbol,
        interval="1s",
        cutoff_us=end_us,
        start_us=start_us,
        end_us=end_us,
        limit=2000,
    )

    try:
        return archive_source.fetch(query)
    except Exception:
        return None


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


def _zone_key(zone) -> str:
    """Stable dedup identity for a confluence zone (bucketed mid price).

    Same confluence level across adjacent minutes shares a key so it is not
    re-emitted every bar; distinct levels (clear of each other) get keys apart.
    """
    return str(round(float(zone.mid), 4))


def _process_day(archive_source: ArchiveCandleSource, fib_matrix: FibMatrix,
                 event_store: V3EventStore, symbol: str, date: str,
                 bootstrap_missing: bool):
    """Fully process one (symbol, date) unit and return its event list.

    Raises ``_NoArchiveData`` when the archive has no data for the date (used to
    mark the unit ``unavailable`` rather than ``failed``).
    """
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    day_start_us = int(date_obj.timestamp() * 1_000_000)
    day_end_us = int((date_obj + timedelta(days=1)).timestamp() * 1_000_000)

    print(f"Processing {date}...")

    # Fetch the full day's 1s batch once and resample locally (perf fix:
    # avoids re-fetching and re-parsing the day for every 1m evaluation).
    batch_1s = _get_1s_batch(archive_source, symbol, day_start_us, day_end_us)
    if batch_1s is None:
        raise _NoArchiveData(date)

    from scanner_v2.sources import resample_candles

    candles_1m = list(resample_candles(batch_1s, "1m").candles)
    resampled_by_interval: Dict[str, CandleBatch] = {}
    for interval in FIB_INTERVALS:
        try:
            resampled_by_interval[interval] = resample_candles(batch_1s, interval)
        except Exception:
            continue

    # Compute ATR for clustering scale
    atr = _compute_atr_1m(candles_1m)

    day_events: List[object] = []
    day_evaluations = 0
    # Causal, duplicate-free detection: walk bars forward; a zone is emitted
    # only when the current bar first enters it, using only data up to now.
    # Reaction metrics are measured post-hoc (forward outcome) after detection.
    in_zone: Dict[str, bool] = {}
    for i in range(len(candles_1m)):
        bar = candles_1m[i]
        eval_time = bar.open_time_us
        day_evaluations += 1

        elements = fib_matrix.build_matrix(
            symbol, eval_time, resampled_by_interval=resampled_by_interval)
        if not elements:
            continue

        zones = fib_matrix.cluster_zones(elements, atr)
        for zone in zones:
            touches = FibMatrix.touch(zone, bar)
            key = _zone_key(zone)
            entered = touches and not in_zone.get(key, False)
            in_zone[key] = touches
            if not entered:
                continue

            event = FibMatrix.classify_touch_event(zone, bar, symbol)
            event = V3Event(
                event_type=event.event_type,
                timestamp_us=event.timestamp_us,
                symbol=event.symbol,
                zone=event.zone,
                reaction_metrics=fib_matrix.measure_reaction(
                    zone, bar, candles_1m),
                matrix_elements=event.matrix_elements,
            )
            event_store.record_event(event)
            day_events.append(event)
            print(f"  {event.event_type.value} at {datetime.fromtimestamp(event.timestamp_us / 1_000_000)}")
            print(f"    {log_event_json(event)}")

    print(f"  {len(candles_1m)} 1m candles, {day_evaluations} evaluations, {len(day_events)} events")
    return day_evaluations, day_events


class _NoArchiveData(Exception):
    """Raised internally when an archive date has no 1s data available."""


def run_v3_analysis(symbol: str, start_date: str, end_date: str,
                    archive_dir: str, archive_db: str, event_db: str,
                    bootstrap_missing: bool = False,
                    checkpoint_db: str = "v3_checkpoints.db",
                    run_id: Optional[str] = None) -> int:
    """Run the V3 analysis for a symbol over a date range.

    When checkpointing is enabled (default), an unchanged, already-successful
    (symbol, date) unit is skipped; a failed/unavailable date is retried; and a
    source/version change is recorded as a separately-labelled evaluation.
    """
    _setup_console()

    archive_source = ArchiveCandleSource(archive_dir, archive_db)
    fib_matrix = FibMatrix(archive_source)
    event_store = V3EventStore(event_db)
    checkpoint = V3CheckpointStore(checkpoint_db)

    dates = _date_range(start_date, end_date)
    total_evaluations = 0
    all_events = []
    skipped = 0
    changed = 0
    unavailable = 0
    failed = 0
    completed = 0

    print(f"Running V3 analysis for {symbol}: {start_date} to {end_date}")
    print(f"Archive dir: {archive_dir}")
    print(f"Event DB: {event_db}")
    print(f"Checkpoint DB: {checkpoint_db} (v{V3_COLLECTOR_VERSION})")
    print()

    for date in dates:
        checksum = archive_checksum(symbol, date, archive_dir)
        decision = checkpoint.should_process(symbol, date, checksum)

        if decision["action"] == "skip":
            skipped += 1
            print(f"V3_SKIP {date}: {decision['reason']}")
            continue

        if decision["action"] == "changed":
            changed += 1
            print(f"V3_CHANGE {date}: {decision['reason']}")

        try:
            day_evaluations, day_events = _process_day(
                archive_source, fib_matrix, event_store, symbol, date,
                bootstrap_missing=bootstrap_missing,
            )
        except _NoArchiveData:
            unavailable += 1
            checkpoint.record(
                symbol, date, checksum, Outcome.UNAVAILABLE, EvaluationType.RETRY,
                run_id=run_id,
            )
            if bootstrap_missing:
                print(f"  No archive data for {date}, recorded unavailable (bootstrap-missing)")
            else:
                print(f"  WARNING: No archive data for {date}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR processing {date}: {exc}", file=sys.stderr)
            checkpoint.record(
                symbol, date, checksum, Outcome.FAILED, EvaluationType.RETRY,
                run_id=run_id,
            )
            continue

        evaluation_type = (
            EvaluationType.CHANGED if decision["action"] == "changed"
            else EvaluationType.FIRST
        )
        outcome = (
            Outcome.CHANGED if decision["action"] == "changed"
            else Outcome.SUCCESS
        )
        checkpoint.record(
            symbol, date, checksum, outcome, evaluation_type,
            run_id=run_id, evaluations=day_evaluations, event_count=len(day_events),
        )
        completed += 1
        all_events.extend(day_events)
        total_evaluations += day_evaluations

    # Log summary
    summary = log_summary_json(symbol, all_events, total_evaluations)
    print(f"\n{summary}")

    event_store.close()
    checkpoint.close()
    archive_source.close()

    print(f"\nSummary: {total_evaluations} evaluations, {len(all_events)} events")
    print(f"skipped={skipped} changed={changed} unavailable={unavailable} failed={failed} completed={completed}")
    print(f"Events stored in: {event_db}")
    if failed > 0:
        return 1
    if completed == 0:
        return 2
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
        "--start", default=None,
        help="Start date (YYYY-MM-DD); default: day after last completed unit"
    )
    parser.add_argument(
        "--end", default=None,
        help="End date (YYYY-MM-DD); default: last date present in the archive"
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
    parser.add_argument(
        "--checkpoint-db", default="v3_checkpoints.db",
        help="SQLite checkpoint database (default: v3_checkpoints.db)"
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Run identifier recorded in each checkpoint (default: auto uuid)"
    )

    args = parser.parse_args()

    run_id = args.run_id or str(uuid.uuid4().hex[:12])
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    checkpoint = V3CheckpointStore(args.checkpoint_db)
    start, end = _resolve_window(
        checkpoint, args.archive_dir, args.start, args.end,
        fallback_start="2026-01-01",
    )
    print(f"Resolved window: {start} to {end}")

    for symbol in symbols:
        result = run_v3_analysis(
            symbol, start, end,
            args.archive_dir, args.archive_db, args.event_db,
            args.bootstrap_missing,
            args.checkpoint_db,
            run_id,
        )
        if result != 0:
            return result
    checkpoint.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
