"""Tests for the 1s archive scraper and validator."""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

from scanner_v2.archive import (
    ARCHIVE_BASE_URL,
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
from scanner_v2.models import Candle, CandleBatch, CandleQuery, CandleIntegrityError


class TestArchiveUrlConstruction(unittest.TestCase):
    """Tests for URL construction."""

    def test_build_archive_url_btcusdt(self):
        zip_url, checksum_url = build_archive_url("BTCUSDT", "2026-08-10")
        self.assertEqual(zip_url, f"{ARCHIVE_BASE_URL}/BTCUSDT/1s/BTCUSDT-1s-2026-08-10.zip")
        self.assertEqual(checksum_url, f"{ARCHIVE_BASE_URL}/BTCUSDT/1s/BTCUSDT-1s-2026-08-10.CHECKSUM")

    def test_build_archive_url_eigenusdc(self):
        zip_url, checksum_url = build_archive_url("EIGENUSDC", "2025-06-15")
        self.assertEqual(zip_url, f"{ARCHIVE_BASE_URL}/EIGENUSDC/1s/EIGENUSDC-1s-2025-06-15.zip")
        self.assertEqual(checksum_url, f"{ARCHIVE_BASE_URL}/EIGENUSDC/1s/EIGENUSDC-1s-2025-06-15.CHECKSUM")

    def test_build_archive_url_format(self):
        """Verify URL format matches Binance archive structure."""
        zip_url, checksum_url = build_archive_url("ETHUSDT", "2025-01-01")
        self.assertTrue(zip_url.startswith("https://data.binance.vision/data/spot/daily/klines/"))
        self.assertTrue(zip_url.endswith(".zip"))
        self.assertTrue(checksum_url.endswith(".CHECKSUM"))


class TestLocalPathConstruction(unittest.TestCase):
    """Tests for local file path construction."""

    def test_build_local_path(self):
        zip_path, checksum_path = build_local_path("data/binance_1s", "BTCUSDT", "2026-08-10")
        expected_base = Path("data/binance_1s/raw/spot/daily/klines/BTCUSDT/1s")
        self.assertTrue(zip_path.startswith(str(expected_base)))
        self.assertTrue(zip_path.endswith("BTCUSDT-1s-2026-08-10.zip"))
        self.assertTrue(checksum_path.endswith("BTCUSDT-1s-2026-08-10.CHECKSUM"))

    def test_build_local_path_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_dir = os.path.join(tmpdir, "archive")
            zip_path, checksum_path = build_local_path(archive_dir, "TESTUSDT", "2025-01-01")
            self.assertTrue(os.path.exists(os.path.dirname(zip_path)))


class TestChecksumParsing(unittest.TestCase):
    """Tests for Binance CHECKSUM file parsing."""

    def test_parse_checksum_file_standard(self):
        content = "abc123def456  BTCUSDT-1s-2026-08-10.zip\n"
        result = parse_checksum_file(content)
        self.assertEqual(result, "abc123def456")

    def test_parse_checksum_file_with_whitespace(self):
        content = "  abc123def456   BTCUSDT-1s-2026-08-10.zip  \n"
        result = parse_checksum_file(content)
        self.assertEqual(result, "abc123def456")

    def test_parse_checksum_file_multiple_lines(self):
        content = "abc123def456  file1.zip\nxyz789  file2.zip\n"
        result = parse_checksum_file(content)
        self.assertEqual(result, "abc123def456")

    def test_parse_checksum_file_empty(self):
        with self.assertRaises(ValueError):
            parse_checksum_file("")


class TestTimestampUnitDetection(unittest.TestCase):
    """Tests for timestamp unit detection based on file date."""

    def test_microseconds_2025(self):
        self.assertEqual(detect_timestamp_unit("2025-01-01"), "us")

    def test_microseconds_2025_later(self):
        self.assertEqual(detect_timestamp_unit("2026-08-10"), "us")

    def test_milliseconds_2024(self):
        self.assertEqual(detect_timestamp_unit("2024-12-31"), "ms")

    def test_milliseconds_2023(self):
        self.assertEqual(detect_timestamp_unit("2023-06-15"), "ms")


class TestKlineCsvParsing(unittest.TestCase):
    """Tests for Binance kline CSV parsing."""

    def test_parse_kline_csv_microseconds(self):
        """Test parsing with microsecond timestamps (2025+)."""
        csv_content = "1735689600000000,100.0,101.0,99.0,100.5,1000.0,1735689660000000,100500.0,10,500.0,505.0,0\n"
        candles = parse_kline_csv(csv_content, "us")
        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.open_time_us, 1735689600000000)
        self.assertEqual(candle.close_time_us, 1735689660000000)
        self.assertEqual(candle.open, Decimal("100.0"))
        self.assertEqual(candle.high, Decimal("101.0"))
        self.assertEqual(candle.low, Decimal("99.0"))
        self.assertEqual(candle.close, Decimal("100.5"))
        self.assertEqual(candle.volume, Decimal("1000.0"))
        self.assertEqual(candle.trade_count, 10)

    def test_parse_kline_csv_milliseconds(self):
        """Test parsing with millisecond timestamps (pre-2025)."""
        csv_content = "1704067200000,100.0,101.0,99.0,100.5,1000.0,1704067260000,100500.0,10,500.0,505.0,0\n"
        candles = parse_kline_csv(csv_content, "ms")
        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.open_time_us, 1704067200000000)
        self.assertEqual(candle.close_time_us, 1704067260000000)

    def test_parse_kline_csv_multiple_rows(self):
        csv_content = (
            "1735689600000000,100.0,101.0,99.0,100.5,1000.0,1735689660000000,100500.0,10,500.0,505.0,0\n"
            "1735689660000000,100.5,102.0,100.0,101.0,800.0,1735689720000000,80800.0,8,400.0,404.0,0\n"
        )
        candles = parse_kline_csv(csv_content, "us")
        self.assertEqual(len(candles), 2)

    def test_parse_kline_csv_malformed_row(self):
        csv_content = "1735689600000000,100.0,101.0,99.0\n"
        with self.assertRaises(CandleIntegrityError):
            parse_kline_csv(csv_content, "us")

    def test_parse_kline_csv_invalid_decimal(self):
        csv_content = "1735689600000000,invalid,101.0,99.0,100.5,1000.0,1735689660000000,100500.0,10,500.0,505.0,0\n"
        with self.assertRaises(CandleIntegrityError):
            parse_kline_csv(csv_content, "us")


class TestCandleValidation(unittest.TestCase):
    """Tests for candle sequence validation."""

    def _make_candle(self, open_time_us: int, interval_us: int = 1_000_000) -> Candle:
        return Candle(
            open_time_us=open_time_us,
            close_time_us=open_time_us + interval_us - 1,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("1000"),
            trade_count=10,
        )

    def test_validate_candles_no_gaps_no_duplicates(self):
        candles = [self._make_candle(i * 1_000_000) for i in range(10)]
        has_gaps, has_duplicates, first_ts, last_ts = validate_candles(candles, 1_000_000)
        self.assertFalse(has_gaps)
        self.assertFalse(has_duplicates)
        self.assertEqual(first_ts, 0)
        self.assertEqual(last_ts, 9_999_999)  # close_time_us of last candle

    def test_validate_candles_with_gap(self):
        candles = [self._make_candle(i * 1_000_000) for i in range(5)]
        candles.append(self._make_candle(10_000_000))  # Gap
        has_gaps, has_duplicates, _, _ = validate_candles(candles, 1_000_000)
        self.assertTrue(has_gaps)
        self.assertFalse(has_duplicates)

    def test_validate_candles_with_duplicate(self):
        candles = [self._make_candle(i * 1_000_000) for i in range(5)]
        # Add a duplicate at the end (same timestamp as candle 4)
        candles.append(self._make_candle(4_000_000))
        # Sort to avoid gap detection from unsorted list
        candles.sort(key=lambda c: c.open_time_us)
        has_gaps, has_duplicates, _, _ = validate_candles(candles, 1_000_000)
        # A duplicate creates both a gap (missing next interval) and a duplicate
        self.assertTrue(has_gaps)
        self.assertTrue(has_duplicates)

    def test_validate_candles_empty(self):
        has_gaps, has_duplicates, first_ts, last_ts = validate_candles([], 1_000_000)
        self.assertFalse(has_gaps)
        self.assertFalse(has_duplicates)
        self.assertEqual(first_ts, 0)
        self.assertEqual(last_ts, 0)


class TestArchiveMetadataStore(unittest.TestCase):
    """Tests for the SQLite metadata store."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_archive.db")
        self.store = ArchiveMetadataStore(self.db_path)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_upsert_and_get_file(self):
        record = ArchiveFile(
            symbol="BTCUSDT",
            date="2026-08-10",
            zip_path="/data/BTCUSDT-1s-2026-08-10.zip",
            checksum_path="/data/BTCUSDT-1s-2026-08-10.CHECKSUM",
            local_sha256="abc123",
            expected_checksum="abc123",
            row_count=86400,
            first_timestamp_us=1735689600000000,
            last_timestamp_us=1735775999000000,
            has_gaps=False,
            has_duplicates=False,
            downloaded_at_us=1735689600000000,
            validated=True,
        )
        self.store.upsert_file(record)
        retrieved = self.store.get_file("BTCUSDT", "2026-08-10")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.symbol, "BTCUSDT")
        self.assertEqual(retrieved.date, "2026-08-10")
        self.assertTrue(retrieved.validated)

    def test_get_validated_files(self):
        record1 = ArchiveFile(
            symbol="BTCUSDT", date="2026-08-10", zip_path="p1", checksum_path="c1",
            local_sha256="h1", expected_checksum="h1", row_count=86400,
            first_timestamp_us=1, last_timestamp_us=2, has_gaps=False,
            has_duplicates=False, downloaded_at_us=1, validated=True,
        )
        record2 = ArchiveFile(
            symbol="BTCUSDT", date="2026-08-11", zip_path="p2", checksum_path="c2",
            local_sha256="h2", expected_checksum="h2", row_count=86400,
            first_timestamp_us=3, last_timestamp_us=4, has_gaps=True,
            has_duplicates=False, downloaded_at_us=2, validated=False,
        )
        self.store.upsert_file(record1)
        self.store.upsert_file(record2)
        validated = self.store.get_validated_files("BTCUSDT")
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0].date, "2026-08-10")


class TestArchiveCandleSource(unittest.TestCase):
    """Tests for the ArchiveCandleSource."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.archive_dir = os.path.join(self.tmpdir, "archive")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_fetch_no_validated_files(self):
        source = ArchiveCandleSource(self.archive_dir, self.db_path)
        query = CandleQuery(
            symbol="BTCUSDT", interval="1s", cutoff_us=1735689600000000,
            start_us=1735689600000000, end_us=1735775999000000,
        )
        with self.assertRaises(Exception):
            source.fetch(query)
        source.close()

    def test_fetch_wrong_interval(self):
        source = ArchiveCandleSource(self.archive_dir, self.db_path)
        query = CandleQuery(
            symbol="BTCUSDT", interval="1m", cutoff_us=1735689600000000,
        )
        with self.assertRaises(ValueError):
            source.fetch(query)
        source.close()


if __name__ == "__main__":
    unittest.main()
