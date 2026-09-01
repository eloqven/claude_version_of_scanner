"""Tests for the Fibonacci Matrix V3 research engine."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from scanner_v2.fib_matrix import (
    ConfluenceZone,
    EventType,
    FibMatrix,
    MatrixElement,
    ReactionMetrics,
    V3Event,
    V3EventStore,
    log_event_json,
    log_summary_json,
    FIB_INTERVALS,
    MA_TYPES,
    MA_PERIODS,
    REACTION_WINDOWS_S,
    CLUSTER_WIDTH_ATR_MULT,
    MIN_CLUSTER_MEMBERS,
)
from scanner_v2.archive import ArchiveCandleSource
from scanner_v2.models import Candle, CandleBatch, CandleQuery, interval_to_us


def make_candle(index: int, *, interval_us: int = 60_000_000,
                open_: str = "100", high: str = "101", low: str = "99",
                close: str = "100.5") -> Candle:
    """Create a test candle at the given index."""
    open_us = index * interval_us
    return Candle(
        open_time_us=open_us,
        close_time_us=open_us + interval_us - 1,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        trade_count=10,
    )


class TestMatrixConstants(unittest.TestCase):
    """Tests for matrix configuration constants."""

    def test_fib_intervals(self):
        self.assertEqual(FIB_INTERVALS, ["5m", "8m", "13m", "21m", "34m", "55m"])

    def test_ma_types(self):
        self.assertEqual(MA_TYPES, ["EMA", "WMA", "SMA"])

    def test_ma_periods(self):
        self.assertEqual(MA_PERIODS, [5, 8, 13, 21, 34, 55])

    def test_matrix_size(self):
        """Verify 108 elements per evaluation point."""
        self.assertEqual(len(FIB_INTERVALS) * len(MA_TYPES) * len(MA_PERIODS), 108)

    def test_reaction_windows(self):
        self.assertEqual(REACTION_WINDOWS_S, [60, 300, 900, 1800, 3600, 14400])

    def test_cluster_config(self):
        self.assertEqual(CLUSTER_WIDTH_ATR_MULT, Decimal("0.10"))
        self.assertEqual(MIN_CLUSTER_MEMBERS, 4)


class TestEventType(unittest.TestCase):
    """Tests for event type enum."""

    def test_event_types(self):
        self.assertEqual(EventType.SUPPORT_REJECTION.value, "SUPPORT_REJECTION")
        self.assertEqual(EventType.RESISTANCE_REJECTION.value, "RESISTANCE_REJECTION")
        self.assertEqual(EventType.BREAK_UP.value, "BREAK_UP")
        self.assertEqual(EventType.BREAK_DOWN.value, "BREAK_DOWN")
        self.assertEqual(EventType.TOUCH_ONLY.value, "TOUCH_ONLY")


class TestMatrixElement(unittest.TestCase):
    """Tests for MatrixElement dataclass."""

    def test_create_matrix_element(self):
        element = MatrixElement(
            interval="5m",
            ma_type="EMA",
            period=13,
            value=Decimal("100.5"),
            timestamp_us=1735689600000000,
        )
        self.assertEqual(element.interval, "5m")
        self.assertEqual(element.ma_type, "EMA")
        self.assertEqual(element.period, 13)
        self.assertEqual(element.value, Decimal("100.5"))


class TestConfluenceZone(unittest.TestCase):
    """Tests for ConfluenceZone dataclass."""

    def test_create_zone(self):
        members = [
            MatrixElement("5m", "EMA", 5, Decimal("100.0"), 1),
            MatrixElement("5m", "SMA", 5, Decimal("100.1"), 1),
            MatrixElement("8m", "EMA", 5, Decimal("100.2"), 1),
            MatrixElement("8m", "SMA", 5, Decimal("100.3"), 1),
        ]
        zone = ConfluenceZone(
            low=Decimal("100.0"),
            high=Decimal("100.3"),
            mid=Decimal("100.15"),
            width=Decimal("0.3"),
            members=members,
            interval_diversity=2,
            period_diversity=1,
            ma_type_diversity=2,
        )
        self.assertEqual(zone.low, Decimal("100.0"))
        self.assertEqual(zone.high, Decimal("100.3"))
        self.assertEqual(len(zone.members), 4)


class TestReactionMetrics(unittest.TestCase):
    """Tests for ReactionMetrics dataclass."""

    def test_create_reaction_metrics(self):
        metrics = ReactionMetrics(
            window_s=60,
            return_pct=Decimal("1.5"),
            mfe_pct=Decimal("2.0"),
            mae_pct=Decimal("-1.0"),
            first_touch_time_s=5,
            time_inside_zone_pct=Decimal("50.0"),
            penetration_depth_pct=Decimal("0.5"),
            crossing_count=2,
        )
        self.assertEqual(metrics.window_s, 60)
        self.assertEqual(metrics.return_pct, Decimal("1.5"))


class TestV3Event(unittest.TestCase):
    """Tests for V3Event dataclass."""

    def test_create_event(self):
        zone = ConfluenceZone(
            low=Decimal("100.0"), high=Decimal("100.3"), mid=Decimal("100.15"),
            width=Decimal("0.3"), members=[], interval_diversity=2,
            period_diversity=1, ma_type_diversity=2,
        )
        metrics = [ReactionMetrics(60, Decimal("1.0"), Decimal("2.0"), Decimal("-1.0"), 5, Decimal("50"), Decimal("0.5"), 2)]
        event = V3Event(
            event_type=EventType.SUPPORT_REJECTION,
            timestamp_us=1735689600000000,
            symbol="BTCUSDT",
            zone=zone,
            reaction_metrics=metrics,
            matrix_elements=[],
        )
        self.assertEqual(event.event_type, EventType.SUPPORT_REJECTION)
        self.assertEqual(event.symbol, "BTCUSDT")


class TestFibMatrix(unittest.TestCase):
    """Tests for the FibMatrix engine."""

    def setUp(self):
        self.mock_source = mock.MagicMock(spec=ArchiveCandleSource)
        self.matrix = FibMatrix(self.mock_source)

    def test_compute_ma_sma(self):
        """Test SMA calculation."""
        candles = [make_candle(i) for i in range(10)]
        result = self.matrix._compute_ma(candles, "SMA", 5)
        self.assertIsNotNone(result)
        # SMA of last 5 closes: (100.5 + 100.5 + 100.5 + 100.5 + 100.5) / 5 = 100.5
        self.assertEqual(result, Decimal("100.5"))

    def test_compute_ma_ema(self):
        """Test EMA calculation."""
        candles = [make_candle(i) for i in range(10)]
        result = self.matrix._compute_ma(candles, "EMA", 5)
        self.assertIsNotNone(result)

    def test_compute_ma_wma(self):
        """Test WMA calculation."""
        candles = [make_candle(i) for i in range(10)]
        result = self.matrix._compute_ma(candles, "WMA", 5)
        self.assertIsNotNone(result)

    def test_compute_ma_insufficient_data(self):
        """Test MA with insufficient data."""
        candles = [make_candle(i) for i in range(3)]
        result = self.matrix._compute_ma(candles, "SMA", 5)
        self.assertIsNone(result)

    def test_compute_ma_unknown_type(self):
        """Test unknown MA type raises error."""
        candles = [make_candle(i) for i in range(10)]
        with self.assertRaises(ValueError):
            self.matrix._compute_ma(candles, "UNKNOWN", 5)

    def test_cluster_zones_empty(self):
        """Test clustering with no elements."""
        zones = self.matrix.cluster_zones([], Decimal("1.0"))
        self.assertEqual(len(zones), 0)

    def test_cluster_zones_single_cluster(self):
        """Test clustering with elements that form one zone."""
        elements = [
            MatrixElement("5m", "EMA", 5, Decimal("100.0"), 1),
            MatrixElement("5m", "SMA", 5, Decimal("100.1"), 1),
            MatrixElement("8m", "EMA", 5, Decimal("100.2"), 1),
            MatrixElement("8m", "SMA", 5, Decimal("100.3"), 1),
        ]
        zones = self.matrix.cluster_zones(elements, Decimal("1.0"))
        self.assertEqual(len(zones), 1)
        self.assertEqual(len(zones[0].members), 4)

    def test_cluster_zones_multiple_clusters(self):
        """Test clustering with elements that form multiple zones."""
        elements = [
            MatrixElement("5m", "EMA", 5, Decimal("100.0"), 1),
            MatrixElement("5m", "SMA", 5, Decimal("100.1"), 1),
            MatrixElement("8m", "EMA", 5, Decimal("100.2"), 1),
            MatrixElement("8m", "SMA", 5, Decimal("100.3"), 1),
            MatrixElement("5m", "EMA", 8, Decimal("200.0"), 1),
            MatrixElement("5m", "SMA", 8, Decimal("200.1"), 1),
            MatrixElement("8m", "EMA", 8, Decimal("200.2"), 1),
            MatrixElement("8m", "SMA", 8, Decimal("200.3"), 1),
        ]
        zones = self.matrix.cluster_zones(elements, Decimal("1.0"))
        self.assertEqual(len(zones), 2)


class TestEventDetection(unittest.TestCase):
    """Tests for event detection logic."""

    def setUp(self):
        self.mock_source = mock.MagicMock(spec=ArchiveCandleSource)
        self.matrix = FibMatrix(self.mock_source)

    def test_detect_events_empty_zones(self):
        """Test event detection with no zones."""
        candles = [make_candle(i) for i in range(10)]
        events = self.matrix.detect_events([], candles, "TEST")
        self.assertEqual(len(events), 0)

    def test_detect_events_no_touch(self):
        """Test event detection when price doesn't touch zone."""
        zone = ConfluenceZone(
            low=Decimal("200.0"), high=Decimal("200.3"), mid=Decimal("200.15"),
            width=Decimal("0.3"), members=[], interval_diversity=2,
            period_diversity=1, ma_type_diversity=2,
        )
        candles = [make_candle(i) for i in range(10)]  # All around 100
        events = self.matrix.detect_events([zone], candles, "TEST")
        self.assertEqual(len(events), 0)

    def test_detect_events_support_rejection(self):
        """Test support rejection detection."""
        zone = ConfluenceZone(
            low=Decimal("99.0"), high=Decimal("100.0"), mid=Decimal("99.5"),
            width=Decimal("1.0"), members=[], interval_diversity=2,
            period_diversity=1, ma_type_diversity=2,
        )
        # Candle that touches zone low and bounces up
        candles = [
            make_candle(0, open_="99.5", low="98.0", high="99.5", close="99.5"),
            make_candle(1, open_="99.5", low="99.0", high="100.5", close="100.5"),
        ]
        events = self.matrix.detect_events([zone], candles, "TEST")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.SUPPORT_REJECTION)


class TestV3EventStore(unittest.TestCase):
    """Tests for the V3 event store."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_v3.db")
        self.store = V3EventStore(self.db_path)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_record_event(self):
        """Test recording an event."""
        zone = ConfluenceZone(
            low=Decimal("100.0"), high=Decimal("100.3"), mid=Decimal("100.15"),
            width=Decimal("0.3"), members=[], interval_diversity=2,
            period_diversity=1, ma_type_diversity=2,
        )
        metrics = [ReactionMetrics(60, Decimal("1.0"), Decimal("2.0"), Decimal("-1.0"), 5, Decimal("50"), Decimal("0.5"), 2)]
        event = V3Event(
            event_type=EventType.SUPPORT_REJECTION,
            timestamp_us=1735689600000000,
            symbol="BTCUSDT",
            zone=zone,
            reaction_metrics=metrics,
            matrix_elements=[],
        )
        event_id = self.store.record_event(event)
        self.assertIsNotNone(event_id)

        # Verify it was stored
        row = self.store.connection.execute(
            "SELECT symbol, event_type FROM v3_events WHERE id=?", (event_id,)
        ).fetchone()
        self.assertEqual(row[0], "BTCUSDT")
        self.assertEqual(row[1], "SUPPORT_REJECTION")


class TestJsonLogging(unittest.TestCase):
    """Tests for JSON logging functions."""

    def test_log_event_json(self):
        """Test event JSON logging."""
        zone = ConfluenceZone(
            low=Decimal("100.0"), high=Decimal("100.3"), mid=Decimal("100.15"),
            width=Decimal("0.3"), members=[], interval_diversity=2,
            period_diversity=1, ma_type_diversity=2,
        )
        metrics = [ReactionMetrics(60, Decimal("1.0"), Decimal("2.0"), Decimal("-1.0"), 5, Decimal("50"), Decimal("0.5"), 2)]
        event = V3Event(
            event_type=EventType.SUPPORT_REJECTION,
            timestamp_us=1735689600000000,
            symbol="BTCUSDT",
            zone=zone,
            reaction_metrics=metrics,
            matrix_elements=[],
        )
        json_str = log_event_json(event)
        data = json.loads(json_str)
        self.assertEqual(data["type"], "V3_EVENT")
        self.assertEqual(data["symbol"], "BTCUSDT")
        self.assertEqual(data["event_type"], "SUPPORT_REJECTION")

    def test_log_summary_json(self):
        """Test summary JSON logging."""
        zone = ConfluenceZone(
            low=Decimal("100.0"), high=Decimal("100.3"), mid=Decimal("100.15"),
            width=Decimal("0.3"), members=[], interval_diversity=2,
            period_diversity=1, ma_type_diversity=2,
        )
        metrics = [ReactionMetrics(60, Decimal("1.0"), Decimal("2.0"), Decimal("-1.0"), 5, Decimal("50"), Decimal("0.5"), 2)]
        event = V3Event(
            event_type=EventType.SUPPORT_REJECTION,
            timestamp_us=1735689600000000,
            symbol="BTCUSDT",
            zone=zone,
            reaction_metrics=metrics,
            matrix_elements=[],
        )
        json_str = log_summary_json("BTCUSDT", [event], 100)
        data = json.loads(json_str)
        self.assertEqual(data["type"], "V3_SUMMARY")
        self.assertEqual(data["symbol"], "BTCUSDT")
        self.assertEqual(data["total_evaluations"], 100)
        self.assertEqual(data["total_events"], 1)
        self.assertEqual(data["event_counts"]["SUPPORT_REJECTION"], 1)


class TestDeterministicFixture(unittest.TestCase):
    """Tests using deterministic fixtures."""

    def test_deterministic_1m_resample(self):
        """Test that 1s data resamples to 1m correctly."""
        # Create 60 1-second candles that should resample to 1 1-minute candle
        candles_1s = [make_candle(i, interval_us=1_000_000) for i in range(60)]
        batch = CandleBatch(
            symbol="TEST",
            interval="1s",
            candles=candles_1s,
            source="test",
            timestamp_unit="us",
            cutoff_us=60_000_000,
        )
        from scanner_v2.sources import resample_candles
        resampled = resample_candles(batch, "1m")
        self.assertEqual(len(resampled.candles), 1)

    def test_matrix_values_match_fixture(self):
        """Test that matrix values match expected fixture calculations."""
        mock_source = mock.MagicMock(spec=ArchiveCandleSource)
        matrix = FibMatrix(mock_source)

        # Create deterministic candles
        candles = [make_candle(i, interval_us=60_000_000) for i in range(20)]

        # Test SMA calculation
        sma = matrix._compute_ma(candles, "SMA", 5)
        expected = sum(c.close for c in candles[-5:]) / Decimal("5")
        self.assertEqual(sma, expected)

    def test_confluence_clustering_groups_close_levels(self):
        """Test that confluence clustering groups close levels."""
        mock_source = mock.MagicMock(spec=ArchiveCandleSource)
        matrix = FibMatrix(mock_source)

        # Create elements with close values
        elements = [
            MatrixElement("5m", "EMA", 5, Decimal("100.0"), 1),
            MatrixElement("5m", "SMA", 5, Decimal("100.05"), 1),
            MatrixElement("8m", "EMA", 5, Decimal("100.1"), 1),
            MatrixElement("8m", "SMA", 5, Decimal("100.15"), 1),
        ]
        zones = matrix.cluster_zones(elements, Decimal("1.0"))
        self.assertEqual(len(zones), 1)
        self.assertEqual(len(zones[0].members), 4)

    def test_confluence_clustering_rejects_weak_clusters(self):
        """Test that clusters with fewer than MIN_CLUSTER_MEMBERS are rejected."""
        mock_source = mock.MagicMock(spec=ArchiveCandleSource)
        matrix = FibMatrix(mock_source)

        # Create elements with only 3 members (below minimum of 4)
        elements = [
            MatrixElement("5m", "EMA", 5, Decimal("100.0"), 1),
            MatrixElement("5m", "SMA", 5, Decimal("100.05"), 1),
            MatrixElement("8m", "EMA", 5, Decimal("100.1"), 1),
        ]
        zones = matrix.cluster_zones(elements, Decimal("1.0"))
        self.assertEqual(len(zones), 0)


def make_1s_day_batch(minutes: int = 120, day_start_us: int = 0) -> CandleBatch:
    """Build a day-aligned 1s CandleBatch for resample integration tests."""
    from scanner_v2.models import interval_to_us
    interval_us = interval_to_us("1s")
    candles = [
        Candle(
            open_time_us=day_start_us + i * interval_us,
            close_time_us=day_start_us + i * interval_us + interval_us - 1,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("1000"),
            trade_count=10,
        )
        for i in range(minutes * 60)
    ]
    return CandleBatch(
        symbol="TEST",
        interval="1s",
        candles=candles,
        source="test",
        timestamp_unit="us",
        cutoff_us=day_start_us + minutes * 60 * interval_us,
    )


def make_resampled_batch(interval: str, count: int) -> CandleBatch:
    """Build a resampled CandleBatch (already at the fib interval) for tests."""
    from scanner_v2.models import interval_to_us
    interval_us = interval_to_us(interval)
    candles = []
    for i in range(count):
        open_us = i * interval_us
        close = Decimal(str(100 + i * 0.01))
        candles.append(Candle(
            open_time_us=open_us,
            close_time_us=open_us + interval_us - 1,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("1000"),
            trade_count=10,
        ))
    return CandleBatch(
        symbol="TEST",
        interval=interval,
        candles=candles,
        source="test:resampled",
        timestamp_unit="us",
        cutoff_us=count * interval_us,
    )


class TestBuildMatrixIntegration(unittest.TestCase):
    """Integration tests for build_matrix source->resample->matrix path."""

    def test_build_matrix_from_1s_source_returns_elements(self):
        """build_matrix must no longer be a silent no-op over a 1s archive.

        Regression test for the CRITICAL review finding: a real 1s
        ArchiveCandleSource must yield matrix elements (previously every
        interval raised ValueError and was swallowed, producing []).
        """
        batch = make_1s_day_batch(minutes=120)
        mock_source = mock.MagicMock(spec=ArchiveCandleSource)
        mock_source.fetch.return_value = batch

        matrix = FibMatrix(mock_source)
        timestamp_us = 120 * 60 * 1_000_000
        elements = matrix.build_matrix("BTCUSDT", timestamp_us)

        self.assertGreater(len(elements), 0)
        self.assertTrue(any(e.interval == "5m" for e in elements))
        # Verify the values are real MAs, not zeros.
        self.assertTrue(all(e.value > 0 for e in elements))

    def test_build_matrix_resampled_dict_returns_108(self):
        """A precomputed resample mapping yields the full 108-element matrix."""
        resampled_by_interval = {iv: make_resampled_batch(iv, 60) for iv in FIB_INTERVALS}
        matrix = FibMatrix(mock.MagicMock(spec=ArchiveCandleSource))
        timestamp_us = 60 * interval_to_us("55m")
        elements = matrix.build_matrix("BTCUSDT", timestamp_us,
                                       resampled_by_interval=resampled_by_interval)
        self.assertEqual(len(elements), 108)

    def test_end_to_end_symbol_threaded_through_event(self):
        """build_matrix -> detect_events -> record_event stores the real symbol.

        Regression test for the HIGH review finding: the event symbol used to
        be set to an interval string instead of the trading symbol.
        """
        resampled_by_interval = {iv: make_resampled_batch(iv, 60) for iv in FIB_INTERVALS}
        matrix = FibMatrix(mock.MagicMock(spec=ArchiveCandleSource))
        timestamp_us = 60 * interval_to_us("55m")
        elements = matrix.build_matrix("BTCUSDT", timestamp_us,
                                       resampled_by_interval=resampled_by_interval)
        self.assertGreater(len(elements), 0)

        atr = Decimal("1000")
        zones = matrix.cluster_zones(elements, atr)
        self.assertGreater(len(zones), 0)

        # Candles that touch the zone so an event is emitted.
        candles = [
            make_candle(0, open_="100.0", low="99.0", high="101.0", close="100.5"),
            make_candle(1, open_="100.5", low="100.0", high="101.5", close="101.0"),
        ]
        events = matrix.detect_events(zones, candles, "BTCUSDT")
        self.assertGreater(len(events), 0)
        self.assertEqual(events[0].symbol, "BTCUSDT")

        tmpdir = tempfile.mkdtemp()
        try:
            store = V3EventStore(os.path.join(tmpdir, "e2e.db"))
            event_id = store.record_event(events[0])
            row = store.connection.execute(
                "SELECT symbol FROM v3_events WHERE id=?", (event_id,)
            ).fetchone()
            store.close()
            self.assertEqual(row[0], "BTCUSDT")
        finally:
            import shutil
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
