"""Fibonacci confluence matrix V3 research engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple
import json
import sqlite3
from pathlib import Path

import pandas as pd

from .models import (
    Candle,
    CandleBatch,
    CandleQuery,
    IndicatorFrame,
    IndicatorSpec,
    interval_to_us,
)
from .archive import ArchiveCandleSource
from .indicators import IndicatorEngine


FIB_INTERVALS = ["5m", "8m", "13m", "21m", "34m", "55m"]
MA_TYPES = ["EMA", "WMA", "SMA"]
MA_PERIODS = [5, 8, 13, 21, 34, 55]
REACTION_WINDOWS_S = [60, 300, 900, 1800, 3600, 14400]
CLUSTER_WIDTH_ATR_MULT = Decimal("0.10")
MIN_CLUSTER_MEMBERS = 4


class EventType(str, Enum):
    SUPPORT_REJECTION = "SUPPORT_REJECTION"
    RESISTANCE_REJECTION = "RESISTANCE_REJECTION"
    BREAK_UP = "BREAK_UP"
    BREAK_DOWN = "BREAK_DOWN"
    TOUCH_ONLY = "TOUCH_ONLY"


@dataclass(frozen=True)
class MatrixElement:
    """A single element in the Fibonacci matrix."""
    interval: str
    ma_type: str
    period: int
    value: Decimal
    timestamp_us: int


@dataclass(frozen=True)
class ConfluenceZone:
    """A cluster of Fibonacci levels that are close together."""
    low: Decimal
    high: Decimal
    mid: Decimal
    width: Decimal
    members: List[MatrixElement]
    interval_diversity: int
    period_diversity: int
    ma_type_diversity: int


@dataclass(frozen=True)
class ReactionMetrics:
    """Metrics measuring price reaction after an event."""
    window_s: int
    return_pct: Decimal
    mfe_pct: Decimal
    mae_pct: Decimal
    first_touch_time_s: Optional[int]
    time_inside_zone_pct: Decimal
    penetration_depth_pct: Decimal
    crossing_count: int


@dataclass(frozen=True)
class V3Event:
    """A Fibonacci confluence event."""
    event_type: EventType
    timestamp_us: int
    symbol: str
    zone: ConfluenceZone
    reaction_metrics: List[ReactionMetrics]
    matrix_elements: List[MatrixElement]


class FibMatrix:
    """Builds and evaluates Fibonacci interval x period MA matrices."""

    def __init__(self, archive_source: ArchiveCandleSource) -> None:
        self.archive_source = archive_source
        self.indicator_engine = IndicatorEngine()

    def build_matrix(self, symbol: str, timestamp_us: int,
                      lookback_candles: int = 100,
                      resampled_by_interval: Optional[Dict[str, CandleBatch]] = None
                      ) -> List[MatrixElement]:
        """Build the 108-element matrix at a given timestamp.

        The Fibonacci matrix resamples a 1s source to each fib interval and
        computes the latest closed MA value at ``timestamp_us``.

        ``resampled_by_interval`` is an optional precomputed mapping of
        ``interval -> CandleBatch`` already resampled to that fib interval and
        aligned to UTC epoch buckets. When omitted, ``build_matrix`` fetches the
        day's 1s batch from the archive source and resamples it locally. Callers
        that evaluate many timestamps within the same day should precompute and
        pass this mapping to avoid re-fetching and re-resampling per evaluation.
        """
        elements: List[MatrixElement] = []

        from scanner_v2.sources import resample_candles

        if resampled_by_interval is None:
            batch_1s = self._fetch_day_1s(symbol, timestamp_us)
            if batch_1s is None:
                return elements
            resampled_by_interval = {}
            for interval in FIB_INTERVALS:
                try:
                    resampled_by_interval[interval] = resample_candles(batch_1s, interval)
                except Exception:
                    continue

        for interval, resampled in resampled_by_interval.items():
            candles = [c for c in resampled.candles if c.close_time_us < timestamp_us]
            if not candles:
                continue

            for ma_type in MA_TYPES:
                for period in MA_PERIODS:
                    if len(candles) < period:
                        continue

                    value = self._compute_ma(candles, ma_type, period)
                    if value is not None:
                        elements.append(MatrixElement(
                            interval=interval,
                            ma_type=ma_type,
                            period=period,
                            value=value,
                            timestamp_us=timestamp_us,
                        ))

        return elements

    def _fetch_day_1s(self, symbol: str, timestamp_us: int) -> Optional[CandleBatch]:
        """Fetch the 1s day batch containing ``timestamp_us`` from the archive."""
        day_us = interval_to_us("1d")
        day_start_us = timestamp_us - (timestamp_us % day_us)
        query = CandleQuery(
            symbol=symbol,
            interval="1s",
            cutoff_us=timestamp_us,
            start_us=day_start_us,
            end_us=day_start_us + day_us,
            limit=2000,
        )
        try:
            return self.archive_source.fetch(query)
        except Exception:
            return None

    def _interval_to_us(self, interval: str) -> int:
        """Convert interval string to microseconds."""
        return interval_to_us(interval)

    def _compute_ma(self, candles: Sequence[Candle], ma_type: str, period: int) -> Optional[Decimal]:
        """Compute a moving average of the specified type and period.

        SMA and WMA are trailing-window averages over the last ``period``
        closed candles. EMA is seeded with the SMA of the first ``period``
        closes and then rolled forward over the full available series (standard
        TA-Lib/TradingView semantics).
        """
        if len(candles) < period:
            return None

        closes = [c.close for c in candles]

        if ma_type == "SMA":
            window = closes[-period:]
            return sum(window, Decimal("0")) / Decimal(period)
        elif ma_type == "EMA":
            return self._compute_ema(closes, period)
        elif ma_type == "WMA":
            window = closes[-period:]
            return self._compute_wma(window, period)
        else:
            raise ValueError(f"Unknown MA type: {ma_type}")

    def _compute_ema(self, values: Sequence[Decimal], period: int) -> Decimal:
        """Compute a standard Exponential Moving Average.

        Seed with the SMA of the first ``period`` values, then roll the
        recursion forward over the entire series.
        """
        if len(values) < period:
            raise ValueError("EMA requires at least `period` values")
        seed = sum(values[:period], Decimal("0")) / Decimal(period)
        multiplier = Decimal("2") / Decimal(period + 1)
        ema = seed
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
        return ema

    def _compute_wma(self, values: Sequence[Decimal], period: int) -> Decimal:
        """Compute a weighted moving average over the trailing ``period`` values."""
        if not values:
            return Decimal("0")
        weights = list(range(1, len(values) + 1))
        weighted_sum = sum(v * w for v, w in zip(values, weights))
        weight_sum = sum(weights)
        return weighted_sum / Decimal(weight_sum)

    def cluster_zones(self, elements: List[MatrixElement],
                      atr: Decimal) -> List[ConfluenceZone]:
        """Cluster matrix elements into confluence zones using ATR scale."""
        if not elements:
            return []

        sorted_elements = sorted(elements, key=lambda e: e.value)
        cluster_width = atr * CLUSTER_WIDTH_ATR_MULT

        zones: List[ConfluenceZone] = []
        current_cluster: List[MatrixElement] = [sorted_elements[0]]

        for element in sorted_elements[1:]:
            if element.value - current_cluster[-1].value <= cluster_width:
                current_cluster.append(element)
            else:
                if len(current_cluster) >= MIN_CLUSTER_MEMBERS:
                    zones.append(self._create_zone(current_cluster))
                current_cluster = [element]

        if len(current_cluster) >= MIN_CLUSTER_MEMBERS:
            zones.append(self._create_zone(current_cluster))

        return zones

    def _create_zone(self, members: List[MatrixElement]) -> ConfluenceZone:
        """Create a ConfluenceZone from a cluster of elements."""
        values = [m.value for m in members]
        low = min(values)
        high = max(values)
        mid = (low + high) / Decimal("2")
        width = high - low

        intervals = set(m.interval for m in members)
        periods = set(m.period for m in members)
        ma_types = set(m.ma_type for m in members)

        return ConfluenceZone(
            low=low,
            high=high,
            mid=mid,
            width=width,
            members=members,
            interval_diversity=len(intervals),
            period_diversity=len(periods),
            ma_type_diversity=len(ma_types),
        )

    def detect_events(self, zones: List[ConfluenceZone],
                      candles: Sequence[Candle], symbol: str) -> List[V3Event]:
        """Detect Fibonacci confluence events from zones and price action."""
        events: List[V3Event] = []

        for zone in zones:
            event = self._classify_zone_event(zone, candles, symbol)
            if event:
                events.append(event)

        return events

    def _classify_zone_event(self, zone: ConfluenceZone,
                            candles: Sequence[Candle],
                            symbol: str) -> Optional[V3Event]:
        """Classify a zone interaction as a specific event type."""
        zone_candles = [
            c for c in candles
            if c.low <= zone.high and c.high >= zone.low
        ]

        if not zone_candles:
            return None

        first_touch = zone_candles[0]
        last_touch = zone_candles[-1]

        # Determine event type based on price action
        if first_touch.low <= zone.low and first_touch.close > zone.low:
            event_type = EventType.SUPPORT_REJECTION
        elif first_touch.high >= zone.high and first_touch.close < zone.high:
            event_type = EventType.RESISTANCE_REJECTION
        elif first_touch.close > zone.high:
            event_type = EventType.BREAK_UP
        elif first_touch.close < zone.low:
            event_type = EventType.BREAK_DOWN
        else:
            event_type = EventType.TOUCH_ONLY

        # Calculate reaction metrics
        reaction_metrics = self._calculate_reaction_metrics(
            zone, first_touch, candles
        )

        return V3Event(
            event_type=event_type,
            timestamp_us=first_touch.open_time_us,
            symbol=symbol if symbol else (zone.members[0].interval if zone.members else "UNKNOWN"),
            zone=zone,
            reaction_metrics=reaction_metrics,
            matrix_elements=zone.members,
        )

    def _calculate_reaction_metrics(self, zone: ConfluenceZone,
                                    touch_candle: Candle,
                                    candles: Sequence[Candle]) -> List[ReactionMetrics]:
        """Calculate reaction metrics for each reaction window."""
        metrics: List[ReactionMetrics] = []
        touch_time = touch_candle.open_time_us

        for window_s in REACTION_WINDOWS_S:
            window_end_us = touch_time + window_s * 1_000_000
            window_candles = [
                c for c in candles
                if touch_time <= c.open_time_us < window_end_us
            ]

            if not window_candles:
                continue

            entry_price = touch_candle.close
            high = max(c.high for c in window_candles)
            low = min(c.low for c in window_candles)
            exit_price = window_candles[-1].close

            return_pct = (exit_price - entry_price) / entry_price * Decimal("100")
            mfe_pct = (high - entry_price) / entry_price * Decimal("100")
            mae_pct = (low - entry_price) / entry_price * Decimal("100")

            # First touch time within zone
            first_touch_time = None
            for c in window_candles:
                if c.low <= zone.high and c.high >= zone.low:
                    first_touch_time = (c.open_time_us - touch_time) // 1_000_000
                    break

            # Time inside zone
            inside_count = sum(
                1 for c in window_candles
                if c.low <= zone.high and c.high >= zone.low
            )
            time_inside_pct = Decimal(inside_count) / Decimal(len(window_candles)) * Decimal("100")

            # Penetration depth
            if entry_price > zone.mid:
                penetration = (high - zone.high) / zone.width * Decimal("100") if zone.width > 0 else Decimal("0")
            else:
                penetration = (zone.low - low) / zone.width * Decimal("100") if zone.width > 0 else Decimal("0")

            # Crossing count
            crossings = 0
            prev_in_zone = False
            for c in window_candles:
                in_zone = c.low <= zone.high and c.high >= zone.low
                if in_zone and not prev_in_zone:
                    crossings += 1
                prev_in_zone = in_zone

            metrics.append(ReactionMetrics(
                window_s=window_s,
                return_pct=return_pct,
                mfe_pct=mfe_pct,
                mae_pct=mae_pct,
                first_touch_time_s=first_touch_time,
                time_inside_zone_pct=time_inside_pct,
                penetration_depth_pct=penetration,
                crossing_count=crossings,
            ))

        return metrics


class V3EventStore:
    """SQLite store for V3 events and summaries."""

    def __init__(self, db_path: str = "fib_matrix_v3.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_tables()
        self.connection.commit()

    def _create_tables(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS v3_events (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp_us INTEGER NOT NULL,
                zone_low TEXT NOT NULL,
                zone_high TEXT NOT NULL,
                zone_mid TEXT NOT NULL,
                zone_width TEXT NOT NULL,
                zone_members INTEGER NOT NULL,
                interval_diversity INTEGER NOT NULL,
                period_diversity INTEGER NOT NULL,
                ma_type_diversity INTEGER NOT NULL,
                reaction_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_v3_symbol ON v3_events(symbol);
            CREATE INDEX IF NOT EXISTS idx_v3_timestamp ON v3_events(timestamp_us);
            CREATE INDEX IF NOT EXISTS idx_v3_event_type ON v3_events(event_type);
        """)

    def record_event(self, event: V3Event) -> int:
        """Record a V3 event and return its ID."""
        reaction_data = [
            {
                "window_s": m.window_s,
                "return_pct": str(m.return_pct),
                "mfe_pct": str(m.mfe_pct),
                "mae_pct": str(m.mae_pct),
                "first_touch_time_s": m.first_touch_time_s,
                "time_inside_zone_pct": str(m.time_inside_zone_pct),
                "penetration_depth_pct": str(m.penetration_depth_pct),
                "crossing_count": m.crossing_count,
            }
            for m in event.reaction_metrics
        ]

        cursor = self.connection.execute("""
            INSERT INTO v3_events
            (symbol, event_type, timestamp_us, zone_low, zone_high, zone_mid,
             zone_width, zone_members, interval_diversity, period_diversity,
             ma_type_diversity, reaction_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.symbol,
            event.event_type.value,
            event.timestamp_us,
            str(event.zone.low),
            str(event.zone.high),
            str(event.zone.mid),
            str(event.zone.width),
            len(event.zone.members),
            event.zone.interval_diversity,
            event.zone.period_diversity,
            event.zone.ma_type_diversity,
            json.dumps(reaction_data),
            datetime.now(timezone.utc).isoformat(),
        ))
        self.connection.commit()
        return cursor.lastrowid

    def close(self) -> None:
        self.connection.close()


def log_event_json(event: V3Event) -> str:
    """Format a V3 event as a JSON line for logging."""
    return json.dumps({
        "type": "V3_EVENT",
        "symbol": event.symbol,
        "event_type": event.event_type.value,
        "timestamp_us": event.timestamp_us,
        "zone": {
            "low": str(event.zone.low),
            "high": str(event.zone.high),
            "mid": str(event.zone.mid),
            "width": str(event.zone.width),
            "members": len(event.zone.members),
        },
        "reaction_metrics": [
            {
                "window_s": m.window_s,
                "return_pct": str(m.return_pct),
                "mfe_pct": str(m.mfe_pct),
                "mae_pct": str(m.mae_pct),
            }
            for m in event.reaction_metrics
        ],
    })


def log_summary_json(symbol: str, events: List[V3Event], total_evaluations: int) -> str:
    """Format a V3 summary as a JSON line for logging."""
    event_counts = {}
    for event in events:
        event_counts[event.event_type.value] = event_counts.get(event.event_type.value, 0) + 1

    return json.dumps({
        "type": "V3_SUMMARY",
        "symbol": symbol,
        "total_evaluations": total_evaluations,
        "total_events": len(events),
        "event_counts": event_counts,
    })
