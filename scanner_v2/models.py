"""Canonical V2 value objects and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Iterable, Optional

import pandas as pd


_INTERVAL_RE = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[smhdw])$")
_INTERVAL_US = {"s": 1_000_000, "m": 60_000_000, "h": 3_600_000_000,
                "d": 86_400_000_000, "w": 604_800_000_000}
_TIMESTAMP_UNITS = {"s": 1_000_000, "ms": 1_000, "us": 1}


class CandleIntegrityError(ValueError):
    """Raised when a candle sequence cannot support deterministic research."""


class SourceError(RuntimeError):
    """Raised when a declared data adapter cannot provide valid market data."""


def interval_to_us(interval: str) -> int:
    """Return a fixed interval length; calendar intervals are intentionally excluded."""
    match = _INTERVAL_RE.fullmatch(interval)
    if match is None:
        raise ValueError("interval must be a positive fixed UTC interval such as 5m or 4h")
    return int(match["count"]) * _INTERVAL_US[match["unit"]]


def timestamp_scale(timestamp_unit: str) -> int:
    try:
        return _TIMESTAMP_UNITS[timestamp_unit]
    except KeyError as exc:
        raise ValueError("timestamp_unit must be one of: s, ms, us") from exc


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CandleIntegrityError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise CandleIntegrityError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class Candle:
    """A closed OHLCV candle represented with exact decimal market values."""

    open_time_us: int
    close_time_us: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int = 0

    def __post_init__(self) -> None:
        if self.open_time_us < 0 or self.close_time_us < self.open_time_us:
            raise CandleIntegrityError("invalid candle timestamps")
        values = {
            "open": self.open, "high": self.high, "low": self.low,
            "close": self.close, "volume": self.volume,
        }
        normalized = {name: _decimal(value, name) for name, value in values.items()}
        if any(value < 0 for value in normalized.values()):
            raise CandleIntegrityError("OHLCV values cannot be negative")
        if normalized["high"] < max(normalized["open"], normalized["close"]):
            raise CandleIntegrityError("high is below open or close")
        if normalized["low"] > min(normalized["open"], normalized["close"]):
            raise CandleIntegrityError("low is above open or close")
        if type(self.trade_count) is not int or self.trade_count < 0:
            raise CandleIntegrityError("trade_count must be a non-negative integer")
        for name, value in normalized.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class CandleQuery:
    symbol: str
    interval: str
    cutoff_us: int
    start_us: Optional[int] = None
    end_us: Optional[int] = None
    limit: int = 500

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.isupper():
            raise ValueError("symbol must be an uppercase Binance symbol")
        interval_to_us(self.interval)
        if self.cutoff_us <= 0:
            raise ValueError("cutoff_us must be positive")
        if self.start_us is not None and self.start_us < 0:
            raise ValueError("start_us cannot be negative")
        if self.end_us is not None and self.end_us <= 0:
            raise ValueError("end_us must be positive")
        if self.start_us is not None and self.end_us is not None and self.start_us >= self.end_us:
            raise ValueError("CandleQuery ranges are half-open and require start_us < end_us")
        if not 1 <= self.limit <= 2_000:
            raise ValueError("limit must be between 1 and 2000")


@dataclass(frozen=True)
class CandleBatch:
    """A validated, gap-free sequence with explicit source provenance."""

    symbol: str
    interval: str
    candles: Iterable[Candle]
    source: str
    timestamp_unit: str
    cutoff_us: Optional[int] = None
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        interval_us = interval_to_us(self.interval)
        source_tick_us = timestamp_scale(self.timestamp_unit)
        if not self.symbol or not self.source:
            raise CandleIntegrityError("symbol and source are required")
        candles = tuple(self.candles)
        if not candles:
            raise CandleIntegrityError("a candle batch cannot be empty")
        if any(not isinstance(candle, Candle) for candle in candles):
            raise CandleIntegrityError("candle batches require Candle values")
        ordered = tuple(sorted(candles, key=lambda candle: candle.open_time_us))
        if ordered != candles:
            raise CandleIntegrityError("candles must be ordered by open timestamp")
        previous: Optional[Candle] = None
        for candle in candles:
            if candle.open_time_us % interval_us:
                raise CandleIntegrityError("candle open is not UTC-epoch aligned")
            if candle.close_time_us - candle.open_time_us + source_tick_us != interval_us:
                raise CandleIntegrityError("candle is partial or conflicts with its declared interval")
            if previous is not None:
                if candle.open_time_us == previous.open_time_us:
                    raise CandleIntegrityError("duplicate candle open timestamp")
                if candle.open_time_us != previous.open_time_us + interval_us:
                    raise CandleIntegrityError("gap or conflicting candle interval")
            previous = candle
        if self.cutoff_us is not None:
            if self.cutoff_us <= 0:
                raise CandleIntegrityError("cutoff_us must be positive")
            if any(candle.close_time_us >= self.cutoff_us for candle in candles):
                raise CandleIntegrityError("open or partial candle included past the cutoff")
        digest = hashlib.sha256()
        for candle in candles:
            digest.update(
                (f"{candle.open_time_us}|{candle.close_time_us}|{candle.open}|"
                 f"{candle.high}|{candle.low}|{candle.close}|{candle.volume}|"
                 f"{candle.trade_count}\n").encode("ascii")
            )
        object.__setattr__(self, "candles", candles)
        object.__setattr__(self, "content_hash", digest.hexdigest())

    @property
    def provenance(self) -> dict:
        return {
            "source": self.source,
            "timestamp_unit": self.timestamp_unit,
            "content_hash": self.content_hash,
            "cutoff_us": self.cutoff_us,
            "interval": self.interval,
            "count": len(self.candles),
            "complete": True,
        }


@dataclass(frozen=True)
class BookQuote:
    symbol: str
    bid: Decimal
    ask: Decimal
    observed_at_us: Optional[int] = None

    def __post_init__(self) -> None:
        bid = _decimal(self.bid, "bid")
        ask = _decimal(self.ask, "ask")
        if bid <= 0 or ask <= 0 or bid > ask:
            raise SourceError("book quote must satisfy 0 < bid <= ask")
        if self.observed_at_us is not None and self.observed_at_us <= 0:
            raise SourceError("observed_at_us must be positive")
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass(frozen=True)
class IndicatorSpec:
    atr_period: int = 14
    rsi_period: int = 14
    version: str = "v1-sma-14"

    def __post_init__(self) -> None:
        if self.atr_period <= 0 or self.rsi_period <= 0 or not self.version:
            raise ValueError("indicator periods and version are required")


@dataclass(frozen=True)
class IndicatorFrame:
    batch: CandleBatch
    spec: IndicatorSpec
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        required = {"open_time_us", "open", "high", "low", "close", "volume", "trades", "atr", "rsi"}
        if not required.issubset(self.frame.columns):
            raise ValueError("indicator frame is missing required columns")
        if len(self.frame) != len(self.batch.candles):
            raise ValueError("indicator frame must have one row per candle")
