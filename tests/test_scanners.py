"""Deterministic regression tests for the Binance scanners (no live network).

All Binance REST responses are mocked. Run with:
    python -m unittest discover -s tests -v
"""

import io
import os
import sys
import tempfile
import unittest
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
if __name__ == "__main__":
    unittest.main()
