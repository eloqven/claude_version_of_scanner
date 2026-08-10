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


if __name__ == "__main__":
    unittest.main()
