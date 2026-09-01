"""Data-source interfaces, REST adapters, and deterministic resampling."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence

from .models import (
    BookQuote,
    Candle,
    CandleBatch,
    CandleIntegrityError,
    CandleQuery,
    SourceError,
    interval_to_us,
    timestamp_scale,
)


class CandleSource(Protocol):
    def fetch(self, query: CandleQuery) -> CandleBatch:
        """Fetch a validated batch for one explicit candle query."""


class QuoteSource(Protocol):
    def get_best_quote(self, symbol: str) -> BookQuote:
        """Fetch a public best bid/ask quote."""


class RestCandleSource:
    """Binance REST adapter with explicit timestamp conversion and pagination."""

    def __init__(self, http_get: Callable[[str, Dict], object], base_url: str,
                 *, timestamp_unit: str) -> None:
        if not callable(http_get) or not base_url:
            raise ValueError("http_get and base_url are required")
        self._http_get = http_get
        self._base_url = base_url.rstrip("/")
        self._timestamp_unit = timestamp_unit
        self._scale = timestamp_scale(timestamp_unit)

    def _to_us(self, value: object) -> int:
        try:
            raw = int(value)
        except (TypeError, ValueError) as exc:
            raise SourceError("REST candle timestamp is not an integer") from exc
        if raw < 0:
            raise SourceError("REST candle timestamp cannot be negative")
        return raw * self._scale

    def _to_source_unit(self, value_us: int) -> int:
        return value_us // self._scale

    def _decode(self, raw: object) -> Candle:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) < 9:
            raise SourceError("REST kline payload row is malformed")
        try:
            return Candle(
                open_time_us=self._to_us(raw[0]), close_time_us=self._to_us(raw[6]),
                open=Decimal(str(raw[1])), high=Decimal(str(raw[2])),
                low=Decimal(str(raw[3])), close=Decimal(str(raw[4])),
                volume=Decimal(str(raw[5])), trade_count=int(raw[8]),
            )
        except (ArithmeticError, ValueError) as exc:
            raise SourceError("REST kline values are malformed") from exc

    def _request(self, params: Dict) -> List[Candle]:
        payload = self._http_get(f"{self._base_url}/api/v3/klines", params)
        if not isinstance(payload, list) or not payload:
            raise SourceError("REST kline request returned no candles")
        return [self._decode(row) for row in payload]

    def fetch(self, query: CandleQuery) -> CandleBatch:
        if query.limit == 2_000 and query.start_us is None and query.end_us is None:
            return self.fetch_closed(query)
        params: Dict[str, object] = {
            "symbol": query.symbol, "interval": query.interval, "limit": query.limit,
        }
        if query.start_us is not None:
            params["startTime"] = self._to_source_unit(query.start_us)
        if query.end_us is not None:
            params["endTime"] = self._to_source_unit(query.end_us) - 1
        received = self._request(params)
        if ((query.start_us is not None and any(candle.open_time_us < query.start_us
                                                for candle in received)) or
                (query.end_us is not None and any(candle.open_time_us >= query.end_us
                                                  for candle in received))):
            raise SourceError("REST kline response escaped the requested half-open range")
        candles = [candle for candle in received if candle.close_time_us < query.cutoff_us]
        if not candles:
            raise SourceError("REST kline request only contained open candles")
        return CandleBatch(query.symbol, query.interval, candles, "binance-rest",
                           self._timestamp_unit, query.cutoff_us)

    def fetch_closed(self, query: CandleQuery, *, pages: int = 4,
                     page_size: int = 500) -> CandleBatch:
        """Fetch exactly four backward pages of closed candles beneath one cutoff."""
        if pages != 4 or page_size != 500:
            raise ValueError("V2 requires four 500-candle REST pages")
        # Anchor below the currently open bucket so four pages always mean 2,000
        # closed candles rather than 1,999 closed candles plus the live one.
        interval_us = interval_to_us(query.interval)
        closed_cutoff_us = query.cutoff_us - (query.cutoff_us % interval_us)
        end_time = self._to_source_unit(closed_cutoff_us) - 1
        collected: List[Candle] = []
        for _ in range(pages):
            page = self._request({
                "symbol": query.symbol, "interval": query.interval,
                "limit": page_size, "endTime": end_time,
            })
            if len(page) != page_size:
                raise SourceError("REST pagination did not return a full 500-candle page")
            closed = [candle for candle in page if candle.close_time_us < query.cutoff_us]
            if len(closed) != page_size:
                raise SourceError("REST pagination included an open or partial candle")
            collected.extend(closed)
            end_time = self._to_source_unit(min(candle.open_time_us for candle in page)) - 1
        collected.sort(key=lambda candle: candle.open_time_us)
        return CandleBatch(query.symbol, query.interval, collected, "binance-rest",
                           self._timestamp_unit, query.cutoff_us)


class RestQuoteSource:
    """Public book-ticker adapter; quote freshness is owned by the caller."""

    def __init__(self, http_get: Callable[[str, Dict], object], base_url: str) -> None:
        if not callable(http_get) or not base_url:
            raise ValueError("http_get and base_url are required")
        self._http_get = http_get
        self._base_url = base_url.rstrip("/")

    def get_best_quote(self, symbol: str) -> BookQuote:
        payload = self._http_get(
            f"{self._base_url}/api/v3/ticker/bookTicker", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise SourceError("book ticker response is malformed")
        if payload.get("symbol") != symbol:
            raise SourceError("book ticker response symbol does not match request")
        try:
            return BookQuote(symbol, Decimal(str(payload["bidPrice"])),
                             Decimal(str(payload["askPrice"])))
        except (KeyError, ArithmeticError, SourceError) as exc:
            raise SourceError("book ticker response is missing a valid quote") from exc


def resample_candles(batch: CandleBatch, interval: str) -> CandleBatch:
    """Resample fixed intervals on UTC epoch boundaries without partial buckets."""
    source_us = interval_to_us(batch.interval)
    target_us = interval_to_us(interval)
    if target_us < source_us or target_us % source_us:
        raise CandleIntegrityError("target interval must be a multiple of the source interval")
    if target_us == source_us:
        return batch
    size = target_us // source_us
    buckets: Dict[int, List[Candle]] = {}
    for candle in batch.candles:
        bucket_open = (candle.open_time_us // target_us) * target_us
        buckets.setdefault(bucket_open, []).append(candle)
    output: List[Candle] = []
    for bucket_open, group in sorted(buckets.items()):
        if len(group) != size:
            raise CandleIntegrityError("partial resample bucket")
        expected = [bucket_open + index * source_us for index in range(size)]
        if [candle.open_time_us for candle in group] != expected:
            raise CandleIntegrityError("resample bucket contains a gap or conflicting candle")
        output.append(Candle(
            open_time_us=bucket_open,
            close_time_us=group[-1].close_time_us,
            open=group[0].open,
            high=max(candle.high for candle in group),
            low=min(candle.low for candle in group),
            close=group[-1].close,
            volume=sum((candle.volume for candle in group), Decimal("0")),
            trade_count=sum(candle.trade_count for candle in group),
        ))
    return CandleBatch(batch.symbol, interval, output, f"{batch.source}:resampled",
                       batch.timestamp_unit, batch.cutoff_us)
