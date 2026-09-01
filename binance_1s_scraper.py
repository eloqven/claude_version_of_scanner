#!/usr/bin/env python3
"""Binance 1-second kline archive scraper and validator."""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple
from zipfile import ZipFile, BadZipFile

import requests

from scanner_v2.archive import (
    ArchiveCandleSource,
    ArchiveFile,
    ArchiveMetadataStore,
    build_archive_url,
    build_local_path,
    detect_timestamp_unit,
    parse_checksum_file,
    parse_kline_csv,
    validate_candles,
)
from scanner_v2.models import Candle, CandleBatch, CandleQuery, interval_to_us


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


def _download_file(url: str, dest_path: str, timeout: int = 30) -> bool:
    """Download a file from URL to dest_path. Returns True on success."""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(response.content)
        return True
    except requests.RequestException as exc:
        print(f"  ERROR: Failed to download {url}: {exc}", file=sys.stderr)
        return False


def _compute_sha256(file_path: str) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _verify_checksum(zip_path: str, expected_checksum: str) -> bool:
    """Verify that the ZIP file's SHA256 matches the expected checksum."""
    actual = _compute_sha256(zip_path)
    return actual.lower() == expected_checksum.lower()


def _parse_archive_file(zip_path: str, date: str) -> Tuple[List[Candle], bool, bool, int, int]:
    """Parse an archive ZIP file and return (candles, has_gaps, has_duplicates, first_ts, last_ts)."""
    timestamp_unit = detect_timestamp_unit(date)
    interval_us = interval_to_us("1s")

    with ZipFile(zip_path, "r") as zf:
        csv_name = zf.namelist()[0]
        with zf.open(csv_name) as f:
            content = f.read().decode("utf-8")

    candles = parse_kline_csv(content, timestamp_unit)
    has_gaps, has_duplicates, first_ts, last_ts = validate_candles(candles, interval_us)
    return candles, has_gaps, has_duplicates, first_ts, last_ts


def _process_symbol_date(
    symbol: str,
    date: str,
    archive_dir: str,
    metadata_store: ArchiveMetadataStore,
    *,
    force: bool = False,
    verify_only: bool = False,
    dry_run: bool = False,
) -> Optional[ArchiveFile]:
    """Download, verify, and validate a single symbol/date archive.

    Returns ArchiveFile metadata if successful, None if skipped/failed.
    """
    zip_url, checksum_url = build_archive_url(symbol, date)
    zip_path, checksum_path = build_local_path(archive_dir, symbol, date)

    # Check if already validated
    if not force:
        existing = metadata_store.get_file(symbol, date)
        if existing and existing.validated:
            print(f"  {symbol} {date}: already validated, skipping")
            return existing

        # Download checksum file (optional). Binance daily klines publish the ZIP
        # but do not always publish a .CHECKSUM, so proceed without verification
        # when the checksum is unavailable.
        if not dry_run:
            expected_checksum = None
            if _download_file(checksum_url, checksum_path):
                try:
                    with open(checksum_path, "r") as f:
                        expected_checksum = parse_checksum_file(f.read())
                except (ValueError, OSError):
                    expected_checksum = None
            else:
                print(f"  {symbol} {date}: checksum unavailable, continuing without verification")

            if verify_only:
                if expected_checksum is None:
                    print(f"  {symbol} {date}: no checksum published to verify", file=sys.stderr)
                    return None
                # Just verify checksum, don't download ZIP
                if not _download_file(zip_url, zip_path):
                    print(f"  {symbol} {date}: failed to download ZIP for verification", file=sys.stderr)
                    return None
                if not _verify_checksum(zip_path, expected_checksum):
                    print(f"  {symbol} {date}: CHECKSUM MISMATCH", file=sys.stderr)
                    return None
                print(f"  {symbol} {date}: checksum verified")
                return None

            # Download ZIP file
            if not _download_file(zip_url, zip_path):
                print(f"  {symbol} {date}: failed to download ZIP", file=sys.stderr)
                return None

            # Verify checksum only when one was published
            if expected_checksum is not None and not _verify_checksum(zip_path, expected_checksum):
                print(f"  {symbol} {date}: CHECKSUM MISMATCH", file=sys.stderr)
                return None

        # Parse and validate
        try:
            candles, has_gaps, has_duplicates, first_ts, last_ts = _parse_archive_file(zip_path, date)
        except (BadZipFile, ValueError, OSError) as exc:
            print(f"  {symbol} {date}: failed to parse: {exc}", file=sys.stderr)
            return None

        local_sha256 = _compute_sha256(zip_path)
        now_us = int(time.time() * 1_000_000)

        record = ArchiveFile(
            symbol=symbol,
            date=date,
            zip_path=zip_path,
            checksum_path=checksum_path,
            local_sha256=local_sha256,
            expected_checksum=expected_checksum or "",
            row_count=len(candles),
            first_timestamp_us=first_ts,
            last_timestamp_us=last_ts,
            has_gaps=has_gaps,
            has_duplicates=has_duplicates,
            downloaded_at_us=now_us,
            validated=not has_gaps and not has_duplicates,
        )

        metadata_store.upsert_file(record)
        status = "validated" if record.validated else "validated (with gaps/duplicates)"
        print(f"  {symbol} {date}: {len(candles)} rows, {status}")
        return record
    else:
        print(f"  {symbol} {date}: dry-run, would download from {zip_url}")
        return None


def main() -> int:
    _setup_console()

    parser = argparse.ArgumentParser(
        description="Binance 1-second kline archive scraper and validator"
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
        "--db", default="scanner_archive.db",
        help="SQLite metadata database path (default: scanner_archive.db)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download and re-validate files even if already validated"
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Only verify checksums, do not parse or validate"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be downloaded without actually downloading"
    )
    parser.add_argument(
        "--max-retries", type=int, default=3,
        help="Maximum download retries per file (default: 3)"
    )

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    dates = _date_range(args.start, args.end)

    print(f"Scraping {len(symbols)} symbols x {len(dates)} dates = {len(symbols) * len(dates)} files")
    print(f"Archive dir: {args.archive_dir}")
    print(f"Metadata DB: {args.db}")
    print()

    metadata_store = ArchiveMetadataStore(args.db)
    total_files = 0
    total_validated = 0
    total_failed = 0

    for symbol in symbols:
        print(f"=== {symbol} ===")
        for date in dates:
            if args.dry_run:
                # Dry-run is informational only: never retry and never count
                # the "would download" result as a failure.
                result = _process_symbol_date(
                    symbol, date, args.archive_dir, metadata_store,
                    force=args.force, verify_only=args.verify_only, dry_run=True,
                )
                if result is not None:
                    total_files += 1
                    if result.validated:
                        total_validated += 1
                continue
            for attempt in range(args.max_retries):
                result = _process_symbol_date(
                    symbol, date, args.archive_dir, metadata_store,
                    force=args.force, verify_only=args.verify_only,
                )
                if result is not None:
                    total_files += 1
                    if result.validated:
                        total_validated += 1
                    break
                elif attempt < args.max_retries - 1:
                    print(f"  {symbol} {date}: retrying ({attempt + 1}/{args.max_retries})...")
                    time.sleep(1)
                else:
                    total_failed += 1
        print()

    metadata_store.close()

    print(f"Summary: {total_files} files processed, {total_validated} validated, {total_failed} failed")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
