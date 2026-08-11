#!/usr/bin/env python3
"""Adaptive TP research scanner built on source-independent V2 primitives."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

import requests

from scanner_common import exclusive_log_path, load_percent_price_reference, parse_symbol_filters
from scanner_v2 import (
    AdaptiveConfig,
    AdaptiveStrategy,
    CandleBatch,
    CandleQuery,
    CandleSource,
    IndicatorEngine,
    IndicatorSpec,
    QuoteSource,
    ResearchStore,
    RestCandleSource,
    RestQuoteSource,
    ScanStore,
    SourceError,
    build_order,
    interval_to_us,
    resample_candles,
)


_NATIVE_INTERVALS = {
    "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h",
    "8h", "12h", "1d", "3d", "1w",
}
BASE_URL = "https://api.binance.com"
_CHILD_INTERVALS = {
    "1w": "1d", "3d": "1d", "1d": "12h", "12h": "6h", "8h": "4h",
    "6h": "2h", "4h": "2h", "2h": "1h", "1h": "30m", "30m": "15m",
    "15m": "5m", "5m": "1m", "3m": "1m", "1m": "1s",
}


@dataclass(frozen=True)
class Config:
    budget: Decimal = Decimal("10")
    min_wr: float = 0.0987
    max_wr: float = 0.1440
    tp_mult: Decimal = Decimal("8")
    sl_mult: Decimal = Decimal("1")
    trig_mult: Decimal = Decimal("0.15")
    min_vol: float = 300_000
    max_scan: int = 200
    interval: str = "4h"
    rsi_low: int = 20
    rsi_high: int = 36
    lo_lookback: int = 20
    lo_margin: Decimal = Decimal("1.025")
    min_atr_pct: Decimal = Decimal("0.004")
    fwd_bars: int = 72
    cool_down: int = 5
    min_signals: int = 8
    db_path: str = "scanner_v2.db"
    log_file: str = ""

    def adaptive(self) -> AdaptiveConfig:
        return AdaptiveConfig(
            min_wr=self.min_wr, max_wr=self.max_wr, tp_mult=self.tp_mult,
            sl_mult=self.sl_mult, trig_mult=self.trig_mult, rsi_low=self.rsi_low,
            rsi_high=self.rsi_high, lo_lookback=self.lo_lookback,
            lo_margin=self.lo_margin, min_atr_pct=self.min_atr_pct,
            fwd_bars=self.fwd_bars, cool_down=self.cool_down,
            min_signals=self.min_signals,
        )


@dataclass(frozen=True)
class PairScanSummary:
    """Rankable V2 output without retaining per-pair candles, frames, or traces."""

    pair: Dict[str, Any]
    signal_state: Optional[str] = None
    baseline: Any = None
    selected: Any = None
    target_source: Optional[str] = None
    resistance: Any = None
    warning: Optional[str] = None
    order: Any = None
    current_atr: Any = None
    provenance: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class Logger:
    """Small V2-only log sink; V1's console workflow is intentionally isolated."""

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "w", encoding="utf-8")

    def _emit(self, line: str) -> None:
        print(line)
        self._file.write(line + "\n")
        self._file.flush()

    def raw(self, line: str = "") -> None:
        self._emit(line)

    def info(self, message: str) -> None:
        self._emit(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] INFO {message}")

    def warn(self, message: str) -> None:
        self._emit(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] WARN {message}")

    def header(self, rows: List[str]) -> None:
        self._emit("=" * 70)
        for row in rows:
            self._emit("  " + row)
        self._emit("=" * 70)

    def close(self) -> None:
        self._file.close()


def default_log_path(logdir: str = "logs") -> str:
    directory = Path(logdir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(exclusive_log_path(directory, "v2", stamp))


def _fixed_interval(value: str) -> str:
    try:
        interval_to_us(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def parse_args(argv: Optional[List[str]] = None) -> tuple[Config, bool]:
    parser = argparse.ArgumentParser(
        prog="binance_scanner_v2.py",
        description="Binance Spot Scanner V2 — adaptive TP research output",
    )
    parser.add_argument("--budget", type=Decimal, default=Decimal("10"))
    parser.add_argument("--min-wr", type=float, default=9.87, metavar="PCT")
    parser.add_argument("--max-wr", type=float, default=14.40, metavar="PCT")
    parser.add_argument("--tp-mult", type=Decimal, default=Decimal("8"), metavar="X")
    parser.add_argument("--sl-mult", type=Decimal, default=Decimal("1"), metavar="X")
    parser.add_argument("--trig-mult", type=Decimal, default=Decimal("0.15"), metavar="X")
    parser.add_argument("--min-vol", type=float, default=300_000, metavar="QUOTE")
    parser.add_argument("--max-scan", type=int, default=200, metavar="N")
    parser.add_argument("--interval", type=_fixed_interval, default="4h", metavar="TF")
    parser.add_argument("--rsi-low", type=int, default=20)
    parser.add_argument("--rsi-high", type=int, default=36)
    parser.add_argument("--lo-lookback", type=int, default=20)
    parser.add_argument("--lo-margin", type=Decimal, default=Decimal("1.025"))
    parser.add_argument("--min-atr-pct", type=Decimal, default=Decimal("0.40"), metavar="PCT")
    parser.add_argument("--fwd-bars", type=int, default=72)
    parser.add_argument("--cool-down", type=int, default=5)
    parser.add_argument("--min-signals", type=int, default=8)
    parser.add_argument("--db", default="scanner_v2.db")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args(argv)
    values = (args.budget, args.tp_mult, args.sl_mult, args.trig_mult, args.lo_margin,
              args.min_atr_pct)
    if not all(value.is_finite() for value in values):
        parser.error("decimal parameters must be finite")
    if not all(math.isfinite(value) for value in (args.min_wr, args.max_wr, args.min_vol)):
        parser.error("number parameters must be finite")
    if args.budget <= 0 or args.tp_mult <= 0 or args.sl_mult <= 0:
        parser.error("budget, TP multiplier, and SL multiplier must be positive")
    if not (Decimal("0") < args.trig_mult < args.sl_mult):
        parser.error("--trig-mult must be positive and less than --sl-mult")
    if not (0 < args.min_wr <= args.max_wr < 100):
        parser.error("--min-wr / --max-wr must satisfy 0 < min <= max < 100")
    if args.min_vol < 0 or args.max_scan <= 0 or args.fwd_bars <= 0 or args.cool_down < 0:
        parser.error("scan limits are invalid")
    if not (0 <= args.rsi_low <= args.rsi_high <= 100) or args.lo_lookback <= 0:
        parser.error("RSI or low-lookback values are invalid")
    if args.lo_margin <= 0 or args.min_atr_pct < 0 or args.min_signals <= 0:
        parser.error("strategy parameters are invalid")
    config = Config(
        budget=args.budget, min_wr=args.min_wr / 100, max_wr=args.max_wr / 100,
        tp_mult=args.tp_mult, sl_mult=args.sl_mult, trig_mult=args.trig_mult,
        min_vol=args.min_vol, max_scan=args.max_scan, interval=args.interval,
        rsi_low=args.rsi_low, rsi_high=args.rsi_high, lo_lookback=args.lo_lookback,
        lo_margin=args.lo_margin, min_atr_pct=args.min_atr_pct / 100,
        fwd_bars=args.fwd_bars, cool_down=args.cool_down, min_signals=args.min_signals,
        db_path=args.db, log_file=args.log_file,
    )
    # Trigger dataclass validation before a network call.
    config.adaptive()
    return config, args.history


def _http_get(url: str, params: Optional[Dict] = None) -> Any:
    error = "unknown error"
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=12)
            if response.status_code == 429:
                delay = response.headers.get("Retry-After", "1")
                try:
                    time.sleep(max(1, min(int(delay), 30)))
                except (TypeError, ValueError):
                    time.sleep(1)
                error = "rate limited"
                continue
            if response.status_code == 418:
                time.sleep(30)
                error = "temporarily banned"
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            error = str(exc) or type(exc).__name__
            if attempt < 2:
                time.sleep(1)
    raise SourceError(f"public Binance request failed: {error}")


def _server_cutoff_us() -> int:
    payload = _http_get(f"{BASE_URL}/api/v3/time")
    if not isinstance(payload, dict) or not isinstance(payload.get("serverTime"), int):
        raise SourceError("Binance server time response is invalid")
    return payload["serverTime"] * 1_000


def _eligible_pairs(config: Config, log: Logger) -> List[Dict[str, Any]]:
    """Apply the V1 structural spot/filter rules without importing its workflow."""
    payload = _http_get(f"{BASE_URL}/api/v3/exchangeInfo")
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list):
        raise SourceError("exchange-info response is malformed")
    pairs: List[Dict[str, Any]] = []
    for symbol in symbols:
        if not isinstance(symbol, dict):
            continue
        if (symbol.get("status") != "TRADING" or symbol.get("quoteAsset") not in {"USDT", "USDC"}
                or not symbol.get("isSpotTradingAllowed")):
            continue
        filters = parse_symbol_filters(symbol)
        if filters is None or filters["min_val"] > config.budget:
            continue
        name = symbol.get("symbol")
        base = symbol.get("baseAsset")
        quote = symbol.get("quoteAsset")
        if not all(isinstance(value, str) and value for value in (name, base, quote)):
            continue
        pairs.append({"symbol": name, "base": base, "quote": quote, **filters})
    log.info(f"Exchange filter: {len(pairs)} eligible spot pairs")
    return pairs


def _ticker_pairs(pairs: List[Dict[str, Any]], config: Config, log: Logger) -> List[Dict[str, Any]]:
    """Apply V2's bounded 24-hour volume prefilter and order pairs by volume."""
    payload = _http_get(f"{BASE_URL}/api/v3/ticker/24hr")
    if not isinstance(payload, list):
        raise SourceError("24-hour ticker response is malformed")
    ticker_map = {row.get("symbol"): row for row in payload if isinstance(row, dict)}
    selected: List[Dict[str, Any]] = []
    minimum_volume = Decimal(str(config.min_vol))
    for pair in pairs:
        ticker = ticker_map.get(pair["symbol"])
        if not isinstance(ticker, dict):
            continue
        try:
            price = Decimal(str(ticker["lastPrice"]))
            volume = Decimal(str(ticker["quoteVolume"]))
            change = Decimal(str(ticker["priceChangePercent"]))
        except (KeyError, ArithmeticError, ValueError):
            continue
        if (not all(value.is_finite() for value in (price, volume, change))
                or price <= 0 or volume < minimum_volume
                or config.budget / price < Decimal("0.000001")):
            continue
        try:
            volume_float, change_float = float(volume), float(change)
        except (OverflowError, ValueError):
            continue
        if not math.isfinite(volume_float) or not math.isfinite(change_float):
            continue
        selected.append({**pair, "price": price, "volume": volume_float, "chg24": change_float})
    selected.sort(key=lambda pair: pair["volume"], reverse=True)
    selected = selected[:config.max_scan]
    log.info(f"Ticker filter: {len(selected)} pairs queued for V2 evaluation")
    return selected


def _source_interval(interval: str) -> str:
    if interval in _NATIVE_INTERVALS:
        return interval
    return "1m" if interval_to_us(interval) % interval_to_us("1m") == 0 else "1s"


def _resample_full(batch: CandleBatch, interval: str) -> CandleBatch:
    """Discard only query-boundary candles before strict resampler validation."""
    source_us = interval_to_us(batch.interval)
    target_us = interval_to_us(interval)
    if target_us == source_us:
        return batch
    if target_us < source_us or target_us % source_us:
        raise SourceError("requested interval cannot be resampled from the source interval")
    start = next((index for index, candle in enumerate(batch.candles)
                  if candle.open_time_us % target_us == 0), None)
    if start is None:
        raise SourceError("source window contains no full UTC resample bucket")
    usable = list(batch.candles[start:])
    remainder = len(usable) % (target_us // source_us)
    if remainder:
        usable = usable[:-remainder]
    if not usable:
        raise SourceError("source window contains no complete resample output")
    trimmed = CandleBatch(batch.symbol, batch.interval, usable, batch.source,
                          batch.timestamp_unit, batch.cutoff_us)
    return resample_candles(trimmed, interval)


def _child_interval(interval: str) -> Optional[str]:
    if interval == "1s":
        return None
    if interval in _CHILD_INTERVALS:
        return _CHILD_INTERVALS[interval]
    return ("1m" if interval_to_us(interval) % interval_to_us("1m") == 0
            else "1s")


def _dual_hit_resolver(source: CandleSource, symbol: str, cutoff_us: int,
                        interval: str):
    cache = {}

    def resolve_bar(parent, parent_interval: str, target: Decimal,
                    stop_trigger: Decimal) -> str:
        child = _child_interval(parent_interval)
        if child is None:
            return "loss"
        key = (child, parent.open_time_us, parent.close_time_us)
        try:
            if key not in cache:
                query = CandleQuery(
                    symbol, child, cutoff_us=min(cutoff_us, parent.close_time_us + 1),
                    start_us=parent.open_time_us, end_us=parent.close_time_us + 1,
                )
                cache[key] = source.fetch(query)
            batch = cache[key]
        except SourceError:
            raise
        except ValueError as exc:
            raise SourceError("lower-resolution candle data is invalid") from exc
        for candle in batch.candles:
            hit_target = candle.high >= target
            hit_stop = candle.low <= stop_trigger
            if hit_target and hit_stop:
                return resolve_bar(candle, child, target, stop_trigger)
            if hit_target:
                return "win"
            if hit_stop:
                return "loss"
        return "loss"

    def resolve(_opportunity, parent, target, stop_trigger) -> str:
        return resolve_bar(parent, interval, target, stop_trigger)

    return resolve


def _parameter_hash(config: Config) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _history(path: str) -> int:
    if not Path(path).is_file():
        print(f"No V2 history database at {Path(path).resolve()}")
        return 0
    rows = ScanStore.history_rows(path)
    if not rows:
        print("No V2 scan history yet.")
        return 0
    print("\nV2 scan history")
    for run_id, started, cutoff, version, completed in rows:
        state = "COMPLETE" if completed else "INCOMPLETE"
        print(f"#{run_id:<4} {state:<10} cutoff={cutoff} strategy={version} started={started}")
    return 0


def _payload_for_pair(result: PairScanSummary, rank: int, cutoff_us: int) -> Dict[str, Any]:
    pair = result.pair
    selected = result.selected
    score = selected or result.baseline
    current_atr = result.current_atr
    payload = {
        "version": "v2", "rank": rank, "symbol": pair["symbol"], "base": pair["base"],
        "quote": pair["quote"], "price": str(pair.get("best_ask", pair.get("price", ""))),
        "volume": pair.get("volume"), "chg24": pair.get("chg24"), "cutoff_us": cutoff_us,
        "in_sample": True, "signal_state": result.signal_state,
        "atr": None if current_atr is None or math.isnan(float(current_atr)) else float(current_atr),
        "opportunities": score.opportunities, "wins": score.wins, "losses": score.losses,
        "timeouts": score.timeouts, "baseline": result.baseline.as_dict(),
        "selected": selected.as_dict() if selected else None,
        "target_source": result.target_source,
        "resistance": asdict(result.resistance) if result.resistance else None,
        "provenance": result.provenance,
        "warning": result.warning, "order": result.order.as_dict() if result.order else None,
        "status": "CANDIDATE" if selected and result.order else "NO_FEASIBLE_TP",
        "error": result.error,
    }
    return payload


def scan_pair(pair: Dict[str, Any], *, config: Config, cutoff_us: int,
              candle_source: CandleSource, quote_source: QuoteSource):
    """Evaluate one pair without allowing source calls into the strategy itself."""
    quote = quote_source.get_best_quote(pair["symbol"])
    pair = dict(pair)
    pair["best_ask"] = quote.ask
    pair["price"] = quote.ask  # avgPrice mins=0 reference is the exact entry basis.
    if not load_percent_price_reference(pair, _http_get, BASE_URL):
        raise SourceError("required percent-price reference is unavailable")
    source_interval = _source_interval(config.interval)
    source_cutoff = cutoff_us
    if source_interval != config.interval:
        # Epoch-align custom intervals so any discarded endpoints are query boundaries only.
        source_cutoff = cutoff_us - cutoff_us % interval_to_us(config.interval)
    query = CandleQuery(pair["symbol"], source_interval, source_cutoff, limit=2_000)
    batch = candle_source.fetch(query)
    if len(batch.candles) != 2_000:
        raise SourceError("candle source did not provide the required 2,000 closed candles")
    if source_interval != config.interval:
        batch = _resample_full(batch, config.interval)
    indicators = IndicatorEngine().compute(batch, IndicatorSpec())
    adaptive_config = config.adaptive()
    strategy = AdaptiveStrategy(adaptive_config)
    current_atr = Decimal(str(indicators.frame.at[len(indicators.frame) - 1, "atr"]))
    if not current_atr.is_finite() or current_atr <= 0:
        raise SourceError("current ATR is invalid")
    def executable(target: Decimal) -> bool:
        return build_order(entry=quote.ask, atr=current_atr, target=target,
                           budget=config.budget, filters=pair,
                           config=adaptive_config) is not None
    evaluation, trace = strategy.evaluate(
        indicators, entry=quote.ask, tick=pair["tick"], spread=quote.spread,
        is_executable=executable,
        dual_hit_resolver=_dual_hit_resolver(candle_source, pair["symbol"], source_cutoff,
                                             config.interval),
    )
    order = None
    if evaluation.selected is not None:
        if evaluation.resistance is not None:
            target = evaluation.resistance.target
        else:
            target = quote.ask + current_atr * evaluation.selected.multiplier
        order = build_order(entry=quote.ask, atr=current_atr, target=target,
                            budget=config.budget, filters=pair, config=adaptive_config)
    return pair, indicators, evaluation, trace, order


def main(argv: Optional[List[str]] = None) -> int:
    config, want_history = parse_args(argv)
    if want_history:
        return _history(config.db_path)
    log_path = config.log_file or default_log_path(os.environ.get("SCANNER_LOGDIR", "logs"))
    log = Logger(log_path)
    store = ScanStore(config.db_path)
    started_at_us = time.time_ns() // 1_000
    try:
        cutoff_us = _server_cutoff_us()
        run_id = store.start_run(
            started_at_us=started_at_us, cutoff_us=cutoff_us,
            strategy_version=config.adaptive().version, indicator_version=IndicatorSpec().version,
            parameter_hash=_parameter_hash(config), provenance={"candle": "binance-rest", "quote": "bookTicker"},
        )
        log.header([
            "BINANCE SPOT SCANNER V2 — Adaptive TP Research",
            f"  IN_SAMPLE only · cutoff={cutoff_us} · database={Path(config.db_path).resolve()}",
            f"  interval={config.interval} · four pages × 500 candles · max scan={config.max_scan}",
        ])
        pairs = _ticker_pairs(_eligible_pairs(config, log), config, log)
        candle_source = RestCandleSource(_http_get, BASE_URL, timestamp_unit="ms")
        quote_source = RestQuoteSource(_http_get, BASE_URL)
        research_store = ResearchStore()
        evaluations: List[PairScanSummary] = []
        for pair in pairs:
            try:
                scanned_pair, indicators, evaluation, trace, order = scan_pair(
                    pair, config=config, cutoff_us=cutoff_us,
                    candle_source=candle_source, quote_source=quote_source,
                )
                evaluations.append(PairScanSummary(
                    pair=scanned_pair,
                    signal_state=evaluation.signal_state,
                    baseline=evaluation.baseline,
                    selected=evaluation.selected,
                    target_source=evaluation.target_source,
                    resistance=evaluation.resistance,
                    warning=evaluation.warning,
                    order=order,
                    current_atr=indicators.frame.at[len(indicators.frame) - 1, "atr"],
                    provenance=indicators.batch.provenance,
                ))
                research_store.record(trace)
                del indicators, evaluation, trace, order
            except (SourceError, ValueError, ArithmeticError) as exc:
                log.warn(f"{pair['symbol']} V2_REJECTED {exc}")
                evaluations.append(PairScanSummary(pair=pair, error=str(exc)))

        def sort_key(result):
            active = result.signal_state == "ACTIVE"
            rr = float(result.order.rr_to_trigger) if result.order is not None else float("-inf")
            opportunities = result.selected.opportunities if result.selected else 0
            return (0 if active else 1, -rr, -opportunities,
                    -float(result.pair.get("volume", 0)))

        evaluations.sort(key=sort_key)
        pair_records = []
        for rank, result in enumerate(evaluations, 1):
            pair = result.pair
            if result.error is not None:
                payload = {
                    "version": "v2", "rank": rank, "symbol": pair["symbol"], "base": pair["base"],
                    "quote": pair["quote"], "volume": pair.get("volume"), "chg24": pair.get("chg24"),
                    "cutoff_us": cutoff_us, "in_sample": True, "signal_state": "INACTIVE",
                    "opportunities": 0, "wins": 0, "losses": 0, "timeouts": 0,
                    "baseline": None, "selected": None, "resistance": None, "provenance": None,
                    "warning": None, "order": None, "target_source": "NO_FEASIBLE_TP",
                    "status": "NO_FEASIBLE_TP", "error": result.error,
                }
            else:
                payload = _payload_for_pair(result, rank, cutoff_us)
            pair_records.append((pair["symbol"], payload["signal_state"], payload))
            log.raw("V2_RESULT " + json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")))
        store.record_pairs(run_id, pair_records)
        store.finish_run(run_id, time.time_ns() // 1_000)
        log.info(f"V2 run #{run_id} completed; results in {Path(config.db_path).resolve()}")
        return 0
    finally:
        store.close()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
