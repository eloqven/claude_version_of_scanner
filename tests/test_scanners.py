"""Deterministic regression tests for the Binance scanners (no live network).

All Binance REST responses are mocked. Run with:
    python -m unittest discover -s tests -v
"""

import io
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import binance_scanner_proto as proto
import binance_scanner_v1 as v1


def _symbol(symbol, base, quote, status="TRADING", spot=True,
            min_notional="1.0", step="0.01", tick="0.0001"):
    return {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": quote,
        "status": status,
        "isSpotTradingAllowed": spot,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": tick},
            {"filterType": "LOT_SIZE", "stepSize": step},
            {"filterType": "MIN_NOTIONAL", "minNotional": min_notional},
        ],
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

    def _pair(self, tick="0.5", step="0.001", min_qty="0", min_notional="5"):
        return {
            "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "min_val": Decimal(min_notional), "min_qty": Decimal(min_qty),
            "step": Decimal(step), "tick": Decimal(tick),
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
        real_out = sys.stdout
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
                sys.stdout = real_out
                proto._LOG_ACTIVE = False
                tee._fh.close()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("[", text)
        self.assertRegex(text, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
        self.assertIn("hello log", text)
        self.assertIn("line two", text)


class TestDefaultLogPath(unittest.TestCase):
    """V1 - default log path is timestamped under the logs dir."""

    def test_default_log_path_timestamped(self):
        with tempfile.TemporaryDirectory() as td:
            path = v1.default_log_path("v1", td)
            self.assertTrue(os.path.basename(path).startswith("v1_"))
            self.assertRegex(os.path.basename(path),
                             r"^v1_\d{8}_\d{6}\.log$")


if __name__ == "__main__":
    unittest.main()
