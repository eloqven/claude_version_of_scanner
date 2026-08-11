"""Deterministic coverage for the V2 source-independent scanner core."""

from __future__ import annotations

from decimal import Decimal
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import pandas as pd

import binance_scanner_v2 as v2

from scanner_v2 import (
    AdaptiveConfig,
    AdaptiveStrategy,
    BookQuote,
    Candle,
    CandleBatch,
    CandleIntegrityError,
    CandleQuery,
    IndicatorEngine,
    IndicatorFrame,
    IndicatorSpec,
    RestCandleSource,
    RestQuoteSource,
    ScanStore,
    SourceError,
    TargetScore,
    Opportunity,
    build_order,
    fallback_multipliers,
    resistance_candidates,
    score_multiplier,
    select_hardest_passing,
    resample_candles,
)
from scanner_v2.strategy import freeze_opportunities


def candle(index: int, *, interval_us: int = 60_000_000, open_: str = "10",
           high: str = "11", low: str = "9", close: str = "10") -> Candle:
    open_us = index * interval_us
    return Candle(
        open_time_us=open_us,
        close_time_us=open_us + interval_us - 1,
        open=Decimal(open_), high=Decimal(high), low=Decimal(low),
        close=Decimal(close), volume=Decimal("2"), trade_count=3,
    )


class TestV2CandleData(unittest.TestCase):
    def test_batch_rejects_duplicate_and_gap(self):
        duplicate = [candle(0), candle(0)]
        with self.assertRaises(CandleIntegrityError):
            CandleBatch("TESTUSDT", "1m", duplicate, "rest", "us")
        with self.assertRaises(CandleIntegrityError):
            CandleBatch("TESTUSDT", "1m", [candle(0), candle(2)], "rest", "us")

    def test_batch_rejects_partial_and_off_epoch_candles(self):
        partial = Candle(
            open_time_us=0, close_time_us=59_999_998,
            open=Decimal("10"), high=Decimal("11"), low=Decimal("9"),
            close=Decimal("10"), volume=Decimal("2"), trade_count=3,
        )
        off_epoch = Candle(
            open_time_us=1, close_time_us=60_000_000,
            open=Decimal("10"), high=Decimal("11"), low=Decimal("9"),
            close=Decimal("10"), volume=Decimal("2"), trade_count=3,
        )
        with self.assertRaises(CandleIntegrityError):
            CandleBatch("TESTUSDT", "1m", [partial], "fixture", "us")
        with self.assertRaises(CandleIntegrityError):
            CandleBatch("TESTUSDT", "1m", [off_epoch], "fixture", "us")

    def test_resampler_uses_utc_epoch_and_exact_ohlcv_aggregation(self):
        batch = CandleBatch(
            "TESTUSDT", "1m",
            [
                candle(0, high="11", low="9", close="10"),
                candle(1, high="12", low="8", close="11"),
                candle(2, high="13", low="7", close="12"),
                candle(3, high="14", low="6", close="13"),
            ], "rest", "us",
        )
        out = resample_candles(batch, "2m")
        self.assertEqual(len(out.candles), 2)
        first = out.candles[0]
        self.assertEqual(first.open_time_us, 0)
        self.assertEqual(first.open, Decimal("10"))
        self.assertEqual(first.high, Decimal("12"))
        self.assertEqual(first.low, Decimal("8"))
        self.assertEqual(first.close, Decimal("11"))
        self.assertEqual(first.volume, Decimal("4"))
        self.assertEqual(first.trade_count, 6)

    def test_resampler_rejects_partial_bucket(self):
        batch = CandleBatch("TESTUSDT", "1m", [candle(0)], "rest", "us")
        with self.assertRaises(CandleIntegrityError):
            resample_candles(batch, "2m")

    def test_rest_source_requires_declared_unit_and_uses_four_pages(self):
        calls = []

        def http_get(url, params):
            calls.append(params)
            page = len(calls)
            start = (4 - page) * 500
            return [
                [
                    (start + offset) * 60_000, "10", "11", "9", "10", "2",
                    (start + offset + 1) * 60_000 - 1, "0", 3, "0", "0", "0",
                ]
                for offset in range(500)
            ]

        source = RestCandleSource(http_get, "https://example.invalid", timestamp_unit="ms")
        batch = source.fetch(CandleQuery("TESTUSDT", "1m", cutoff_us=120_000_030_000,
                                         limit=2_000))
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(batch.candles), 2_000)
        self.assertEqual(batch.timestamp_unit, "ms")
        self.assertTrue(batch.provenance["complete"])
        self.assertEqual(calls[0]["limit"], 500)
        self.assertEqual(calls[0]["endTime"], 119_999_999)

    def test_rest_source_paginates_microsecond_timestamps(self):
        calls = []

        def http_get(_url, params):
            calls.append(params)
            page = len(calls)
            start = (4 - page) * 500
            return [
                [
                    (start + offset) * 60_000_000, "10", "11", "9", "10", "2",
                    (start + offset + 1) * 60_000_000 - 1, "0", 3, "0", "0", "0",
                ]
                for offset in range(500)
            ]

        source = RestCandleSource(http_get, "https://example.invalid", timestamp_unit="us")
        batch = source.fetch(CandleQuery("TESTUSDT", "1m", cutoff_us=120_000_030_000,
                                         limit=2_000))
        self.assertEqual(len(batch.candles), 2_000)
        self.assertEqual(batch.timestamp_unit, "us")
        self.assertEqual(calls[0]["endTime"], 119_999_999_999)

    def test_rest_source_rejects_a_response_outside_half_open_range(self):
        def http_get(_url, _params):
            return [[0, "10", "11", "9", "10", "2", 59_999, "0", 3, "0", "0", "0"]]

        source = RestCandleSource(http_get, "https://example.invalid", timestamp_unit="ms")
        with self.assertRaises(SourceError):
            source.fetch(CandleQuery("TESTUSDT", "1m", cutoff_us=120_000_000,
                                     start_us=60_000_000, end_us=120_000_000))

    def test_quote_source_rejects_symbol_mismatch(self):
        source = RestQuoteSource(
            lambda _url, _params: {"symbol": "OTHERUSDT", "bidPrice": "9", "askPrice": "10"},
            "https://example.invalid",
        )
        with self.assertRaises(SourceError):
            source.get_best_quote("TESTUSDT")


class TestV2Indicators(unittest.TestCase):
    def test_indicator_engine_matches_v1_sma_formula(self):
        candles = [
            candle(i, open_=str(100 + (i % 3)), high=str(101 + i),
                   low=str(99), close=str(100 + (i % 3)))
            for i in range(24)
        ]
        batch = CandleBatch("TESTUSDT", "1m", candles, "fixture", "us")
        got = IndicatorEngine().compute(batch, IndicatorSpec(atr_period=14, rsi_period=14))
        frame = pd.DataFrame({
            "high": [float(c.high) for c in candles],
            "low": [float(c.low) for c in candles],
            "close": [float(c.close) for c in candles],
        })
        previous = frame["close"].shift(1)
        expected_atr = pd.concat([
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ], axis=1).max(axis=1).rolling(14).mean()
        delta = frame["close"].diff()
        expected_rsi = 100 - 100 / (1 + delta.clip(lower=0).rolling(14).mean() /
                                    (-delta.clip(upper=0)).rolling(14).mean().replace(0, float("nan")))
        pd.testing.assert_series_equal(got.frame["atr"], expected_atr, check_names=False)
        pd.testing.assert_series_equal(got.frame["rsi"], expected_rsi, check_names=False)


class TestV2Strategy(unittest.TestCase):
    def _outcome_candles(self, outcomes):
        rows = []
        for index, outcome in enumerate(outcomes):
            high, low = {"win": ("109", "99.5"), "loss": ("105", "99"),
                         "timeout": ("105", "99.5"), "dual": ("109", "99")}[outcome]
            rows.extend([
                candle(index * 2, open_="100", high="101", low="99.5", close="100"),
                candle(index * 2 + 1, open_="100", high=high, low=low, close="100"),
            ])
        return rows

    def _opportunities(self, count):
        return [Opportunity(i * 2, i * 2 + 1, i * 2 + 1,
                            Decimal("100"), Decimal("1"))
                for i in range(count)]

    def test_dodo_denominator_counts_timeouts(self):
        outcomes = ["win", "win"] + ["loss"] * 11 + ["timeout", "timeout"]
        score = score_multiplier(
            self._outcome_candles(outcomes), self._opportunities(len(outcomes)),
            AdaptiveConfig(fwd_bars=1), Decimal("8"), tick=Decimal("0"),
        )
        self.assertEqual((score.wins, score.losses, score.timeouts), (2, 11, 2))
        self.assertAlmostEqual(score.hit_rate * 100, 13.3333333333)

    def test_stop_trigger_and_unresolved_dual_hit_are_losses(self):
        trigger_loss = score_multiplier(
            self._outcome_candles(["loss"]), self._opportunities(1),
            AdaptiveConfig(fwd_bars=1), Decimal("8"), tick=Decimal("0"),
        )
        dual_loss = score_multiplier(
            self._outcome_candles(["dual"]), self._opportunities(1),
            AdaptiveConfig(fwd_bars=1), Decimal("8"), tick=Decimal("0"),
        )
        dual_win = score_multiplier(
            self._outcome_candles(["dual"]), self._opportunities(1),
            AdaptiveConfig(fwd_bars=1), Decimal("8"), tick=Decimal("0"),
            dual_hit_resolver=lambda *_: "win",
        )
        self.assertEqual(trigger_loss.losses, 1)
        self.assertEqual(dual_loss.losses, 1)
        self.assertEqual(dual_win.wins, 1)

    def test_hardest_passing_target_and_discrete_band_warning(self):
        scores = [
            TargetScore(Decimal("8"), "RESISTANCE", 2, 11, 2),
            TargetScore(Decimal("9"), "RESISTANCE", 3, 10, 2),
            TargetScore(Decimal("10"), "RESISTANCE", 1, 12, 2),
        ]
        selected, warning = select_hardest_passing(scores, 0.13, 0.20)
        self.assertEqual(selected.multiplier, Decimal("9"))
        self.assertFalse(warning)
        selected, warning = select_hardest_passing(scores, 0.134, 0.135)
        self.assertIsNone(selected)
        self.assertTrue(warning)

    def test_selector_can_exclude_a_nonexecutable_farthest_target(self):
        scores = [
            TargetScore(Decimal("8"), "RESISTANCE", 2, 11, 2),
            TargetScore(Decimal("9"), "RESISTANCE", 3, 10, 2),
        ]
        selected, _ = select_hardest_passing(
            scores, 0.13, 0.20, executable=lambda score: score.multiplier < Decimal("9"),
        )
        self.assertEqual(selected.multiplier, Decimal("8"))

    def test_resistance_cluster_uses_median_and_adaptive_buffer(self):
        candles = [candle(index, high=high) for index, high in enumerate(
            ("10", "10", "12", "10", "10", "10", "12.1", "10", "10"))]
        evidence = resistance_candidates(
            candles, entry=Decimal("1"), atr=Decimal("1"), tick=Decimal("0.01"),
            spread=Decimal("0.04"), config=AdaptiveConfig(resistance_lookback=9),
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].touches, 2)
        self.assertEqual(evidence[0].buffer, Decimal("0.05"))
        self.assertEqual(evidence[0].target, Decimal("12.00"))

    def test_fallback_candidates_use_favorable_excursion_breakpoints(self):
        opportunities = self._opportunities(2)
        multipliers = fallback_multipliers(
            self._outcome_candles(["win", "timeout"]), opportunities,
            AdaptiveConfig(fwd_bars=1),
        )
        self.assertEqual(multipliers, (Decimal("9"),))

    def test_min_signals_blocks_target_selection(self):
        candles = [candle(index, open_="100", high="101", low="99", close="100")
                   for index in range(100)]
        batch = CandleBatch("TESTUSDT", "1m", candles, "fixture", "us")
        frame = pd.DataFrame({
            "open_time_us": [item.open_time_us for item in candles],
            "open": [float(item.open) for item in candles],
            "high": [float(item.high) for item in candles],
            "low": [float(item.low) for item in candles],
            "close": [float(item.close) for item in candles],
            "volume": [float(item.volume) for item in candles],
            "trades": [item.trade_count for item in candles],
            "atr": [1.0] * len(candles), "rsi": [30.0] * len(candles),
        })
        indicators = IndicatorFrame(batch, IndicatorSpec(), frame)
        opportunity = Opportunity(95, 96, 97, Decimal("100"), Decimal("1"))
        with mock.patch("scanner_v2.strategy.freeze_opportunities", return_value=(opportunity,)):
            evaluation, trace = AdaptiveStrategy(AdaptiveConfig(min_signals=2)).evaluate(
                indicators, entry=Decimal("100"), tick=Decimal("0"),
            )
        self.assertIsNone(evaluation.selected)
        self.assertEqual(evaluation.warning, "INSUFFICIENT_SIGNALS")
        self.assertEqual(trace.scores[0].opportunities, 1)

    def test_short_history_is_explicitly_ineligible_for_forward_scoring(self):
        candles = [candle(index, open_="100", high="101", low="99", close="100")
                   for index in range(93)]
        batch = CandleBatch("TESTUSDT", "1m", candles, "fixture", "us")
        frame = pd.DataFrame({
            "open_time_us": [item.open_time_us for item in candles],
            "open": [float(item.open) for item in candles],
            "high": [float(item.high) for item in candles],
            "low": [float(item.low) for item in candles],
            "close": [float(item.close) for item in candles],
            "volume": [float(item.volume) for item in candles],
            "trades": [item.trade_count for item in candles],
            "atr": [1.0] * len(candles), "rsi": [30.0] * len(candles),
        })
        evaluation, _ = AdaptiveStrategy(AdaptiveConfig()).evaluate(
            IndicatorFrame(batch, IndicatorSpec(), frame), entry=Decimal("100"), tick=Decimal("0"),
        )
        self.assertIsNone(evaluation.selected)
        self.assertEqual(evaluation.warning, "INSUFFICIENT_HISTORY")

    def test_dual_hit_resolver_recurses_and_caches_child_batches(self):
        parent = Candle(0, 14_399_999_999, Decimal("100"), Decimal("110"),
                        Decimal("9"), Decimal("100"), Decimal("2"), 1)
        dual_child = Candle(0, 7_199_999_999, Decimal("100"), Decimal("110"),
                            Decimal("9"), Decimal("100"), Decimal("2"), 1)
        target_child = Candle(0, 3_599_999_999, Decimal("100"), Decimal("110"),
                              Decimal("9.5"), Decimal("100"), Decimal("2"), 1)
        source = mock.Mock()
        source.fetch.side_effect = lambda query: {
            "2h": CandleBatch("TESTUSDT", "2h", [dual_child], "fixture", "us"),
            "1h": CandleBatch("TESTUSDT", "1h", [target_child], "fixture", "us"),
        }[query.interval]
        resolve = v2._dual_hit_resolver(source, "TESTUSDT", 14_400_000_000, "4h")
        self.assertEqual(resolve(None, parent, Decimal("108"), Decimal("9.15")), "win")
        self.assertEqual(resolve(None, parent, Decimal("108"), Decimal("9.15")), "win")
        self.assertEqual(source.fetch.call_count, 2)
        self.assertIsNone(v2._child_interval("1s"))

    def test_dual_hit_data_failure_rejects_the_pair(self):
        parent = Candle(0, 14_399_999_999, Decimal("100"), Decimal("110"),
                        Decimal("9"), Decimal("100"), Decimal("2"), 1)
        source = mock.Mock()
        source.fetch.side_effect = SourceError("child data unavailable")
        resolve = v2._dual_hit_resolver(source, "TESTUSDT", 14_400_000_000, "4h")
        with self.assertRaises(SourceError):
            resolve(None, parent, Decimal("108"), Decimal("9.15"))

    def test_freeze_opportunities_uses_next_open_and_full_window_lockout(self):
        candles = [candle(index, open_="100", high="101", low="99", close="100")
                   for index in range(31)]
        batch = CandleBatch("TESTUSDT", "1m", candles, "fixture", "us")
        frame = pd.DataFrame({
            "open_time_us": [item.open_time_us for item in candles],
            "open": [float(item.open) for item in candles],
            "high": [float(item.high) for item in candles],
            "low": [float(item.low) for item in candles],
            "close": [float(item.close) for item in candles],
            "volume": [float(item.volume) for item in candles],
            "trades": [item.trade_count for item in candles],
            "atr": [1.0] * len(candles), "rsi": [30.0] * len(candles),
        })
        config = AdaptiveConfig(fwd_bars=4, cool_down=1, min_signals=1)
        opportunities = freeze_opportunities(IndicatorFrame(batch, IndicatorSpec(), frame), config)
        self.assertEqual([item.signal_index for item in opportunities], [21, 25])
        self.assertEqual([item.entry_index for item in opportunities], [22, 26])
        self.assertEqual(opportunities[0].last_index, 25)
        self.assertGreater(opportunities[1].entry_index, opportunities[0].last_index)


class TestV2Orders(unittest.TestCase):
    def _filters(self):
        return {
            "tick": Decimal("0.01"), "step": Decimal("0.1"),
            "min_qty": Decimal("0.1"), "max_qty": Decimal("100"),
            "min_price": Decimal("0.01"), "max_price": Decimal("10000"),
            "min_val": Decimal("1"), "max_val": Decimal("1000"),
            "percent_min_mult": None, "percent_max_mult": None,
            "percent_buy_min_mult": None, "percent_buy_max_mult": None,
            "percent_ref": None,
            # V2 must ignore this market-only constraint for a limit/OCO layout.
            "market_min_qty": Decimal("10"),
        }

    def test_order_uses_lot_size_and_quantizes_before_validation(self):
        order = build_order(
            entry=Decimal("10.00"), atr=Decimal("1"), target=Decimal("18.999"),
            budget=Decimal("25"), filters=self._filters(),
            config=AdaptiveConfig(),
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.quantity, Decimal("2.5"))
        self.assertEqual(order.take_profit, Decimal("18.99"))
        self.assertGreater(order.stop_trigger, order.stop_limit)

    def test_order_fails_closed_for_quote_alignment_or_missing_percent_reference(self):
        self.assertIsNone(build_order(
            entry=Decimal("10.001"), atr=Decimal("1"), target=Decimal("18"),
            budget=Decimal("25"), filters=self._filters(), config=AdaptiveConfig(),
        ))
        filters = self._filters()
        filters.update({
            "percent_min_mult": Decimal("0.8"), "percent_max_mult": Decimal("1.2"),
            "percent_buy_min_mult": Decimal("0.8"), "percent_buy_max_mult": Decimal("1.2"),
        })
        self.assertIsNone(build_order(
            entry=Decimal("10"), atr=Decimal("1"), target=Decimal("18"),
            budget=Decimal("25"), filters=filters, config=AdaptiveConfig(),
        ))
        malformed = self._filters()
        malformed["min_price"] = "not-a-decimal"
        self.assertIsNone(build_order(
            entry=Decimal("10"), atr=Decimal("1"), target=Decimal("18"),
            budget=Decimal("25"), filters=malformed, config=AdaptiveConfig(),
        ))

    def test_order_checks_each_percent_price_level_with_a_valid_reference(self):
        filters = self._filters()
        filters.update({
            "percent_ref": Decimal("10"),
            "percent_buy_min_mult": Decimal("0.99"),
            "percent_buy_max_mult": Decimal("1.01"),
            "percent_min_mult": Decimal("0.80"),
            "percent_max_mult": Decimal("2.00"),
        })
        import scanner_v2.orders as order_module

        with mock.patch.object(order_module, "_in_percent_band",
                               wraps=order_module._in_percent_band) as check:
            order = build_order(
                entry=Decimal("10"), atr=Decimal("1"), target=Decimal("18"),
                budget=Decimal("25"), filters=filters, config=AdaptiveConfig(),
            )
        self.assertIsNotNone(order)
        self.assertEqual(
            [call.args[0] for call in check.call_args_list],
            [order.entry, order.take_profit, order.stop_trigger, order.stop_limit],
        )

        buy_out_of_band = dict(filters, percent_buy_max_mult=Decimal("0.99"))
        self.assertIsNone(build_order(
            entry=Decimal("10"), atr=Decimal("1"), target=Decimal("18"),
            budget=Decimal("25"), filters=buy_out_of_band, config=AdaptiveConfig(),
        ))
        target_out_of_band = dict(filters, percent_max_mult=Decimal("1.70"))
        self.assertIsNone(build_order(
            entry=Decimal("10"), atr=Decimal("1"), target=Decimal("18"),
            budget=Decimal("25"), filters=target_out_of_band, config=AdaptiveConfig(),
        ))
        stop_limit_out_of_band = dict(filters, percent_min_mult=Decimal("0.905"))
        self.assertIsNone(build_order(
            entry=Decimal("10"), atr=Decimal("1"), target=Decimal("18"),
            budget=Decimal("25"), filters=stop_limit_out_of_band, config=AdaptiveConfig(),
        ))


class TestV2RunnerCli(unittest.TestCase):
    def test_v2_defaults_and_custom_fixed_interval(self):
        config, history = v2.parse_args(["--max-scan", "3", "--interval", "13m"])
        self.assertFalse(history)
        self.assertEqual(config.db_path, "scanner_v2.db")
        self.assertEqual(config.max_scan, 3)
        self.assertEqual(config.interval, "13m")

    def test_v2_cli_rejects_invalid_rate_band(self):
        with self.assertRaises(SystemExit):
            v2.parse_args(["--min-wr", "20", "--max-wr", "10"])


class TestV2RunnerBoundaries(unittest.TestCase):
    @staticmethod
    def _batch():
        return CandleBatch(
            "TESTUSDT", "1m",
            [candle(index, open_="10", high="11", low="9", close="10")
             for index in range(2_000)],
            "fixture", "us",
        )

    @staticmethod
    def _pair():
        return {
            "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "tick": Decimal("0.000000000000000001"), "step": Decimal("0.1"),
            "min_qty": Decimal("0.1"), "max_qty": Decimal("1000"),
            "min_price": Decimal("0.000000000000000001"), "max_price": Decimal("10000"),
            "min_val": Decimal("1"), "max_val": Decimal("10000"),
            "percent_min_mult": None, "percent_max_mult": None,
            "percent_buy_min_mult": None, "percent_buy_max_mult": None,
            "percent_ref": None, "volume": 1000, "chg24": 0,
        }

    def test_scan_pair_uses_only_the_candle_source_contract_and_keeps_decimal_quote(self):
        batch = self._batch()

        class Source:
            def __init__(self):
                self.query = None

            def fetch(self, query):
                self.query = query
                return batch

        class Quote:
            def get_best_quote(self, symbol):
                return BookQuote(symbol, Decimal("0.123456789123456788"),
                                 Decimal("0.123456789123456789"))

        source = Source()
        seen_prices = []

        def reference(pair, *_args):
            seen_prices.append(pair["price"])
            return True

        with mock.patch.object(v2, "load_percent_price_reference", side_effect=reference):
            _pair, indicators, _evaluation, _trace, _order = v2.scan_pair(
                self._pair(), config=v2.Config(interval="1m"), cutoff_us=120_000_000_000,
                candle_source=source, quote_source=Quote(),
            )
        self.assertEqual(source.query.limit, 2_000)
        self.assertEqual(seen_prices, [Decimal("0.123456789123456789")])
        self.assertEqual(indicators.batch.provenance["count"], 2_000)

    def test_scan_store_round_trips_versions_and_batch_provenance(self):
        batch = self._batch()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "scanner_v2.db")
            store = ScanStore(path)
            try:
                run_id = store.start_run(
                    started_at_us=1, cutoff_us=2, strategy_version="strategy-v2",
                    indicator_version="indicator-v1", parameter_hash="abc",
                    provenance={"candle": "binance-rest", "quote": "bookTicker"},
                )
                store.record_pair(run_id, "TESTUSDT", "INACTIVE", {
                    "version": "v2", "provenance": batch.provenance,
                })
                store.finish_run(run_id, 3)
                payload = json.loads(store.connection.execute(
                    "SELECT payload_json FROM pair_scans WHERE run_id=?", (run_id,)).fetchone()[0])
            finally:
                store.close()
            self.assertEqual(payload["provenance"]["content_hash"], batch.content_hash)
            self.assertEqual(ScanStore.history_rows(path), [(1, 1, 2, "strategy-v2", 3)])

    def test_scan_store_enforces_foreign_keys_and_batches_pair_records(self):
        with tempfile.TemporaryDirectory() as td:
            store = ScanStore(os.path.join(td, "scanner_v2.db"))
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    store.record_pair(999, "TESTUSDT", "INACTIVE", {})
                run_id = store.start_run(
                    started_at_us=1, cutoff_us=2, strategy_version="strategy-v2",
                    indicator_version="indicator-v1", parameter_hash="abc", provenance={},
                )
                statements = []
                store.connection.set_trace_callback(statements.append)
                store.record_pairs(run_id, (
                    ("AAAUSDT", "ACTIVE", {"version": "v2"}),
                    ("BBBUSDT", "INACTIVE", {"version": "v2"}),
                ))
                self.assertEqual(store.connection.execute(
                    "SELECT COUNT(*) FROM pair_scans WHERE run_id=?", (run_id,)).fetchone()[0], 2)
            finally:
                store.close()
        self.assertEqual(sum(line.strip().upper() == "COMMIT" for line in statements), 1)

    def test_v2_market_filters_do_not_depend_on_v1_workflow(self):
        filters = [
            {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "minQty": "0.1", "maxQty": "1000", "stepSize": "0.1"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "1"},
        ]
        exchange = {"symbols": [
            {"symbol": "AAAUSDT", "baseAsset": "AAA", "quoteAsset": "USDT",
             "status": "TRADING", "isSpotTradingAllowed": True, "filters": filters},
            {"symbol": "BADBTC", "baseAsset": "BAD", "quoteAsset": "BTC",
             "status": "TRADING", "isSpotTradingAllowed": True, "filters": filters},
        ]}
        config = v2.Config(min_vol=100, max_scan=3)
        log = mock.Mock()
        with mock.patch.object(v2, "_http_get", return_value=exchange):
            pairs = v2._eligible_pairs(config, log)
        self.assertEqual([pair["symbol"] for pair in pairs], ["AAAUSDT"])
        tickers = [{"symbol": "AAAUSDT", "lastPrice": "10", "quoteVolume": "1000",
                    "priceChangePercent": "1"}]
        with mock.patch.object(v2, "_http_get", return_value=tickers):
            selected = v2._ticker_pairs(pairs, config, log)
        self.assertEqual([pair["symbol"] for pair in selected], ["AAAUSDT"])

    def test_custom_fixed_intervals_resample_without_partial_buckets(self):
        batch = self._batch()
        expected_counts = {"5m": 400, "8m": 250, "13m": 153, "21m": 95,
                           "34m": 58, "55m": 36}
        for interval, count in expected_counts.items():
            with self.subTest(interval=interval):
                self.assertEqual(len(v2._resample_full(batch, interval).candles), count)
        short = v2._resample_full(batch, "55m")
        evaluation, _ = AdaptiveStrategy(AdaptiveConfig()).evaluate(
            IndicatorEngine().compute(short, IndicatorSpec()), entry=Decimal("10"), tick=Decimal("0"),
        )
        self.assertEqual(evaluation.warning, "INSUFFICIENT_HISTORY")

    def test_custom_second_intervals_resample_from_one_second_candles(self):
        second_batch = CandleBatch(
            "TESTUSDT", "1s",
            [candle(index, interval_us=1_000_000, open_="10", high="11", low="9", close="10")
             for index in range(2_000)],
            "fixture", "us",
        )
        for interval, expected in (("2s", 1_000), ("90s", 22)):
            with self.subTest(interval=interval):
                self.assertEqual(v2._source_interval(interval), "1s")
                self.assertEqual(len(v2._resample_full(second_batch, interval).candles), expected)

    def test_main_persists_success_and_rejected_pair_records(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "scanner_v2.db")
            log_path = os.path.join(td, "run.log")
            config = v2.Config(db_path=db_path, log_file=log_path, max_scan=2, interval="1m")
            accepted = self._pair()
            rejected = {**self._pair(), "symbol": "FAILUSDT", "base": "FAIL"}

            class Source:
                def fetch(self, query):
                    if query.symbol == "FAILUSDT":
                        raise SourceError("fixture rejection")
                    return TestV2RunnerBoundaries._batch()

            class Quote:
                def get_best_quote(self, symbol):
                    return BookQuote(symbol, Decimal("9.99"), Decimal("10.00"))

            with mock.patch.object(v2, "parse_args", return_value=(config, False)), \
                    mock.patch.object(v2, "_server_cutoff_us", return_value=120_000_000_000), \
                    mock.patch.object(v2, "_eligible_pairs", return_value=[accepted, rejected]), \
                    mock.patch.object(v2, "_ticker_pairs", return_value=[accepted, rejected]), \
                    mock.patch.object(v2, "RestCandleSource", return_value=Source()), \
                    mock.patch.object(v2, "RestQuoteSource", return_value=Quote()), \
                    mock.patch.object(v2, "load_percent_price_reference", return_value=True):
                self.assertEqual(v2.main([]), 0)

            with open(log_path, encoding="utf-8") as fh:
                records = [json.loads(line.split("V2_RESULT ", 1)[1]) for line in fh
                           if line.startswith("V2_RESULT ")]
            self.assertEqual({record["symbol"] for record in records}, {"TESTUSDT", "FAILUSDT"})
            success = next(record for record in records if record["symbol"] == "TESTUSDT")
            failed = next(record for record in records if record["symbol"] == "FAILUSDT")
            self.assertIsNotNone(success["provenance"]["content_hash"])
            self.assertEqual(failed["error"], "fixture rejection")
            connection = sqlite3.connect(db_path)
            try:
                self.assertIsNotNone(connection.execute(
                    "SELECT completed_at_us FROM scan_runs").fetchone()[0])
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM pair_scans").fetchone()[0], 2)
            finally:
                connection.close()
