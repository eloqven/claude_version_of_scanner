"""Archive-backed candle source for Binance 1-second kline data."""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile

from .models import (
    Candle,
    CandleBatch,
    CandleIntegrityError,
    CandleQuery,
    SourceError,
    interval_to_us,
    timestamp_scale,
)


ARCHIVE_BASE_URL = "https://data.binance.vision/data/spot/daily/klines"


@dataclass(frozen=True)
class ArchiveFile:
    """Metadata for a downloaded archive file."""
    symbol: str
    date: str  # YYYY-MM-DD
    zip_path: str
    checksum_path: str
    local_sha256: str
    expected_checksum: str
    row_count: int
    first_timestamp_us: int
    last_timestamp_us: int
    has_gaps: bool
    has_duplicates: bool
    downloaded_at_us: int
    validated: bool


class ArchiveMetadataStore:
    """SQLite metadata store for archive files."""

    def __init__(self, db_path: str = "scanner_archive.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_tables()
        self.connection.commit()

    def _create_tables(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS archive_files (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                zip_path TEXT NOT NULL,
                checksum_path TEXT NOT NULL,
                local_sha256 TEXT NOT NULL,
                expected_checksum TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                first_timestamp_us INTEGER NOT NULL,
                last_timestamp_us INTEGER NOT NULL,
                has_gaps INTEGER NOT NULL,
                has_duplicates INTEGER NOT NULL,
                downloaded_at_us INTEGER NOT NULL,
                validated INTEGER NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            CREATE INDEX IF NOT EXISTS idx_archive_symbol ON archive_files(symbol);
            CREATE INDEX IF NOT EXISTS idx_archive_date ON archive_files(date);
        """)

    def upsert_file(self, record: ArchiveFile) -> None:
        self.connection.execute("""
            INSERT OR REPLACE INTO archive_files
            (symbol, date, zip_path, checksum_path, local_sha256, expected_checksum,
             row_count, first_timestamp_us, last_timestamp_us, has_gaps, has_duplicates,
             downloaded_at_us, validated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.symbol, record.date, record.zip_path, record.checksum_path,
            record.local_sha256, record.expected_checksum, record.row_count,
            record.first_timestamp_us, record.last_timestamp_us,
            int(record.has_gaps), int(record.has_duplicates),
            record.downloaded_at_us, int(record.validated),
        ))
        self.connection.commit()

    def get_file(self, symbol: str, date: str) -> Optional[ArchiveFile]:
        row = self.connection.execute(
            "SELECT * FROM archive_files WHERE symbol=? AND date=?",
            (symbol, date)
        ).fetchone()
        if row is None:
            return None
        return ArchiveFile(
            symbol=row[0], date=row[1], zip_path=row[2], checksum_path=row[3],
            local_sha256=row[4], expected_checksum=row[5], row_count=row[6],
            first_timestamp_us=row[7], last_timestamp_us=row[8],
            has_gaps=bool(row[9]), has_duplicates=bool(row[10]),
            downloaded_at_us=row[11], validated=bool(row[12]),
        )

    def get_validated_files(self, symbol: str) -> List[ArchiveFile]:
        rows = self.connection.execute(
            "SELECT * FROM archive_files WHERE symbol=? AND validated=1 ORDER BY date",
            (symbol,)
        ).fetchall()
        return [
            ArchiveFile(
                symbol=row[0], date=row[1], zip_path=row[2], checksum_path=row[3],
                local_sha256=row[4], expected_checksum=row[5], row_count=row[6],
                first_timestamp_us=row[7], last_timestamp_us=row[8],
                has_gaps=bool(row[9]), has_duplicates=bool(row[10]),
                downloaded_at_us=row[11], validated=bool(row[12]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self.connection.close()


def build_archive_url(symbol: str, date: str) -> Tuple[str, str]:
    """Build the ZIP and CHECKSUM URLs for a symbol/date.

    Returns (zip_url, checksum_url).
    """
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    date_str = date_obj.strftime("%Y-%m-%d")
    zip_url = f"{ARCHIVE_BASE_URL}/{symbol}/1s/{symbol}-1s-{date_str}.zip"
    checksum_url = f"{ARCHIVE_BASE_URL}/{symbol}/1s/{symbol}-1s-{date_str}.CHECKSUM"
    return zip_url, checksum_url


def build_local_path(archive_dir: str, symbol: str, date: str) -> Tuple[str, str]:
    """Build local file paths for ZIP and CHECKSUM files.

    Returns (zip_path, checksum_path).
    """
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    date_str = date_obj.strftime("%Y-%m-%d")
    base = Path(archive_dir) / "raw" / "spot" / "daily" / "klines" / symbol / "1s"
    base.mkdir(parents=True, exist_ok=True)
    zip_path = str(base / f"{symbol}-1s-{date_str}.zip")
    checksum_path = str(base / f"{symbol}-1s-{date_str}.CHECKSUM")
    return zip_path, checksum_path


def parse_checksum_file(content: str) -> str:
    """Parse a Binance CHECKSUM file and return the expected SHA256 hash.

    Binance CHECKSUM files contain lines like:
    <sha256>  <filename>
    """
    for line in content.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 1:
            return parts[0]
    raise ValueError("Could not parse checksum file")


def parse_kline_csv(csv_content: str, timestamp_unit: str) -> List[Candle]:
    """Parse Binance kline CSV content into Candle objects.

    Binance kline CSV columns:
    open time, open, high, low, close, volume, close time, quote volume,
    trade count, taker buy base, taker buy quote, ignore
    """
    scale = timestamp_scale(timestamp_unit)
    candles: List[Candle] = []

    reader = csv.reader(io.StringIO(csv_content))
    for row in reader:
        if len(row) < 12:
            raise CandleIntegrityError("kline CSV row has fewer than 12 columns")
        try:
            open_time = int(row[0]) * scale
            close_time = int(row[6]) * scale
            candle = Candle(
                open_time_us=open_time,
                close_time_us=close_time,
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[5]),
                trade_count=int(row[8]),
            )
            candles.append(candle)
        except (ValueError, ArithmeticError) as exc:
            raise CandleIntegrityError(f"malformed kline CSV row: {exc}") from exc

    return candles


def detect_timestamp_unit(date: str) -> str:
    """Determine timestamp unit based on file date.

    Files from 2025-01-01 onwards use microseconds.
    Older files use milliseconds.
    """
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    cutoff = datetime(2025, 1, 1)
    if date_obj >= cutoff:
        return "us"
    return "ms"


def validate_candles(candles: List[Candle], interval_us: int) -> Tuple[bool, bool, int, int]:
    """Validate candle sequence for gaps and duplicates.

    Returns (has_gaps, has_duplicates, first_timestamp_us, last_timestamp_us).
    """
    if not candles:
        return False, False, 0, 0

    has_duplicates = False
    has_gaps = False

    for i in range(1, len(candles)):
        prev = candles[i - 1]
        curr = candles[i]
        if curr.open_time_us == prev.open_time_us:
            has_duplicates = True
        if curr.open_time_us != prev.open_time_us + interval_us:
            has_gaps = True

    return has_gaps, has_duplicates, candles[0].open_time_us, candles[-1].close_time_us


class ArchiveCandleSource:
    """CandleSource that reads validated 1s archive data from local storage.

    Implements the CandleSource protocol from scanner_v2.sources.
    """

    def __init__(self, archive_dir: str = "data/binance_1s",
                 db_path: str = "scanner_archive.db") -> None:
        self.archive_dir = archive_dir
        self.metadata_store = ArchiveMetadataStore(db_path)

    def fetch(self, query: CandleQuery) -> CandleBatch:
        """Fetch validated 1s candles for a symbol within a time range.

        Returns a CandleBatch with 1s interval candles.
        """
        if query.interval != "1s":
            raise ValueError("ArchiveCandleSource only supports 1s interval")

        validated_files = self.metadata_store.get_validated_files(query.symbol)
        if not validated_files:
            raise SourceError(f"No validated archive files found for {query.symbol}")

        all_candles: List[Candle] = []
        for archive_file in validated_files:
            if query.start_us is not None and archive_file.last_timestamp_us < query.start_us:
                continue
            if query.end_us is not None and archive_file.first_timestamp_us >= query.end_us:
                continue

            zip_path, _ = build_local_path(self.archive_dir, query.symbol, archive_file.date)
            candles = self._read_archive_file(zip_path, archive_file.date)
            all_candles.extend(candles)

        if not all_candles:
            raise SourceError(f"No candles found for {query.symbol} in the requested time range")

        all_candles.sort(key=lambda c: c.open_time_us)

        if query.start_us is not None:
            all_candles = [c for c in all_candles if c.open_time_us >= query.start_us]
        if query.end_us is not None:
            all_candles = [c for c in all_candles if c.open_time_us < query.end_us]

        if not all_candles:
            raise SourceError(f"No candles in the requested time range for {query.symbol}")

        if query.cutoff_us is not None:
            all_candles = [c for c in all_candles if c.close_time_us < query.cutoff_us]

        if not all_candles:
            raise SourceError(f"No closed candles before cutoff for {query.symbol}")

        return CandleBatch(
            symbol=query.symbol,
            interval="1s",
            candles=all_candles,
            source="binance-archive",
            timestamp_unit="us",
            cutoff_us=query.cutoff_us,
        )

    def _read_archive_file(self, zip_path: str, date: str) -> List[Candle]:
        """Read candles from a local archive ZIP file."""
        timestamp_unit = detect_timestamp_unit(date)
        interval_us = interval_to_us("1s")

        with ZipFile(zip_path, "r") as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                content = f.read().decode("utf-8")

        candles = parse_kline_csv(content, timestamp_unit)
        return candles

    def close(self) -> None:
        self.metadata_store.close()
