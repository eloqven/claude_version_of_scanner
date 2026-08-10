"""Deterministic regression tests for the Binance scanners (no live network).

All Binance REST responses are mocked. Run with:
    python -m unittest discover -s tests -v
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import binance_scanner_proto as proto
import binance_scanner_v1 as v1
import log_dashboard as dash


def _symbol(symbol, base, quote, status="TRADING", spot=True,
            min_notional="1.0", step="0.01", tick="0.0001",
            min_price="0", max_price="1000000", max_qty="1000000000",
            max_notional=None, market_min_qty=None, market_max_qty=None,
            market_step=None, percent_sell_down=None, percent_sell_up=None,
            percent_avg_mins=5):
    filters = [
        {"filterType": "PRICE_FILTER", "minPrice": min_price,
         "maxPrice": max_price, "tickSize": tick},
        {"filterType": "LOT_SIZE", "minQty": "0", "maxQty": max_qty, "stepSize": step},
        {"filterType": "MIN_NOTIONAL", "minNotional": min_notional},
    ]
    if max_notional is not None:
        filters.append({"filterType": "NOTIONAL",
                        "minNotional": min_notional, "maxNotional": max_notional})
    if any(value is not None for value in
           (market_min_qty, market_max_qty, market_step)):
        filters.append({
            "filterType": "MARKET_LOT_SIZE",
            "minQty": market_min_qty or "0",
            "maxQty": market_max_qty or "0",
            "stepSize": market_step or "0",
        })
    if percent_sell_down is not None or percent_sell_up is not None:
        filters.append({
            "filterType": "PERCENT_PRICE_BY_SIDE",
            "bidMultiplierUp": "5",
            "bidMultiplierDown": "0.2",
            "askMultiplierUp": percent_sell_up,
            "askMultiplierDown": percent_sell_down,
            "avgPriceMins": percent_avg_mins,
        })
    return {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": quote,
        "status": status,
        "isSpotTradingAllowed": spot,
        "filters": filters,
    }


def _ticker(symbol, price="1.0", vol="1000000", chg="1.0"):
    return {
        "symbol": symbol,
        "lastPrice": price,
        "quoteVolume": vol,
        "priceChangePercent": chg,
    }


class _Quiet:
    """Context manager that swallows scanner console output."""

    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc):
        sys.stdout = self._old
        return False


class TestConsoleEncoding(unittest.TestCase):
    """R1 - both scripts must print safely on cp1252 Windows consoles."""

    def _simulate_cp1252(self, module):
        buf = io.BytesIO()
        stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
        old = sys.stdout
        sys.stdout = stream
        try:
            module._setup_console()
            print("\u2554\u2500 TEST \u2713")   # box-drawing + check mark
            stream.flush()
            return buf.getvalue()
        finally:
            sys.stdout = old

    def test_proto_reconfigures_console(self):
        data = self._simulate_cp1252(proto)
        self.assertIn(b"TEST", data)

    def test_v1_reconfigures_console(self):
        data = self._simulate_cp1252(v1)
        self.assertIn(b"TEST", data)


class TestTickerAbort(unittest.TestCase):
    """R2 - a failed ticker request must abort, not leak incomplete pairs."""

    def test_proto_exits_when_ticker_fails(self):
        with mock.patch.object(proto, "GET", return_value=None):
            with self.assertRaises(SystemExit):
                proto.enrich_ticker([_symbol("BTCUSDT", "BTC", "USDT")])

    def test_v1_exits_when_ticker_fails(self):
        log = v1.Logger("")
        with _Quiet():
            with mock.patch.object(v1, "_get", return_value=None):
                with self.assertRaises(SystemExit):
                    v1.step_ticker([_symbol("BTCUSDT", "BTC", "USDT")],
                                   v1.Config(), log)


class TestEmptyPairList(unittest.TestCase):
    """R7 - empty eligible-pair lists must complete without runtime errors."""

    def test_proto_enrich_empty_list(self):
        with mock.patch.object(proto, "GET", return_value=[_ticker("BTCUSDT")]):
            self.assertEqual(proto.enrich_ticker([]), [])

    def test_v1_step_ticker_empty_list(self):
        log = v1.Logger("")
        with _Quiet():
            with mock.patch.object(v1, "_get", return_value=[_ticker("BTCUSDT")]):
                out = v1.step_ticker([], v1.Config(), log)
        self.assertEqual(out, [])


class TestSymbolFiltering(unittest.TestCase):
    """R6 - JUP/SYRUP stay eligible; leveraged products excluded via metadata."""

    INFO = {"symbols": [
        _symbol("JUPUSDT", "JUP", "USDT"),
        _symbol("SYRUPUSDT", "SYRUP", "USDT"),
        _symbol("BTCUPUSDT", "BTCUP", "USDT", spot=False),
        _symbol("PAUSEDUSDT", "PAUSED", "USDT", status="BREAK"),
        _symbol("EURUSDT", "EUR", "EUR"),
    ]}

    def test_proto_keeps_jup_and_syrup(self):
        with mock.patch.object(proto, "GET", return_value=self.INFO):
            pairs = proto.get_pairs()
        syms = {p["symbol"] for p in pairs}
        self.assertIn("JUPUSDT", syms)
        self.assertIn("SYRUPUSDT", syms)
        self.assertNotIn("BTCUPUSDT", syms)
        self.assertNotIn("EURUSDT", syms)

    def test_v1_keeps_jup_and_syrup(self):
        log = v1.Logger("")
        with _Quiet():
            with mock.patch.object(v1, "_get", return_value=self.INFO):
                pairs = v1.step_exchange(v1.Config(), log)
        syms = {p["symbol"] for p in pairs}
        self.assertIn("JUPUSDT", syms)
        self.assertIn("SYRUPUSDT", syms)
        self.assertNotIn("BTCUPUSDT", syms)
        self.assertNotIn("EURUSDT", syms)


class TestParentDirs(unittest.TestCase):
    """R9 - V1 must create configured database and log parent directories."""
    def test_db_parent_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "nested", "data", "scanner.db")
            log = v1.Logger("")
            with _Quiet():
                conn = v1.db_open(db_path, log)
                conn.close()
            self.assertTrue(os.path.isdir(os.path.dirname(db_path)))

    def test_log_parent_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = os.path.join(td, "logs", "deep", "run.log")
            log = v1.Logger(log_path)
            log.close()
            self.assertTrue(os.path.isdir(os.path.dirname(log_path)))


def _kline(ts, open_, high, low, close):
    """Binance 12-column kline row (ts, OHLC, vol, cts, qvol, n, tbv, tqv, _)."""
    return [ts, str(open_), str(high), str(low), str(close),
            "1000", str(ts + 1000), "1000000", "10", "0", "0", "0"]


def _fixture_df(n_bars=120, trough=36, bounce=0.05, fwd_rise=1.015):
    """Synthetic df: decline to `trough`, +bounce%, brief decline, then uptrend."""
    close = [100.0]
    for i in range(1, n_bars):
        if i <= trough:
            close.append(close[-1] * 0.99)
        elif i == trough + 1:
            close.append(close[-1] * (1 + bounce))
        elif i <= trough + 4:
            close.append(close[-1] * 0.99)
        else:
            close.append(close[-1] * fwd_rise)
    ts = [i * 4 * 3600 * 1000 for i in range(n_bars)]
    open_ = [close[0]] + close[:-1]
    high = [max(o, c) * 1.001 for o, c in zip(open_, close)]
    low = [min(o, c) * 0.999 for o, c in zip(open_, close)]
    return pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low,
                         "close": close, "vol": [1000.0] * n_bars,
                         "qvol": [1e6] * n_bars})


class TestBacktestForwardWindow(unittest.TestCase):
    """R4 - only signals with a complete forward window may be scored."""

    def _truncated_df(self):
        """Only entry-signal (bar 77) has i + fwd_bars >= len(df)."""
        return _fixture_df(n_bars=120, trough=73, bounce=0.05)

    def _complete_df(self):
        """Entry-signal at bar 40 owns all 72 forward bars."""
        return _fixture_df(n_bars=120, trough=36, bounce=0.05)

    def test_v1_tail_signal_with_truncated_horizon_not_scored(self):
        df = self._truncated_df()
        cfg = v1.Config(fwd_bars=72, n_candles=500)
        wins, losses, total, _ = v1._backtest(df, v1._atr(df), cfg, "TESTUSDT")
        self.assertEqual((wins, losses, total), (0, 0, 0))

    def test_v1_signal_with_complete_horizon_is_scored(self):
        df = self._complete_df()
        cfg = v1.Config(fwd_bars=72, n_candles=500)
        wins, losses, total, _ = v1._backtest(df, v1._atr(df), cfg, "TESTUSDT")
        self.assertEqual((wins, losses, total), (1, 0, 1))

    def test_proto_tail_signal_with_truncated_horizon_not_scored(self):
        df = self._truncated_df()
        with mock.patch.object(proto, "MIN_SIGNALS", 1):
            wr, sigs = proto.backtest(df, proto.calc_atr(df), "TESTUSDT")
        self.assertEqual(sigs, 0)

    def test_proto_signal_with_complete_horizon_is_scored(self):
        df = self._complete_df()
        with mock.patch.object(proto, "MIN_SIGNALS", 1):
            wr, sigs = proto.backtest(df, proto.calc_atr(df), "TESTUSDT")
        self.assertEqual((wr, sigs), (1.0, 1))


class TestDrillDown(unittest.TestCase):
    """R5 - same-candle TP/SL dual hits resolve via lower-timeframe drill-down."""

    TS0 = 1_000_000_000_000
    TP = 110.0
    SL = 99.0

    def _proto(self, rows):
        return mock.patch.object(proto, "GET", return_value=rows)

    def _v1(self, rows):
        return mock.patch.object(v1, "_get", return_value=rows)

    def test_proto_drilldown_tp_first_wins(self):
        rows = [
            _kline(self.TS0, 100, 112, 105, 108),
            _kline(self.TS0 + 7_200_000, 108, 109, 104, 105),
        ]
        with self._proto(rows):
            res = proto._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "win")

    def test_proto_drilldown_sl_first_loses(self):
        rows = [
            _kline(self.TS0, 100, 104, 98, 101),
            _kline(self.TS0 + 7_200_000, 101, 105, 100, 102),
        ]
        with self._proto(rows):
            res = proto._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_proto_drilldown_recurses_into_child(self):
        rows_2h = [
            _kline(self.TS0, 100, 112, 98, 105),
            _kline(self.TS0 + 7_200_000, 105, 106, 100, 101),
        ]
        rows_1h = [_kline(self.TS0 + 600_000, 100, 112, 105, 108)]

        def fake_get(url, params=None, retries=3):
            iv = (params or {}).get("interval")
            if iv == "2h":
                return rows_2h
            if iv == "1h":
                return rows_1h
            return None

        with mock.patch.object(proto, "GET", side_effect=fake_get) as m:
            res = proto._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "win")
        intervals = [c.args[1].get("interval") for c in m.call_args_list]
        self.assertIn("2h", intervals)
        self.assertIn("1h", intervals)

    def test_proto_drilldown_fetch_failure_is_loss(self):
        with self._proto(None):
            res = proto._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_proto_drilldown_chain_end_is_loss(self):
        rows = [_kline(self.TS0, 100, 112, 98, 105)]
        with self._proto(rows):
            res = proto._resolve_dual_hit(
                "TESTUSDT", "1m", self.TS0, self.TS0 + 60_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_proto_drilldown_skips_neutral_children_then_wins(self):
        rows = [
            _kline(self.TS0, 100, 105, 100, 103),               # neither level
            _kline(self.TS0 + 7_200_000, 103, 112, 104, 108),   # TP only
            _kline(self.TS0 + 14_400_000, 108, 109, 104, 105),
        ]
        with self._proto(rows) as m:
            res = proto._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "win")
        # exactly one lower-timeframe fetch for the whole window
        self.assertEqual(len(m.call_args_list), 1)

    def test_proto_drilldown_skips_neutral_children_then_loses(self):
        rows = [
            _kline(self.TS0, 100, 105, 100, 103),               # neither level
            _kline(self.TS0 + 7_200_000, 103, 104, 98, 101),    # SL only
            _kline(self.TS0 + 14_400_000, 101, 105, 100, 102),
        ]
        with self._proto(rows):
            res = proto._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_proto_drilldown_no_child_hits_is_loss(self):
        rows = [
            _kline(self.TS0, 100, 105, 100, 103),
            _kline(self.TS0 + 7_200_000, 103, 105, 101, 104),
            _kline(self.TS0 + 14_400_000, 104, 106, 102, 105),
        ]
        with self._proto(rows):
            res = proto._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_v1_drilldown_tp_first_wins(self):
        rows = [
            _kline(self.TS0, 100, 112, 105, 108),
            _kline(self.TS0 + 7_200_000, 108, 109, 104, 105),
        ]
        with self._v1(rows):
            res = v1._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "win")

    def test_v1_drilldown_sl_first_loses(self):
        rows = [
            _kline(self.TS0, 100, 104, 98, 101),
            _kline(self.TS0 + 7_200_000, 101, 105, 100, 102),
        ]
        with self._v1(rows):
            res = v1._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_v1_drilldown_recurses_into_child(self):
        rows_2h = [
            _kline(self.TS0, 100, 112, 98, 105),
            _kline(self.TS0 + 7_200_000, 105, 106, 100, 101),
        ]
        rows_1h = [_kline(self.TS0 + 600_000, 100, 112, 105, 108)]

        def fake_get(url, params=None, retries=3):
            iv = (params or {}).get("interval")
            if iv == "2h":
                return rows_2h
            if iv == "1h":
                return rows_1h
            return None

        with mock.patch.object(v1, "_get", side_effect=fake_get) as m:
            res = v1._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "win")
        intervals = [c.args[1].get("interval") for c in m.call_args_list]
        self.assertIn("2h", intervals)
        self.assertIn("1h", intervals)

    def test_v1_drilldown_fetch_failure_is_loss(self):
        with self._v1(None):
            res = v1._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_v1_drilldown_chain_end_is_loss(self):
        rows = [_kline(self.TS0, 100, 112, 98, 105)]
        with self._v1(rows):
            res = v1._resolve_dual_hit(
                "TESTUSDT", "1m", self.TS0, self.TS0 + 60_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_v1_drilldown_skips_neutral_children_then_wins(self):
        rows = [
            _kline(self.TS0, 100, 105, 100, 103),               # neither level
            _kline(self.TS0 + 7_200_000, 103, 112, 104, 108),   # TP only
            _kline(self.TS0 + 14_400_000, 108, 109, 104, 105),
        ]
        with self._v1(rows) as m:
            res = v1._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "win")
        self.assertEqual(len(m.call_args_list), 1)

    def test_v1_drilldown_skips_neutral_children_then_loses(self):
        rows = [
            _kline(self.TS0, 100, 105, 100, 103),               # neither level
            _kline(self.TS0 + 7_200_000, 103, 104, 98, 101),    # SL only
            _kline(self.TS0 + 14_400_000, 101, 105, 100, 102),
        ]
        with self._v1(rows):
            res = v1._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_v1_drilldown_no_child_hits_is_loss(self):
        rows = [
            _kline(self.TS0, 100, 105, 100, 103),
            _kline(self.TS0 + 7_200_000, 103, 105, 101, 104),
            _kline(self.TS0 + 14_400_000, 104, 106, 102, 105),
        ]
        with self._v1(rows):
            res = v1._resolve_dual_hit(
                "TESTUSDT", "4h", self.TS0, self.TS0 + 14_400_000, self.TP, self.SL)
        self.assertEqual(res, "loss")

    def test_proto_and_v1_drilldown_agree(self):
        """Determinism: both scripts resolve identical payloads identically."""
        ts0 = self.TS0
        scenarios = [
            ("win", [_kline(ts0, 100, 112, 105, 108)]),
            ("loss", [_kline(ts0, 100, 104, 98, 101)]),
        ]
        for expected, rows in scenarios:
            with mock.patch.object(proto, "GET", return_value=rows):
                pr = proto._resolve_dual_hit(
                    "TESTUSDT", "4h", ts0, ts0 + 14_400_000, self.TP, self.SL)
            with mock.patch.object(v1, "_get", return_value=rows):
                vr = v1._resolve_dual_hit(
                    "TESTUSDT", "4h", ts0, ts0 + 14_400_000, self.TP, self.SL)
            self.assertEqual(pr, expected)
            self.assertEqual(vr, expected)
            self.assertEqual(pr, vr)

    def _dual_hit_df(self):
        df = _fixture_df()
        atr_s = v1._atr(df)
        pr = df["close"].iloc[40]
        av = atr_s.iloc[40]
        tp = pr + av * 8
        sl = pr - av
        df.loc[41, "open"] = pr
        df.loc[41, "high"] = tp * 1.001
        df.loc[41, "low"] = sl * 0.999
        df.loc[41, "close"] = pr
        return df, atr_s, tp, sl

    def test_v1_backtest_dual_hit_resolves_win(self):
        df, atr_s, tp, sl = self._dual_hit_df()
        rows = [
            _kline(int(df["ts"].iloc[41]), 100, tp * 1.002, sl + 2, tp),
            _kline(int(df["ts"].iloc[41]) + 7_200_000, 105, 106, 100, 101),
        ]
        with self._v1(rows):
            wins, losses, total, _ = v1._backtest(
                df, atr_s, v1.Config(fwd_bars=72, n_candles=500), "TESTUSDT")
        self.assertEqual((wins, losses, total), (1, 0, 1))

    def test_v1_backtest_dual_hit_resolves_loss(self):
        df, atr_s, tp, sl = self._dual_hit_df()
        rows = [
            _kline(int(df["ts"].iloc[41]), 100, (tp + sl) / 2, sl * 0.998, sl),
            _kline(int(df["ts"].iloc[41]) + 7_200_000, 105, 106, 100, 101),
        ]
        with self._v1(rows):
            wins, losses, total, _ = v1._backtest(
                df, atr_s, v1.Config(fwd_bars=72, n_candles=500), "TESTUSDT")
        self.assertEqual((wins, losses, total), (0, 1, 1))


class TestOrderQuantization(unittest.TestCase):
    """R3 - orders must be exact tick/step multiples and pass validation."""

    def _pair(self, tick="0.5", step="0.001", min_qty="0", min_notional="5",
              max_qty="1000000000", min_price="0", max_price="1000000",
              max_notional=None, market_min_qty=None, market_max_qty=None,
              market_step=None, percent_min_mult=None,
              percent_max_mult=None, percent_ref=None,
              percent_buy_min_mult=None, percent_buy_max_mult=None):
        min_price_value = Decimal(min_price)
        max_price_value = Decimal(max_price)
        tick_value = Decimal(tick)
        return {
            "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "min_val": Decimal(min_notional),
            "max_val": Decimal(max_notional) if max_notional is not None else None,
            "min_qty": Decimal(min_qty), "max_qty": Decimal(max_qty),
            "min_price": min_price_value if min_price_value > 0 else None,
            "max_price": max_price_value if max_price_value > 0 else None,
            "step": Decimal(step), "tick": tick_value if tick_value > 0 else None,
            "market_min_qty": (Decimal(market_min_qty)
                               if market_min_qty is not None else None),
            "market_max_qty": (Decimal(market_max_qty)
                               if market_max_qty is not None else None),
            "market_step": (Decimal(market_step)
                            if market_step is not None else None),
            "percent_min_mult": (Decimal(percent_min_mult)
                                 if percent_min_mult is not None else None),
            "percent_max_mult": (Decimal(percent_max_mult)
                                 if percent_max_mult is not None else None),
            "percent_buy_min_mult": (
                Decimal(percent_buy_min_mult or "0.2")
                if percent_min_mult is not None else None),
            "percent_buy_max_mult": (
                Decimal(percent_buy_max_mult or "5")
                if percent_min_mult is not None else None),
            "percent_avg_mins": 5 if percent_min_mult is not None else None,
            "percent_ref": (Decimal(percent_ref)
                            if percent_ref is not None else None),
        }

    def test_proto_order_quantized_to_tick_and_step(self):
        order = proto.build_order(100.0, 1.3, self._pair())
        self.assertIsNotNone(order)
        tick, step = 0.5, 0.001
        self.assertAlmostEqual(order["qty"] / step, round(order["qty"] / step), places=6)
        self.assertAlmostEqual(order["tp"] % tick, 0.0, places=6)
        self.assertAlmostEqual(order["trig"] % tick, 0.0, places=6)
        self.assertAlmostEqual(order["sl"] % tick, 0.0, places=6)
        self.assertGreaterEqual(order["tp"], 100 + 8 * 1.3)    # TP rounded up
        self.assertLessEqual(order["sl"], 100 - 1.3)           # SL rounded down
        self.assertAlmostEqual(order["gain"],
                               (order["tp"] - 100) * order["qty"], places=6)

    def test_v1_order_quantized_to_tick_and_step(self):
        cfg = v1.Config()
        order = v1.build_order(100.0, 1.3, cfg, self._pair())
        self.assertIsNotNone(order)
        tp, sl, trig, qty = order
        tick, step = 0.5, 0.001
        self.assertAlmostEqual(qty / step, round(qty / step), places=6)
        self.assertAlmostEqual(tp % tick, 0.0, places=6)
        self.assertAlmostEqual(trig % tick, 0.0, places=6)
        self.assertAlmostEqual(sl % tick, 0.0, places=6)
        self.assertGreaterEqual(tp, 100 + 8 * 1.3)
        self.assertLessEqual(sl, 100 - 1.3)

    def test_proto_rejects_below_min_qty(self):
        self.assertIsNone(proto.build_order(100.0, 1.3, self._pair(min_qty="1")))

    def test_proto_rejects_below_min_notional(self):
        self.assertIsNone(proto.build_order(100.0, 1.3, self._pair(min_notional="50")))

    def test_v1_rejects_below_min_notional(self):
        cfg = v1.Config()
        self.assertIsNone(v1.build_order(100.0, 1.3, cfg, self._pair(min_notional="50")))

    def test_proto_rejects_above_max_qty(self):
        self.assertIsNone(proto.build_order(100.0, 1.3, self._pair(max_qty="0.05")))

    def test_proto_rejects_above_max_price(self):
        self.assertIsNone(proto.build_order(100.0, 1.3, self._pair(max_price="95")))

    def test_v1_rejects_above_max_qty(self):
        self.assertIsNone(v1.build_order(
            100.0, 1.3, v1.Config(), self._pair(max_qty="0.05")))

    def test_v1_rejects_above_max_price(self):
        self.assertIsNone(v1.build_order(
            100.0, 1.3, v1.Config(), self._pair(max_price="95")))

    def test_proto_rejects_above_max_notional(self):
        self.assertIsNone(proto.build_order(100.0, 1.3, self._pair(max_notional="5")))

    def test_proto_accepts_within_max_notional(self):
        order = proto.build_order(100.0, 1.3, self._pair(max_notional="50"))
        self.assertIsNotNone(order)

    def test_v1_rejects_above_max_notional(self):
        cfg = v1.Config()
        self.assertIsNone(v1.build_order(100.0, 1.3, cfg, self._pair(max_notional="5")))

    def test_orders_accept_disabled_price_rules(self):
        pair = self._pair(tick="0", min_price="0", max_price="0")
        self.assertIsNotNone(proto.build_order(100.0, 1.3, pair))
        self.assertIsNotNone(v1.build_order(100.0, 1.3, v1.Config(), pair))

    def test_orders_reject_market_lot_size_violation(self):
        pair = self._pair(market_max_qty="0.05")
        self.assertIsNone(proto.build_order(100.0, 1.3, pair))
        self.assertIsNone(v1.build_order(100.0, 1.3, v1.Config(), pair))

    def test_orders_enforce_sell_percent_price_limits(self):
        accepted = self._pair(
            percent_min_mult="0.8", percent_max_mult="1.2", percent_ref="100")
        rejected = self._pair(
            percent_min_mult="0.8", percent_max_mult="1.05", percent_ref="100")
        self.assertIsNotNone(proto.build_order(100.0, 1.3, accepted))
        self.assertIsNotNone(v1.build_order(100.0, 1.3, v1.Config(), accepted))
        self.assertIsNone(proto.build_order(100.0, 1.3, rejected))
        self.assertIsNone(v1.build_order(100.0, 1.3, v1.Config(), rejected))

    def test_orders_reject_missing_percent_price_reference(self):
        pair = self._pair(
            percent_min_mult="0.8", percent_max_mult="1.2")
        self.assertIsNone(proto.build_order(100.0, 1.3, pair))
        self.assertIsNone(v1.build_order(100.0, 1.3, v1.Config(), pair))

    def test_orders_enforce_buy_percent_price_limits_for_limit_entry(self):
        pair = self._pair(
            percent_min_mult="0.8", percent_max_mult="1.2", percent_ref="100",
            percent_buy_min_mult="1.01", percent_buy_max_mult="1.2")
        self.assertIsNone(proto.build_order(100.0, 1.3, pair))
        self.assertIsNone(v1.build_order(100.0, 1.3, v1.Config(), pair))

    def test_orders_reject_nonfinite_inputs(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            self.assertIsNone(proto.build_order(value, 1.3, self._pair()))
            self.assertIsNone(v1.build_order(value, 1.3, v1.Config(), self._pair()))

    def test_proto_ordering_collapses_after_quantization(self):
        """Huge tick: trigger rounds up to (or above) the entry — invalid."""
        self.assertIsNone(proto.build_order(100.0, 1.0, self._pair(tick="10")))

    def test_v1_ordering_collapses_after_quantization(self):
        cfg = v1.Config()
        self.assertIsNone(v1.build_order(100.0, 1.0, cfg, self._pair(tick="10")))

    def _scan_df(self):
        """120-bar fixture with valid RSI (declines + dips) and ~1.3% ATR."""
        n_bars = 120
        close = [100.0]
        for i in range(1, n_bars):
            if i <= 36:
                close.append(close[-1] * 0.99)
            elif i == 37:
                close.append(close[-1] * 1.05)
            elif i % 2 == 0:
                close.append(close[-1] * 1.015)
            else:
                close.append(close[-1] * 0.997)
        ts = [i * 4 * 3600 * 1000 for i in range(n_bars)]
        open_ = [close[0]] + close[:-1]
        high = [max(o, c) * 1.0065 for o, c in zip(open_, close)]
        low = [min(o, c) * 0.9935 for o, c in zip(open_, close)]
        return pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low,
                             "close": close, "vol": [1000.0] * n_bars,
                             "qvol": [1e6] * n_bars})

    def test_v1_scan_pair_persists_quantized_order(self):
        df = self._scan_df()

        p = self._pair()
        p.update({"symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
                  "price": 100.0, "volume": 5e6, "chg24": 1.0})
        cfg = v1.Config(rsi_low=0, rsi_high=100, lo_margin=100.0,
                        min_atr_pct=0.001, min_wr=0.0, max_wr=1.0,
                        min_signals=1, fwd_bars=72, n_candles=120)
        log = v1.Logger("")
        with tempfile.TemporaryDirectory() as td:
            conn = v1.db_open(os.path.join(td, "scanner.db"), log)
            with _Quiet():
                with mock.patch.object(v1, "_candles", return_value=df):
                    is_cand = v1.scan_pair(p, 1, 1, 1, cfg, log, conn)
            row = conn.execute(
                "SELECT * FROM pair_scans WHERE run_id=1").fetchone()
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM pair_scans WHERE run_id=1").description]
            conn.close()
        self.assertTrue(is_cand)
        r = dict(zip(cols, row))
        self.assertEqual(r["status"], "CANDIDATE")
        tick, step = 0.5, 0.001
        self.assertAlmostEqual(r["qty"] / step, round(r["qty"] / step), places=6)
        self.assertAlmostEqual(r["tp"] % tick, 0.0, places=6)
        self.assertAlmostEqual(r["sl_trigger"] % tick, 0.0, places=6)
        self.assertAlmostEqual(r["sl"] % tick, 0.0, places=6)

    def test_v1_scan_pair_rejects_invalid_order(self):
        df = self._scan_df()

        p = self._pair(min_qty="1")
        p.update({"symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
                  "price": 100.0, "volume": 5e6, "chg24": 1.0})
        cfg = v1.Config(rsi_low=0, rsi_high=100, lo_margin=100.0,
                        min_atr_pct=0.001, min_wr=0.0, max_wr=1.0,
                        min_signals=1, fwd_bars=72, n_candles=120)
        log = v1.Logger("")
        with tempfile.TemporaryDirectory() as td:
            conn = v1.db_open(os.path.join(td, "scanner.db"), log)
            with _Quiet():
                with mock.patch.object(v1, "_candles", return_value=df):
                    is_cand = v1.scan_pair(p, 1, 1, 1, cfg, log, conn)
            row = conn.execute(
                "SELECT * FROM pair_scans WHERE run_id=1").fetchone()
            cols = [d[0] for d in conn.execute(
                "SELECT * FROM pair_scans WHERE run_id=1").description]
            conn.close()
        self.assertFalse(is_cand)
        r = dict(zip(cols, row))
        self.assertEqual(r["status"], "REJECTED")
        self.assertEqual(r["reason"], "ORDER_INVALID")

class TestCliValidation(unittest.TestCase):
    """R8 - invalid CLI ranges and relationships must fail during parsing."""

    def _parse(self, *argv):
        old = sys.argv
        sys.argv = ["binance_scanner.py"] + list(argv)
        try:
            return v1.parse_args()
        finally:
            sys.argv = old

    def test_defaults_parse_ok(self):
        cfg, hist = self._parse()
        self.assertFalse(hist)
        self.assertAlmostEqual(cfg.budget, 10.0)

    def test_valid_boundaries_parse_ok(self):
        cfg, _ = self._parse("--budget", "25", "--min-wr", "1", "--max-wr", "99",
                             "--interval", "1m", "--n-candles", "1000",
                             "--trig-mult", "0.1", "--sl-mult", "2")
        self.assertAlmostEqual(cfg.min_wr, 0.01)
        self.assertAlmostEqual(cfg.max_wr, 0.99)

    def test_inverted_win_rate_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--min-wr", "20", "--max-wr", "5")

    def test_zero_budget_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--budget", "0")

    def test_trig_not_below_sl_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--trig-mult", "2", "--sl-mult", "1")

    def test_unknown_interval_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--interval", "7x")

    def test_n_candles_below_60_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--n-candles", "50")

    def test_n_candles_above_1000_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--n-candles", "2000")

    def test_insufficient_forward_window_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--n-candles", "150", "--fwd-bars", "120")

    def test_inverted_rsi_bounds_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse("--rsi-low", "50", "--rsi-high", "20")

    def test_nonfinite_float_values_rejected(self):
        for option in ("--budget", "--max-wr", "--min-atr-pct"):
            for value in ("nan", "inf", "-inf"):
                with self.subTest(option=option, value=value):
                    with self.assertRaises(SystemExit):
                        self._parse(option, value)


class TestSignalCountPersisted(unittest.TestCase):
    """R10 - actual signal count is persisted before minimum-signal rejection."""

    def test_few_signals_row_keeps_n_signals(self):
        df = TestOrderQuantization()._scan_df()
        p = TestOrderQuantization()._pair()
        p.update({"symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
                  "price": 100.0, "volume": 5e6, "chg24": 1.0})
        cfg = v1.Config(rsi_low=0, rsi_high=100, lo_margin=100.0,
                        min_atr_pct=0.001, min_wr=0.0, max_wr=1.0,
                        min_signals=99, fwd_bars=72, n_candles=120)
        log = v1.Logger("")
        with tempfile.TemporaryDirectory() as td:
            conn = v1.db_open(os.path.join(td, "scanner.db"), log)
            with _Quiet():
                with mock.patch.object(v1, "_candles", return_value=df):
                    is_cand = v1.scan_pair(p, 1, 1, 1, cfg, log, conn)
            row = conn.execute(
                "SELECT status, reason, n_signals FROM pair_scans").fetchone()
            conn.close()
        self.assertFalse(is_cand)
        self.assertEqual(row[0], "REJECTED")
        self.assertEqual(row[1], "FEW_SIGNALS")
        self.assertGreater(row[2], 0)          # actual count kept, not 0


class TestHistoryIncomplete(unittest.TestCase):
    """R11 - interrupted runs render INCOMPLETE; completed summary excludes them."""

    def _cand_row(self, run_id, symbol):
        return dict(run_id=run_id, vol_rank=1, symbol=symbol, base=symbol[:3],
                    quote="USDT", price=1.0, volume_24h=1e6, change_24h=1.0,
                    atr=0.1, atr_pct=1.0, win_rate=0.1, n_signals=10,
                    wins=1, losses=9, flat_skips=0, tp=2.0, sl=0.5,
                    sl_trigger=0.6, qty=10.0, pot_gain=10.0, pot_loss=-5.0,
                    rr_ratio=2.0, ev_per_risk=0.1, status="CANDIDATE",
                    reason="WR_IN_RANGE", reason_detail="", scanned_at="2026-01-01T00:00:00")

    def test_interrupted_run_rendered_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "scanner.db")
            log = v1.Logger("")
            conn = v1.db_open(db_path, log)
            run_incomplete = v1.db_insert_run(conn, v1.Config())
            run_final = v1.db_insert_run(conn, v1.Config())
            v1.db_finalise_run(conn, run_final, 1, 1, 1, 1, 1.0)
            v1.db_insert_pair(conn, self._cand_row(run_incomplete, "GHOSTUSDT"))
            v1.db_insert_pair(conn, self._cand_row(run_final, "KEEPUSDT"))
            conn.close()

            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                v1.show_history(db_path)
            text = out.getvalue()

        self.assertIn("INCOMPLETE", text)
        self.assertIn("KEEPUSDT", text)
        self.assertNotIn("GHOSTUSDT", text)


class TestLogfileTee(unittest.TestCase):
    """Always-on timestamped log file - proto console output lands in a log file."""

    def test_init_logfile_tees_stdout_with_timestamps(self):
        real_out, real_err = sys.stdout, sys.stderr
        with tempfile.TemporaryDirectory() as td:
            path = proto.init_logfile(prefix="proto", logdir=td)
            tee = sys.stdout
            try:
                self.assertTrue(os.path.isfile(path))
                self.assertTrue(os.path.basename(path).startswith("proto_"))
                self.assertRegex(os.path.basename(path),
                                 r"^proto_\d{8}_\d{6}\.log$")
                print("hello log")
                print("line two")
                sys.stdout.flush()
                self.assertEqual(proto._LOG_PATH, path)
            finally:
                sys.stdout, sys.stderr = real_out, real_err
                proto._LOG_ACTIVE = False
                proto._LOG_PATH = ""
                tee._fh.close()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("[", text)
        self.assertRegex(text, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
        self.assertIn("hello log", text)
        self.assertIn("line two", text)

    def test_proto_exclusive_path_suffixes_on_collision(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            first = proto._exclusive_path(d, "proto", "20260811_010203")
            second = proto._exclusive_path(d, "proto", "20260811_010203")
            third = proto._exclusive_path(d, "proto", "20260811_010203")
            self.assertEqual(first.name, "proto_20260811_010203.log")
            self.assertEqual(second.name, "proto_20260811_010203_1.log")
            self.assertEqual(third.name, "proto_20260811_010203_2.log")
            for p in (first, second, third):
                self.assertTrue(p.is_file())

    def test_v1_exclusive_path_suffixes_on_collision(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            first = v1._exclusive_path(d, "v1", "20260811_010203")
            second = v1._exclusive_path(d, "v1", "20260811_010203")
            third = v1._exclusive_path(d, "v1", "20260811_010203")
            self.assertEqual(first.name, "v1_20260811_010203.log")
            self.assertEqual(second.name, "v1_20260811_010203_1.log")
            self.assertEqual(third.name, "v1_20260811_010203_2.log")
            for p in (first, second, third):
                self.assertTrue(p.is_file())


class TestVerboseLogging(unittest.TestCase):
    """Proto - verbose logger style: embedded timestamps kept, ANSI stripped."""

    def test_tee_keeps_embedded_timestamp_no_double_prefix(self):
        real_out, real_err = sys.stdout, sys.stderr
        with tempfile.TemporaryDirectory() as td:
            path = proto.init_logfile(prefix="proto", logdir=td)
            tee = sys.stdout
            try:
                print("[23:15:28] INFO  some message")
                print("plain line")
                sys.stdout.flush()
            finally:
                sys.stdout, sys.stderr = real_out, real_err
                proto._LOG_ACTIVE = False
                proto._LOG_PATH = ""
                tee._fh.close()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("[23:15:28] INFO  some message", text)
        self.assertNotIn("] [23:15:28]", text)
        self.assertRegex(text, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] plain")

    def test_tee_strips_ansi_codes(self):
        real_out, real_err = sys.stdout, sys.stderr
        with tempfile.TemporaryDirectory() as td:
            path = proto.init_logfile(prefix="proto", logdir=td)
            tee = sys.stdout
            try:
                proto.log.ok("message")
                sys.stdout.flush()
            finally:
                sys.stdout, sys.stderr = real_out, real_err
                proto._LOG_ACTIVE = False
                proto._LOG_PATH = ""
                tee._fh.close()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertNotIn("\033", text)
        self.assertIn("PASS  message", text)


class TestFilterCounts(unittest.TestCase):
    """Proto - verbose filter helpers count per-reason rejections."""

    def test_exchange_counts_reasons(self):
        info = {"symbols": [
            _symbol("BTCUSDT", "BTC", "USDT"),
            _symbol("PAUSEDUSDT", "PAUSED", "USDT", status="BREAK"),
            _symbol("EURUSDT", "EUR", "EUR"),
            _symbol("UPUSDT", "UP", "USDT", spot=False),
            _symbol("BIGUSDT", "BIG", "USDT", min_notional="50"),
        ]}
        pairs, counts = proto.exchange_filter_counts(info)
        self.assertEqual(counts["NOT_TRADING"], 1)
        self.assertEqual(counts["WRONG_QUOTE"], 1)
        self.assertEqual(counts["NOT_SPOT"], 1)
        self.assertEqual(counts["HIGH_NOTIONAL"], 1)
        self.assertEqual(counts["PASSED"], 1)
        self.assertEqual([p["symbol"] for p in pairs], ["BTCUSDT"])

    def test_exchange_rejects_missing_filters(self):
        bad = _symbol("BADUSDT", "BAD", "USDT")
        bad["filters"] = [f for f in bad["filters"] if f["filterType"] != "PRICE_FILTER"]
        info = {"symbols": [_symbol("GOODUSDT", "GOOD", "USDT"), bad]}
        pairs, counts = proto.exchange_filter_counts(info)
        self.assertEqual(counts["BAD_FILTERS"], 1)
        self.assertEqual(counts["PASSED"], 1)
        self.assertEqual([p["symbol"] for p in pairs], ["GOODUSDT"])

    def test_exchange_rejects_invalid_filter_data(self):
        bad = _symbol("BADUSDT", "BAD", "USDT", tick="not-a-number")
        info = {"symbols": [bad]}
        pairs, counts = proto.exchange_filter_counts(info)
        self.assertEqual(counts["BAD_FILTERS"], 1)
        self.assertEqual(pairs, [])

    def test_both_parsers_reject_malformed_filter_shapes(self):
        cases = []
        missing_type = _symbol("BADUSDT", "BAD", "USDT")
        missing_type["filters"].append({"tickSize": "1"})
        cases.append(missing_type)
        null_filters = _symbol("BADUSDT", "BAD", "USDT")
        null_filters["filters"] = None
        cases.append(null_filters)
        null_tick = _symbol("BADUSDT", "BAD", "USDT", tick=None)
        cases.append(null_tick)
        nan_tick = _symbol("BADUSDT", "BAD", "USDT", tick="NaN")
        cases.append(nan_tick)
        inverted_price = _symbol(
            "BADUSDT", "BAD", "USDT", min_price="10", max_price="1")
        cases.append(inverted_price)

        for scanner in (proto, v1):
            for symbol in cases:
                with self.subTest(scanner=scanner.__name__, case=symbol["filters"]):
                    self.assertIsNone(scanner.parse_symbol_filters(symbol))

    def test_both_parsers_accept_disabled_price_rules(self):
        symbol = _symbol(
            "GOODUSDT", "GOOD", "USDT", min_price="0",
            max_price="0", tick="0")
        for scanner in (proto, v1):
            filters = scanner.parse_symbol_filters(symbol)
            self.assertIsNotNone(filters)
            self.assertIsNone(filters["min_price"])
            self.assertIsNone(filters["max_price"])
            self.assertIsNone(filters["tick"])

    def test_both_parsers_carry_market_lot_size(self):
        symbol = _symbol(
            "GOODUSDT", "GOOD", "USDT", market_min_qty="0.1",
            market_max_qty="10", market_step="0.1")
        for scanner in (proto, v1):
            filters = scanner.parse_symbol_filters(symbol)
            self.assertEqual(filters["market_min_qty"], Decimal("0.1"))
            self.assertEqual(filters["market_max_qty"], Decimal("10"))
            self.assertEqual(filters["market_step"], Decimal("0.1"))

    def test_both_parsers_carry_sell_percent_price_limits(self):
        symbol = _symbol(
            "GOODUSDT", "GOOD", "USDT", percent_sell_down="0.8",
            percent_sell_up="1.2", percent_avg_mins=5)
        for scanner in (proto, v1):
            filters = scanner.parse_symbol_filters(symbol)
            self.assertEqual(filters["percent_min_mult"], Decimal("0.8"))
            self.assertEqual(filters["percent_max_mult"], Decimal("1.2"))
            self.assertEqual(filters["percent_buy_min_mult"], Decimal("0.2"))
            self.assertEqual(filters["percent_buy_max_mult"], Decimal("5"))
            self.assertEqual(filters["percent_avg_mins"], 5)

    def test_both_parsers_reject_invalid_percent_price_limits(self):
        symbol = _symbol(
            "BADUSDT", "BAD", "USDT", percent_sell_down="1.2",
            percent_sell_up="0.8", percent_avg_mins=5)
        for scanner in (proto, v1):
            self.assertIsNone(scanner.parse_symbol_filters(symbol))

    def test_both_load_matching_percent_price_reference(self):
        symbol = _symbol(
            "GOODUSDT", "GOOD", "USDT", percent_sell_down="0.8",
            percent_sell_up="1.2", percent_avg_mins=5)
        response = {"mins": 5, "price": "100.25"}
        for scanner, getter in ((proto, "GET"), (v1, "_get")):
            filters = scanner.parse_symbol_filters(symbol)
            filters["symbol"] = symbol["symbol"]
            with mock.patch.object(scanner, getter, return_value=response):
                self.assertTrue(scanner.load_percent_price_reference(filters))
            self.assertEqual(filters["percent_ref"], Decimal("100.25"))

    def test_exchange_rejects_missing_filters_v1(self):
        bad = _symbol("BADUSDT", "BAD", "USDT")
        bad["filters"] = [f for f in bad["filters"] if f["filterType"] != "LOT_SIZE"]
        info = {"symbols": [_symbol("GOODUSDT", "GOOD", "USDT"), bad]}
        log = v1.Logger("")
        with _Quiet():
            with mock.patch.object(v1, "_get", return_value=info):
                pairs = v1.step_exchange(v1.Config(), log)
        self.assertEqual([p["symbol"] for p in pairs], ["GOODUSDT"])

    def test_pairs_carry_full_filter_decimals(self):
        info = {"symbols": [_symbol("BTCUSDT", "BTC", "USDT", max_notional="500")]}
        pairs, _ = proto.exchange_filter_counts(info)
        p = pairs[0]
        self.assertIsNone(p["min_price"])
        self.assertEqual(p["max_price"], Decimal("1000000"))
        self.assertEqual(p["tick"], Decimal("0.0001"))
        self.assertEqual(p["min_qty"], Decimal("0"))
        self.assertEqual(p["max_qty"], Decimal("1000000000"))
        self.assertEqual(p["step"], Decimal("0.01"))
        self.assertEqual(p["min_val"], Decimal("1.0"))
        self.assertEqual(p["max_val"], Decimal("500"))
        self.assertIsNone(p["market_min_qty"])
        self.assertIsNone(p["market_max_qty"])
        self.assertIsNone(p["market_step"])
        self.assertIsNone(p["percent_min_mult"])
        self.assertIsNone(p["percent_max_mult"])
        self.assertIsNone(p["percent_avg_mins"])

    def test_ticker_counts_reasons(self):
        pairs = [
            _symbol("GOODUSDT", "GOOD", "USDT"),
            _symbol("GONEUSDT", "GONE", "USDT"),
            _symbol("ZEROUSDT", "ZERO", "USDT"),
            _symbol("LOWUSDT", "LOW", "USDT"),
            _symbol("BIGUSDT", "BIG", "USDT"),
        ]
        tickers = [
            _ticker("GOODUSDT"),
            _ticker("ZEROUSDT", price="0.0"),
            _ticker("LOWUSDT", vol="10"),
            _ticker("BIGUSDT", price="100000000"),
        ]
        with mock.patch.object(proto, "GET", return_value=tickers):
            out, counts = proto.ticker_filter_counts(pairs)
        self.assertEqual(counts["NO_TICKER"], 1)
        self.assertEqual(counts["ZERO_PRICE"], 1)
        self.assertEqual(counts["LOW_VOL"], 1)
        self.assertEqual(counts["UNAFFORDABLE"], 1)
        self.assertEqual(counts["PASSED"], 1)
        self.assertEqual([p["symbol"] for p in out], ["GOODUSDT"])

    def test_backtest_detail_counts_flat_skips(self):
        df = _fixture_df(n_bars=120)
        with mock.patch.object(proto, "MIN_ATR_PCT", 100.0):
            wins, losses, total, flat_skips = proto._backtest_detail(
                df, proto.calc_atr(df), "TESTUSDT")
        self.assertEqual((wins, losses, total), (0, 0, 0))
        self.assertGreater(flat_skips, 0)


class TestMarkdownResults(unittest.TestCase):
    """Proto - save_results_md writes a timestamped markdown file."""

    def test_md_empty_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            path = proto.save_results_md([], datetime(2026, 1, 2, 3, 4, 5), td)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertTrue(path.endswith("binance_scan_20260102_030405.md"))
        self.assertIn("No pairs matched", text)

    def test_md_with_candidate(self):
        cand = {
            "symbol": "ABCUSDT", "base": "ABC", "quote": "USDT",
            "price": 100.0, "volume": 5e6, "chg24": 1.5,
            "atr": 2.0, "wr": 0.12, "sigs": 15,
            "tp": 116.0, "sl": 98.0, "trig": 98.3,
            "qty": 0.1, "gain": 1.6, "loss": -0.2, "rr": 8.0,
        }
        with tempfile.TemporaryDirectory() as td:
            path = proto.save_results_md([cand], datetime(2026, 1, 2, 3, 4, 5), td)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("### 1. ABCUSDT (USDT pair)", text)
        self.assertIn("| Entry | 100.0000 USDT |", text)
        self.assertIn("## Execution", text)

    def test_md_records_full_parameter_set(self):
        with tempfile.TemporaryDirectory() as td:
            path = proto.save_results_md([], datetime(2026, 1, 2, 3, 4, 5), td)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        for line in (
            f"- **TP / SL:** {proto.TP_MULT}\u00d7ATR / {proto.SL_MULT}\u00d7ATR",
            f"- **RSI window:** {proto.RSI_LOW}\u2013{proto.RSI_HIGH}",
            f"- **Min ATR:** {proto.MIN_ATR_PCT*100:.2f}% of price",
            f"- **Forward bars:** {proto.FWD_BARS}",
            f"- **Min signals:** {proto.MIN_SIGNALS}",
        ):
            self.assertIn(line, text)


class TestDefaultLogPath(unittest.TestCase):
    """V1 - default log path is timestamped under the logs dir."""

    def test_default_log_path_timestamped(self):
        with tempfile.TemporaryDirectory() as td:
            path = v1.default_log_path("v1", td)
            self.assertTrue(os.path.basename(path).startswith("v1_"))
            self.assertRegex(os.path.basename(path),
                             r"^v1_\d{8}_\d{6}\.log$")


class TestLogDashboard(unittest.TestCase):
    """Log dashboard - file listing, paginated reads, HTTP API."""

    REQUEST_TOKEN = "test-request-token"

    def setUp(self):
        with dash.JOBS_LOCK:
            dash.JOBS.clear()

    def tearDown(self):
        with dash.JOBS_LOCK:
            jobs = list(dash.JOBS.values())
            dash.JOBS.clear()
        for job in jobs:
            proc = getattr(job, "proc", None)
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

    V1_CANDIDATES = """  CANDIDATES
  \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
  #1  MMTUSDT  \u00b7  USDT pair
  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  Current Price                0.208000  USDT
  24h Volume                 65,761,812  USDT
  24h Change                     +0.10%
  ATR (4h)                     0.015821  (7.606%)
  Win Rate                       14.29%   (14 signals  W:2 / L:12)
  R : R                            8.0 : 1
  Exp. Value                   +0.2857  (per $1 risked)

  \u250c\u2500 ENTRY    \u2500\u2500\u2500\u2500             0.208000  USDT
  \u251c\u2500 TP       \u2500\u2500\u2500\u2500             0.334600  USDT   ( +60.87%)
  \u251c\u2500 SL Trig  \u2500\u2500\u2500\u2500             0.194500  USDT   (  -6.49%)
  \u2514\u2500 SL       \u2500\u2500\u2500\u2500             0.192100  USDT   (  -7.64%)

  With $10.00 budget:
    Quantity              48.000000  MMT
    If TP hit               +6.0768  USDT
    If SL hit               -0.7632  USDT

  \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
  #2  JUPUSDT  \u00b7  USDT pair
  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  Current Price                 0.900000  USDT
  24h Volume                 50,000,000  USDT
  24h Change                    -2.50%
  ATR (4h)                     0.045000  (5.000%)
  Win Rate                       12.00%   (10 signals  W:1 / L:9)
  R : R                            5.0 : 1
  Exp. Value                   -0.2000  (per $1 risked)

  \u250c\u2500 ENTRY    \u2500\u2500\u2500\u2500             0.900000  USDT
  \u251c\u2500 TP       \u2500\u2500\u2500\u2500             1.260000  USDT   ( +40.00%)
  \u251c\u2500 SL Trig  \u2500\u2500\u2500\u2500             0.855000  USDT   (  -5.00%)
  \u2514\u2500 SL       \u2500\u2500\u2500\u2500             0.850000  USDT   (  -5.56%)

  With $10.00 budget:
    Quantity              11.000000  JUP
    If TP hit               +3.9600  USDT
    If SL hit               -0.5500  USDT

  \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
  #3  XYZUSDC  \u00b7  USDC pair
  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  Current Price                 1.500000  USDC
  24h Volume                 10,000,000  USDC
  24h Change                    +5.00%
  ATR (4h)                     0.100000  (6.667%)
  Win Rate                       11.00%   (8 signals)
  R : R                            6.0 : 1
  Exp. Value                   +0.3500  (per $1 risked)

  \u250c\u2500 ENTRY    \u2500\u2500\u2500\u2500             1.500000  USDC
  \u251c\u2500 TP       \u2500\u2500\u2500\u2500             2.100000  USDC   ( +40.00%)
  \u251c\u2500 SL Trig  \u2500\u2500\u2500\u2500             1.425000  USDC   (  -5.00%)
  \u2514\u2500 SL       \u2500\u2500\u2500\u2500             1.400000  USDC   (  -6.67%)

  With $10.00 budget:
    Quantity               6.660000  XYZ
    If TP hit               +3.9960  USDC
    If SL hit               -0.6660  USDC

  \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
  Binance OCO order:
"""

    def _candidate_log(self, td):
        path = os.path.join(td, "scan_20260101_000000.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.V1_CANDIDATES)
        return path

    def _dir_with_logs(self, td, n_lines=25):
        path = os.path.join(td, "a_20260101_000000.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"line {i}" for i in range(n_lines)))
        return path

    def test_list_logs_filters_and_sorts(self):
        with tempfile.TemporaryDirectory() as td:
            self._dir_with_logs(td)
            with open(os.path.join(td, "b_20260102_000000.log"), "w") as fh:
                fh.write("x")
            with open(os.path.join(td, "notes.txt"), "w") as fh:
                fh.write("not a log")
            entries = dash.list_logs(td)
        self.assertEqual([e["name"] for e in entries],
                         ["b_20260102_000000.log", "a_20260101_000000.log"])
        self.assertEqual(entries[0]["size"], 1)
        self.assertIn("2026-", entries[0]["mtime"])

    def test_list_logs_missing_dir(self):
        self.assertEqual(dash.list_logs("C:\\no\\such\\dir\\xyz"), [])

    def test_read_page_paginates(self):
        with tempfile.TemporaryDirectory() as td:
            self._dir_with_logs(td)
            p1 = dash.read_page(td, "a_20260101_000000.log", 1, 10)
            p3 = dash.read_page(td, "a_20260101_000000.log", 3, 10)
        self.assertEqual(p1["total"], 25)
        self.assertEqual(p1["pages"], 3)
        self.assertEqual(p1["lines"][0], "line 0")
        self.assertEqual(p1["lines"][-1], "line 9")
        self.assertEqual(p3["lines"], ["line 20", "line 21", "line 22",
                                       "line 23", "line 24"])

    def test_read_page_query_filters(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "s.log"), "w", encoding="utf-8") as fh:
                fh.write("ok line\nWARN something\nok again\n")
            data = dash.read_page(td, "s.log", 1, 10, query="WARN")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["lines"], ["WARN something"])

    def test_read_page_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            for bad in ("..", "..\\x.log", "sub/x.log", "C:\\windows\\x.log",
                        "../etc/passwd", ""):
                self.assertIsNone(dash.read_page(td, bad), bad)

    def test_read_page_rejects_non_log_file(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "notes.txt"), "w", encoding="utf-8") as fh:
                fh.write("not a log")
            self.assertIsNone(dash.read_page(td, "notes.txt"))

    def test_read_page_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as td, \
             tempfile.TemporaryDirectory() as outside_dir:
            outside = os.path.join(outside_dir, "outside_target.log")
            with open(outside, "w", encoding="utf-8") as fh:
                fh.write("secret")
            link = os.path.join(td, "esc.log")
            try:
                os.symlink(outside, link)
            except OSError:
                self.skipTest("symlinks not available")
            self.assertIsNone(dash.read_page(td, "esc.log"))

    def test_main_binds_loopback_only(self):
        with mock.patch.object(sys, "argv", ["log_dashboard.py"]), \
             mock.patch.object(dash, "ThreadingHTTPServer") as srv, \
             mock.patch.object(dash, "print"):
            srv.return_value.serve_forever.side_effect = KeyboardInterrupt
            dash.main()
        host = srv.call_args[0][0][0]
        self.assertEqual(host, "127.0.0.1")
        with mock.patch.object(sys, "argv",
                               ["log_dashboard.py", "--host", "0.0.0.0"]), \
             mock.patch.object(dash, "print"):
            with self.assertRaises(SystemExit):
                dash.main()

    def test_extract_candidates_v1(self):
        with tempfile.TemporaryDirectory() as td:
            name = os.path.basename(self._candidate_log(td))
            rows, warnings = dash.extract_candidates(td, name)
        self.assertEqual(warnings, 0)
        self.assertEqual(len(rows), 3)
        r = rows[0]
        self.assertEqual((r["rank"], r["symbol"], r["quote"], r["base"]),
                         (1, "MMTUSDT", "USDT", "MMT"))
        self.assertAlmostEqual(r["price"], 0.208)
        self.assertAlmostEqual(r["volume"], 65761812)
        self.assertAlmostEqual(r["chg24"], 0.10)
        self.assertAlmostEqual(r["atr"], 0.015821)
        self.assertAlmostEqual(r["atr_pct"], 7.606)
        self.assertAlmostEqual(r["wr"], 14.29)
        self.assertEqual((r["signals"], r["wins"], r["losses"]), (14, 2, 12))
        self.assertAlmostEqual(r["rr"], 8.0)
        self.assertAlmostEqual(r["ev"], 0.2857)
        self.assertAlmostEqual(r["entry"], 0.208)
        self.assertAlmostEqual(r["tp"], 0.3346)
        self.assertAlmostEqual(r["tp_pct"], 60.87)
        self.assertAlmostEqual(r["trig"], 0.1945)
        self.assertAlmostEqual(r["trig_pct"], -6.49)
        self.assertAlmostEqual(r["sl"], 0.1921)
        self.assertAlmostEqual(r["sl_pct"], -7.64)
        self.assertAlmostEqual(r["qty"], 48.0)
        self.assertAlmostEqual(r["gain"], 6.0768)
        self.assertAlmostEqual(r["loss"], -0.7632)

    def test_extract_candidates_proto_style_with_timestamps(self):
        prefixed = "\n".join(
            "[2026-08-11 00:30:27] " + ln if ln.strip() else ln
            for ln in self.V1_CANDIDATES.splitlines())
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "proto_20260811_003027.log")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(prefixed)
            rows, warnings = dash.extract_candidates(td, "proto_20260811_003027.log")
        self.assertEqual(warnings, 0)
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[2]["wins"])   # proto rows have no W/L breakdown
        self.assertIsNone(rows[2]["losses"])
        self.assertEqual(rows[2]["quote"], "USDC")

    def test_extract_candidates_accepts_non_ascii_symbol(self):
        content = self.V1_CANDIDATES.replace(
            "MMTUSDT", "币安人生USDT", 1).replace(
                "48.000000  MMT", "48.000000  币安人生", 1)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "scan_20260101_000000.log")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            rows, warnings = dash.extract_candidates(td, os.path.basename(path))
        self.assertEqual(warnings, 0)
        self.assertEqual(rows[0]["symbol"], "币安人生USDT")
        self.assertEqual(rows[0]["base"], "币安人生")

    def test_extract_candidates_ignores_content_before_candidates(self):
        prefix = ("  #99  FAKEUSDT  \u00b7  USDT pair\n"
                  "  Current Price                9.999  USDT\n"
                  "  \u250c\u2500 ENTRY    \u2500\u2500\u2500\u2500             9.999  USDT\n"
                  "  \u251c\u2500 TP       \u2500\u2500\u2500\u2500            10.999  USDT\n")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "scan_20260101_000000.log")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(prefix + self.V1_CANDIDATES)
            rows, warnings = dash.extract_candidates(td, "scan_20260101_000000.log")
        self.assertEqual(warnings, 0)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["rank"], 1)

    def test_extract_candidates_empty_and_none(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "empty_20260101_000000.log")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("  CANDIDATES\n  FAIL  No pairs matched\n")
            rows, warnings = dash.extract_candidates(td, "empty_20260101_000000.log")
        self.assertEqual((rows, warnings), ([], 0))
        with tempfile.TemporaryDirectory() as td:
            rows, warnings = dash.extract_candidates(td, "missing.log")
        self.assertEqual((rows, warnings), ([], 0))

    def test_extract_candidates_malformed_counts_warning(self):
        bad_block = ("  \u2550\u2550\u2550\n"
                     "  #4  BADUSDT  \u00b7  USDT pair\n"
                     "  \u2500\u2500\n"
                     "  some unparseable garbage\n")
        marker = "  Binance OCO order:"
        head, sep, tail = self.V1_CANDIDATES.partition(marker)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "scan_20260101_000000.log")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(head + bad_block + sep + tail)
            rows, warnings = dash.extract_candidates(td, "scan_20260101_000000.log")
        self.assertEqual(warnings, 1)
        self.assertEqual(len(rows), 3)

    def test_extract_candidates_rejects_mismatched_quote_token(self):
        poisoned = self.V1_CANDIDATES.replace(
            "0.208000  USDT", '0.208000  \"><svg/onload=alert(1)>', 1)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "scan_20260101_000000.log")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(poisoned)
            rows, warnings = dash.extract_candidates(td, os.path.basename(path))
        self.assertEqual(warnings, 1)
        self.assertEqual([row["symbol"] for row in rows], ["JUPUSDT", "XYZUSDC"])

    def test_results_api_sort_filter_paginate(self):
        with tempfile.TemporaryDirectory() as td:
            self._candidate_log(td)
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                dash.make_handler(td, self.REQUEST_TOKEN))
            httpd.daemon_threads = True
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                def get(qs):
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/results?{qs}") as r:
                        return json.load(r)
                d = get("name=scan_20260101_000000.log")
                self.assertEqual(d["total"], 3)
                self.assertEqual(d["warnings"], 0)
                self.assertEqual(d["sort"], "rank")
                self.assertEqual(d["direction"], "asc")
                self.assertEqual([r["symbol"] for r in d["rows"]],
                                 ["MMTUSDT", "JUPUSDT", "XYZUSDC"])
                d = get("name=scan_20260101_000000.log&quote=USDC")
                self.assertEqual(d["total"], 1)
                self.assertEqual(d["rows"][0]["symbol"], "XYZUSDC")
                d = get("name=scan_20260101_000000.log&quote=USDT")
                self.assertEqual(d["total"], 2)
                d = get("name=scan_20260101_000000.log&sort=price&direction=desc")
                self.assertEqual(d["rows"][0]["symbol"], "XYZUSDC")
                d = get("name=scan_20260101_000000.log&sort=wr&direction=desc")
                self.assertEqual(d["rows"][0]["symbol"], "MMTUSDT")
                d = get("name=scan_20260101_000000.log&sort=pair&direction=asc")
                self.assertEqual([row["symbol"] for row in d["rows"]],
                                 ["JUPUSDT", "MMTUSDT", "XYZUSDC"])
                d = get("name=scan_20260101_000000.log&sort=pair&direction=desc")
                self.assertEqual([row["symbol"] for row in d["rows"]],
                                 ["XYZUSDC", "MMTUSDT", "JUPUSDT"])
                d = get("name=scan_20260101_000000.log&page=1&page_size=2")
                self.assertEqual(len(d["rows"]), 2)
                self.assertEqual(d["pages"], 2)
                for bad in ("name=scan_20260101_000000.log&sort=bogus",
                            "name=scan_20260101_000000.log&quote=ETH",
                            "name=scan_20260101_000000.log&direction=sideways",
                            "name=scan_20260101_000000.log&page=abc",
                            "name=scan_20260101_000000.log&page_size=xyz"):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/results?{bad}")
                    self.assertEqual(ctx.exception.code, 400, bad)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/results?name=ghost.log")
                self.assertEqual(ctx.exception.code, 404)
            finally:
                httpd.shutdown()
                thread.join()
                httpd.server_close()

    def test_classify_line(self):
        cases = {
            "\x1b[96m\x1b[1m─── STEP 1 / 3 · EXCHANGE INFO FILTER ───\x1b[0m": "step",
            "\x1b[1m════════════════════════════════════\x1b[0m": "rule",
            "\x1b[96m\x1b[1m──── CANDIDATES ────\x1b[0m": "cand",
            "\x1b[92m\x1b[1m[2026-08-11 00:30:26] PASS\x1b[0m  BTCUSDT  PASS": "pass",
            "\x1b[2m[2026-08-11 00:30:26] SKIP\x1b[0m  NEOUSDT  LOW_VOLUME": "skip",
            "[2026-08-11 00:30:27] INFO  Fetching https://api.binance.com": "info",
            "[2026-08-11 00:30:27] DB    Run #1 finalised": "db",
            "\x1b[91m\x1b[1m[2026-08-11 00:25:49] FAIL\x1b[0m  No pairs matched": "fail",
            "[2026-08-11 00:25:49] ERROR  boom": "error",
            "PAIR  [  1/3]  BTCUSDT  price=64,000": "pair",
            "└─ \x1b[1mVERDICT\x1b[0m\x1b[91m REJECTED\x1b[0m  ·  ATR_TOO_FLAT": "verdict",
            "└─ \x1b[1mVERDICT\x1b[0m\x1b[92m ACCEPTED\x1b[0m": "verdict-pass",
            "├─ Candle fetch    GET /klines": "tree",
            "  Budget        : $10.00 USD": "plain",
            "⚠ Backtest ≠ live performance": "warn",
        }
        for line, expected in cases.items():
            self.assertEqual(dash.classify_line(line), expected, line)

    def test_dashboard_has_two_palettes(self):
        self.assertIn('option value="classic"', dash.PAGE_HTML)
        self.assertIn('option value="ocean"', dash.PAGE_HTML)
        self.assertNotIn('option value="sunrise"', dash.PAGE_HTML)
        self.assertIn('semanticLineHtml(ln.t, ln.c)', dash.PAGE_HTML)
        self.assertIn('function parseLogName(name)', dash.PAGE_HTML)
        self.assertIn('$("nb-transcript").innerHTML = ""', dash.PAGE_HTML)
        self.assertNotIn("/docs", dash.PAGE_HTML)

    def test_quoted_scanner_path_is_unwrapped(self):
        fake_job = mock.Mock(id="job-1")
        with mock.patch.object(dash, "start_job", return_value=fake_job) as start:
            result = dash.run_notebook_command(
                '/scan --log-file "C:\\Scan Logs\\run.log"')
        self.assertEqual(result, {"type": "job", "job_id": "job-1"})
        script, args, env = start.call_args.args
        self.assertEqual(script, "binance_scanner_v1.py")
        self.assertEqual(args, ["--log-file", "C:\\Scan Logs\\run.log"])
        self.assertEqual(env["SCANNER_LOGDIR"], os.path.abspath("logs"))

    def test_notebook_log_commands_use_dashboard_logdir(self):
        with tempfile.TemporaryDirectory() as td:
            self._dir_with_logs(td, 1)
            logs = dash.run_notebook_command("/logs", td)
            status = dash.run_notebook_command("/status", td)
        self.assertIn("a_20260101_000000.log", logs["content"])
        self.assertIn("**Log files:** 1", status["content"])

    def test_notebook_scans_inherit_dashboard_logdir(self):
        fake_job = mock.Mock(id="job-1")
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(dash, "start_job", return_value=fake_job) as start:
            dash.run_notebook_command("/scan --max-scan 3", td)
            scan_call = start.call_args.args
            dash.run_notebook_command("/proto", td)
            proto_call = start.call_args.args
        self.assertEqual(scan_call[2], {"SCANNER_LOGDIR": os.path.abspath(td)})
        self.assertEqual(proto_call[2], {"SCANNER_LOGDIR": os.path.abspath(td)})

    def test_start_job_rolls_back_failed_start(self):
        with mock.patch.object(dash.NotebookJob, "start", side_effect=OSError("boom")):
            with self.assertRaisesRegex(RuntimeError, "cannot start command"):
                dash.start_job("missing.py", [])
        with dash.JOBS_LOCK:
            self.assertEqual(dash.JOBS, {})

    def test_start_job_admits_only_one_concurrent_caller(self):
        release = threading.Event()
        started = threading.Event()

        def blocking_start(job):
            started.set()
            release.wait(5)

        outcomes = []
        with mock.patch.object(dash.NotebookJob, "start", blocking_start):
            first = threading.Thread(
                target=lambda: outcomes.append(dash.start_job("one.py", [])))
            first.start()
            self.assertTrue(started.wait(2))
            with self.assertRaisesRegex(RuntimeError, "still running"):
                dash.start_job("two.py", [])
            release.set()
            first.join(5)
        self.assertFalse(first.is_alive())
        self.assertEqual(len(outcomes), 1)

    def test_job_output_and_completed_history_are_bounded(self):
        with mock.patch.object(dash, "MAX_JOB_LINES", 3):
            job = dash.NotebookJob("bounded", "x.py", [])
            for line in ("one", "two", "three", "four"):
                job._append_line(line)
            snapshot = job.snapshot(0)
        self.assertTrue(snapshot["truncated"])
        self.assertEqual([line["t"] for line in snapshot["lines"]],
                         ["two", "three", "four"])
        self.assertEqual(snapshot["after"], 4)

        def finish_immediately(job):
            with job.lock:
                job.finished = True
                job.exit_code = 0

        with mock.patch.object(dash.NotebookJob, "start", finish_immediately):
            for _ in range(dash.MAX_COMPLETED_JOBS + 3):
                dash.start_job("done.py", [])
        with dash.JOBS_LOCK:
            self.assertLessEqual(len(dash.JOBS), dash.MAX_COMPLETED_JOBS)

    def test_run_api_rejects_untrusted_and_malformed_requests(self):
        with tempfile.TemporaryDirectory() as td:
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                dash.make_handler(td, self.REQUEST_TOKEN))
            httpd.daemon_threads = True
            port = httpd.server_address[1]
            base = f"http://127.0.0.1:{port}"
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()

            def request(path="/api/run", body=b'{"command":"/status"}',
                        token=self.REQUEST_TOKEN, content_type="application/json",
                        origin=None):
                headers = {"Content-Type": content_type}
                if token is not None:
                    headers["X-Scanner-Token"] = token
                if origin is not None:
                    headers["Origin"] = origin
                return urllib.request.urlopen(urllib.request.Request(
                    base + path, data=body, headers=headers))

            try:
                with urllib.request.urlopen(base + "/") as response:
                    html = response.read().decode("utf-8")
                self.assertIn(self.REQUEST_TOKEN, html)
                self.assertNotIn("__SCANNER_TOKEN__", html)

                for kwargs, status in (
                    ({"token": None}, 403),
                    ({"token": "wrong"}, 403),
                    ({"origin": "https://example.com"}, 403),
                    ({"content_type": "text/plain"}, 415),
                    ({"body": b"[]"}, 400),
                    ({"body": b'{"command":42}'}, 400),
                    ({"body": b"x" * (dash.MAX_REQUEST_BODY + 1)}, 413),
                ):
                    with self.subTest(kwargs=kwargs):
                        with self.assertRaises(urllib.error.HTTPError) as ctx:
                            request(**kwargs)
                        self.assertEqual(ctx.exception.code, status)

                for after in ("abc", "-1"):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(base + "/api/run?job=x&after=" + after)
                    self.assertEqual(ctx.exception.code, 400)
            finally:
                httpd.shutdown()
                thread.join()
                httpd.server_close()

    def test_http_api_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            self._dir_with_logs(td)
            with open(os.path.join(td, "notes.txt"), "w", encoding="utf-8") as fh:
                fh.write("not a log")
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                dash.make_handler(td, self.REQUEST_TOKEN))
            httpd.daemon_threads = True
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/logs") as r:
                    data = json.load(r)
                self.assertEqual(len(data["logs"]), 1)
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/log"
                        f"?name=a_20260101_000000.log&page=3&page_size=10") as r:
                    data = json.load(r)
                self.assertEqual(data["total"], 25)
                self.assertEqual(data["pages"], 3)
                self.assertEqual(len(data["lines"]), 5)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/log?name=..%2Fx.log")
                self.assertEqual(ctx.exception.code, 404)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/log?name=notes.txt")
                self.assertEqual(ctx.exception.code, 404)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/log?name=s.log&page=abc")
                self.assertEqual(ctx.exception.code, 400)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/log?name=s.log&page_size=abc")
                self.assertEqual(ctx.exception.code, 400)
                with urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/run",
                        data=b'{"command":"/status"}',
                        headers={"Content-Type": "application/json",
                                 "X-Scanner-Token": self.REQUEST_TOKEN})) as r:
                    data = json.load(r)
                self.assertEqual(data["type"], "markdown")
                with urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/run",
                        data=b'{"command":"/bogus"}',
                        headers={"Content-Type": "application/json",
                                 "X-Scanner-Token": self.REQUEST_TOKEN})) as r:
                    data = json.load(r)
                self.assertIn("unknown command", data["error"])
            finally:
                httpd.shutdown()
                thread.join()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
